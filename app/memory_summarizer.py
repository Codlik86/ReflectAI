# app/memory_summarizer.py
from __future__ import annotations
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql
from app.db.core import async_session

# Qdrant: сохраняем/чистим саммари как векторные документы
from app.rag_summaries import upsert_summary_point, delete_user_summaries

# LLM-адаптер (OpenAI-совместимый)
from app.llm_adapter import complete_chat  # complete_chat(messages=[...], ...)


# === Вспомогательные утилиты ===

def _safe_print(*args):
    try:
        print(*args)
    except Exception:
        pass


def _utc_day_bounds(day_utc: datetime) -> Tuple[datetime, datetime]:
    """[start, end) для конкретного дня в UTC."""
    start = datetime(day_utc.year, day_utc.month, day_utc.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


def _utc_week_bounds(week_start_utc: datetime) -> Tuple[datetime, datetime]:
    """[start, end) для недели в UTC от переданного понедельника (или иного дня как «старт недели»)."""
    start = datetime(week_start_utc.year, week_start_utc.month, week_start_utc.day, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return start, end


def _utc_month_bounds(month_start_utc: datetime) -> Tuple[datetime, datetime]:
    """[start, end) для месяца в UTC от переданного первого числа месяца."""
    start = datetime(month_start_utc.year, month_start_utc.month, month_start_utc.day, tzinfo=timezone.utc)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


# === Системный промпт суммаризации ===

SUMMARY_SYSTEM = (
    "Ты — ассистент, который делает краткие, бережные и информативные саммари диалога.\n"
    "Структура: 1) Контекст и темы; 2) Важные выводы; 3) Договорённости/шаги; 4) Триггеры/предупреждения (если есть).\n"
    "Пиши на русском. Не добавляй советов сверх фактов разговора. 150–250 слов."
)


# === Доступ к исходным сообщениям за период ===

async def _fetch_raw_messages(user_id: int, start: datetime, end: datetime) -> List[Dict]:
    """
    Возвращает список {role, text} из bot_messages за [start, end).
    Роли исходные (user/bot).
    """
    async with async_session() as s:
        rows = (await s.execute(sql("""
            SELECT role, text
            FROM bot_messages
            WHERE user_id = :uid
              AND created_at >= :st
              AND created_at <  :en
            ORDER BY created_at ASC
        """), {"uid": user_id, "st": start, "en": end})).mappings().all()
    return [{"role": r["role"], "text": r["text"]} for r in rows]


# === Вызов LLM для суммаризации ===

async def _llm_summarize(messages: List[Dict]) -> Optional[str]:
    """
    Собираем компактную выжимку. Ограничиваем объём источника и токен-лимит ответа.
    """
    if not messages:
        return None

    # Safety cap по количеству сообщений, чтобы не раздуть запрос:
    pairs = messages[-500:]  # берём последние 500 событий периода — более чем достаточно

    # Упрощённо конкатим в текстовый формат (для саммари это ок):
    joined = "\n".join(f"{m['role']}: {m['text']}" for m in pairs if (m.get("text") or "").strip())

    if not joined.strip():
        return None

    msgs = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Сделай саммари разговора за период.\n\n{joined}"},
    ]

    try:
        out = await complete_chat(messages=msgs, temperature=0.2, max_tokens=700)
    except TypeError:
        # На случай старой сигнатуры complete_chat
        out = await complete_chat(msgs, temperature=0.2, max_tokens=700)
    except Exception as e:
        _safe_print(f"[summarizer] LLM error: {e!r}")
        return None

    return (out or "").strip() or None


# === DAILY ===

async def make_daily(user_id: int, day_utc: datetime) -> None:
    """
    Делает дневную выжимку за [00:00, 24:00) UTC указанной даты и пишет:
    - в БД (dialog_summaries kind='daily')
    - в Qdrant (collection=dialog_summaries_v1, kind='daily')
    """
    start, end = _utc_day_bounds(day_utc)

    # privacy guard
    async with async_session() as s:
        pr = (await s.execute(sql("SELECT privacy_level FROM users WHERE id=:uid"), {"uid": user_id})).scalar_one_or_none()
        if (pr or "").lower() == "none":
            _safe_print(f"[summarizer] skip daily: privacy=none user_id={user_id}")
            return

    msgs = await _fetch_raw_messages(user_id, start, end)
    if not msgs:
        _safe_print(f"[summarizer] no msgs for daily user_id={user_id} day={start.date()}")
        return

    text_sum = await _llm_summarize(msgs)
    if not text_sum:
        _safe_print(f"[summarizer] llm returned empty daily summary user_id={user_id} day={start.date()}")
        return

    # БД: upsert
    async with async_session() as s:
        existing = (await s.execute(sql("""
            SELECT id FROM dialog_summaries
            WHERE user_id=:uid AND kind='daily' AND period_start=:st AND period_end=:en
        """), {"uid": user_id, "st": start, "en": end})).scalar_one_or_none()

        if existing:
            await s.execute(sql("""
                UPDATE dialog_summaries
                SET text=:t, source_count=:cnt, updated_at=NOW()
                WHERE id=:id
            """), {"t": text_sum, "cnt": len(msgs), "id": existing})
            ds_id = existing
            await s.commit()
        else:
            ds_id = (await s.execute(sql("""
                INSERT INTO dialog_summaries (user_id, kind, period_start, period_end, text, source_count, created_at, updated_at)
                VALUES (:uid,'daily',:st,:en,:t,:cnt, NOW(), NOW())
                RETURNING id
            """), {"uid": user_id, "st": start, "en": end, "t": text_sum, "cnt": len(msgs)})).scalar_one()
            await s.commit()

    # Qdrant: upsert
    await upsert_summary_point(
        summary_id=ds_id, user_id=user_id, kind="daily",
        text=text_sum, period_start=start, period_end=end
    )
    _safe_print(f"[summarizer] daily saved user_id={user_id} id={ds_id}")


# === WEEKLY (ROLLUP из daily) ===

async def rollup_weekly(user_id: int, week_start_utc: datetime) -> None:
    """
    Делает недельную выжимку за 7 суток [start, end), сначала пытается собрать из дневных саммарей.
    Если дневных нет — нечего сворачивать (неделя пропускается).
    """
    start, end = _utc_week_bounds(week_start_utc)

    async with async_session() as s:
        dailies = (await s.execute(sql("""
            SELECT text
            FROM dialog_summaries
            WHERE user_id=:uid AND kind='daily'
              AND period_start >= :st
              AND period_end   <= :en
            ORDER BY period_start ASC
        """), {"uid": user_id, "st": start, "en": end})).mappings().all()

    if not dailies:
        _safe_print(f"[summarizer] no daily to rollup weekly user_id={user_id} start={start.date()}")
        return

    joined = "\n\n".join(f"- {r['text']}" for r in dailies if (r.get("text") or "").strip())
    if not joined.strip():
        _safe_print(f"[summarizer] empty text after join (weekly) user_id={user_id}")
        return

    msgs = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Сжать недельную сводку (7 дней) из дневных саммарей:\n{joined}"},
    ]
    try:
        out = await complete_chat(messages=msgs, temperature=0.2, max_tokens=800)
    except TypeError:
        out = await complete_chat(msgs, temperature=0.2, max_tokens=800)
    except Exception as e:
        _safe_print(f"[summarizer] LLM error weekly: {e!r}")
        return

    text_sum = (out or "").strip()
    if not text_sum:
        _safe_print(f"[summarizer] llm returned empty weekly summary user_id={user_id}")
        return

    # БД: upsert weekly
    async with async_session() as s:
        existing = (await s.execute(sql("""
            SELECT id FROM dialog_summaries
            WHERE user_id=:uid AND kind='weekly' AND period_start=:st AND period_end=:en
        """), {"uid": user_id, "st": start, "en": end})).scalar_one_or_none()
        if existing:
            await s.execute(sql("""
                UPDATE dialog_summaries
                SET text=:t, source_count=:cnt, updated_at=NOW()
                WHERE id=:id
            """), {"t": text_sum, "cnt": len(dailies), "id": existing})
            ds_id = existing
            await s.commit()
        else:
            ds_id = (await s.execute(sql("""
                INSERT INTO dialog_summaries (user_id, kind, period_start, period_end, text, source_count, created_at, updated_at)
                VALUES (:uid,'weekly',:st,:en,:t,:cnt, NOW(), NOW())
                RETURNING id
            """), {"uid": user_id, "st": start, "en": end, "t": text_sum, "cnt": len(dailies)})).scalar_one()
            await s.commit()

    # Qdrant
    await upsert_summary_point(
        summary_id=ds_id, user_id=user_id, kind="weekly",
        text=text_sum, period_start=start, period_end=end
    )
    _safe_print(f"[summarizer] weekly saved user_id={user_id} id={ds_id}")


# === MONTHLY (ROLLUP из weekly, fallback на daily) ===

async def rollup_monthly(user_id: int, month_start_utc: datetime) -> None:
    """
    Делает месячную выжимку: пытается собрать из weekly; если weekly нет — из daily за месяц.
    В БД пишет kind='monthly' (legacy 'topic' читается, но больше не используется при вставке).
    """
    start, end = _utc_month_bounds(month_start_utc)

    async with async_session() as s:
        weekly = (await s.execute(sql("""
            SELECT text
            FROM dialog_summaries
            WHERE user_id=:uid AND kind='weekly'
              AND period_start >= :st
              AND period_end   <= :en
            ORDER BY period_start ASC
        """), {"uid": user_id, "st": start, "en": end})).mappings().all()

        items = [w["text"] for w in weekly if (w.get("text") or "").strip()]

        if not items:
            # Фолбэк: собираем из daily
            daily = (await s.execute(sql("""
                SELECT text
                FROM dialog_summaries
                WHERE user_id=:uid AND kind='daily'
                  AND period_start >= :st
                  AND period_end   <= :en
                ORDER BY period_start ASC
            """), {"uid": user_id, "st": start, "en": end})).mappings().all()
            items = [d["text"] for d in daily if (d.get("text") or "").strip()]

        # Legacy: если когда-то уже писали kind='topic', мы их учитываем как источник только для LLM,
        # но новые записи будем делать как kind='monthly'.
        if not items:
            legacy = (await s.execute(sql("""
                SELECT text
                FROM dialog_summaries
                WHERE user_id=:uid AND kind='topic'
                  AND period_start >= :st
                  AND period_end   <= :en
                ORDER BY period_start ASC
            """), {"uid": user_id, "st": start, "en": end})).mappings().all()
            items = [t["text"] for t in legacy if (t.get("text") or "").strip()]

    if not items:
        _safe_print(f"[summarizer] nothing to rollup monthly user_id={user_id} month={start.date():%Y-%m}")
        return

    joined = "\n\n".join(f"- {x}" for x in items)
    msgs = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": "Сделай месячную выжимку: ключевые темы, сдвиги, договорённости, риски.\n" + joined},
    ]
    try:
        out = await complete_chat(messages=msgs, temperature=0.2, max_tokens=900)
    except TypeError:
        out = await complete_chat(msgs, temperature=0.2, max_tokens=900)
    except Exception as e:
        _safe_print(f"[summarizer] LLM error monthly: {e!r}")
        return

    text_sum = (out or "").strip()
    if not text_sum:
        _safe_print(f"[summarizer] llm returned empty monthly summary user_id={user_id}")
        return

    # БД: upsert monthly (учитываем legacy 'topic' при поиске существующей записи)
    async with async_session() as s:
        existing = (await s.execute(sql("""
            SELECT id FROM dialog_summaries
            WHERE user_id=:uid AND kind IN ('monthly','topic') AND period_start=:st AND period_end=:en
            ORDER BY CASE kind WHEN 'monthly' THEN 0 ELSE 1 END
            LIMIT 1
        """), {"uid": user_id, "st": start, "en": end})).scalar_one_or_none()

        if existing:
            await s.execute(sql("""
                UPDATE dialog_summaries
                SET kind='monthly', text=:t, source_count=NULL, updated_at=NOW()
                WHERE id=:id
            """), {"t": text_sum, "id": existing})
            ds_id = existing
            await s.commit()
        else:
            ds_id = (await s.execute(sql("""
                INSERT INTO dialog_summaries (user_id, kind, period_start, period_end, text, created_at, updated_at)
                VALUES (:uid,'monthly',:st,:en,:t, NOW(), NOW())
                RETURNING id
            """), {"uid": user_id, "st": start, "en": end, "t": text_sum})).scalar_one()
            await s.commit()

    # Qdrant
    await upsert_summary_point(
        summary_id=ds_id, user_id=user_id, kind="monthly",
        text=text_sum, period_start=start, period_end=end
    )
    _safe_print(f"[summarizer] monthly saved user_id={user_id} id={ds_id}")
