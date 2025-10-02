# app/memory_summarizer.py
from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql
from app.db.core import async_session
from app.rag_summaries import upsert_summary_point, delete_user_summaries

SUMMARY_SYSTEM = (
    "Ты — ассистент, который делает краткие, бережные и информативные саммари диалога.\n"
    "Структура: 1) Контекст и темы; 2) Важные выводы; 3) Договорённости/шаги; 4) Триггеры/предупреждения (если есть).\n"
    "Не пиши советов, только выжимку из фактов разговора. 150–250 слов."
)

# >>> используем готовый адаптер чата
from app.llm_adapter import complete_chat  # ожидается функция complete_chat(messages=[...], ...)

async def _fetch_raw_messages(user_id: int, start: datetime, end: datetime) -> List[Dict]:
    async with async_session() as s:
        rows = (await s.execute(sql("""
            SELECT role, text
            FROM bot_messages
            WHERE user_id = :uid AND created_at >= :st AND created_at < :en
            ORDER BY created_at ASC
        """), {"uid": user_id, "st": start, "en": end})).mappings().all()
    return [{"role": r["role"], "text": r["text"]} for r in rows]

async def _llm_summarize(messages: List[Dict]) -> Optional[str]:
    if not messages:
        return None
    pairs = messages[-400:]  # safety cap
    joined = "\n".join(f"{m['role']}: {m['text']}" for m in pairs)
    msgs = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Сделай саммари разговора:\n\n{joined}"},
    ]
    out = await complete_chat(messages=msgs, temperature=0.2, max_tokens=600)
    return (out or "").strip() or None

async def make_daily(user_id: int, day_utc: datetime) -> None:
    start = datetime(day_utc.year, day_utc.month, day_utc.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    # privacy guard
    async with async_session() as s:
        pr = (await s.execute(sql("SELECT privacy_level FROM users WHERE id=:uid"), {"uid": user_id})).scalar_one_or_none()
        if pr == "none":
            return

    msgs = await _fetch_raw_messages(user_id, start, end)
    if not msgs:
        return
    text_sum = await _llm_summarize(msgs)
    if not text_sum:
        return

    async with async_session() as s:
        # upsert daily
        existing = (await s.execute(sql("""
            SELECT id FROM dialog_summaries
            WHERE user_id=:uid AND kind='daily' AND period_start=:st AND period_end=:en
        """), {"uid": user_id, "st": start, "en": end})).scalar_one_or_none()

        if existing:
            await s.execute(sql("""
                UPDATE dialog_summaries
                SET text=:t, source_count=:cnt, updated_at=now()
                WHERE id=:id
            """), {"t": text_sum, "cnt": len(msgs), "id": existing})
            ds_id = existing
            await s.commit()
        else:
            ds_id = (await s.execute(sql("""
                INSERT INTO dialog_summaries (user_id, kind, period_start, period_end, text, source_count, created_at, updated_at)
                VALUES (:uid,'daily',:st,:en,:t,:cnt, now(), now())
                RETURNING id
            """), {"uid": user_id, "st": start, "en": end, "t": text_sum, "cnt": len(msgs)})).scalar_one()
            await s.commit()

    await upsert_summary_point(
        summary_id=ds_id, user_id=user_id, kind="daily",
        text=text_sum, period_start=start, period_end=end
    )

async def rollup_weekly(user_id: int, week_start_utc: datetime) -> None:
    start = datetime(week_start_utc.year, week_start_utc.month, week_start_utc.day, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    async with async_session() as s:
        dailies = (await s.execute(sql("""
            SELECT text FROM dialog_summaries
            WHERE user_id=:uid AND kind='daily' AND period_start>=:st AND period_end<=:en
            ORDER BY period_start ASC
        """), {"uid": user_id, "st": start, "en": end})).mappings().all()
    if not dailies:
        return
    joined = "\n\n".join(f"- {r['text']}" for r in dailies)
    msgs = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Сжать недельную сводку (7 дней) из дневных саммарей:\n{joined}"},
    ]
    out = await complete_chat(messages=msgs, temperature=0.2, max_tokens=700)
    if not out:
        return
    text_sum = out.strip()

    async with async_session() as s:
        existing = (await s.execute(sql("""
            SELECT id FROM dialog_summaries
            WHERE user_id=:uid AND kind='weekly' AND period_start=:st AND period_end=:en
        """), {"uid": user_id, "st": start, "en": end})).scalar_one_or_none()
        if existing:
            await s.execute(sql("""
                UPDATE dialog_summaries
                SET text=:t, source_count=:cnt, updated_at=now()
                WHERE id=:id
            """), {"t": text_sum, "cnt": len(dailies), "id": existing})
            ds_id = existing
            await s.commit()
        else:
            ds_id = (await s.execute(sql("""
                INSERT INTO dialog_summaries (user_id, kind, period_start, period_end, text, source_count, created_at, updated_at)
                VALUES (:uid,'weekly',:st,:en,:t,:cnt, now(), now())
                RETURNING id
            """), {"uid": user_id, "st": start, "en": end, "t": text_sum, "cnt": len(dailies)})).scalar_one()
            await s.commit()

    await upsert_summary_point(
        summary_id=ds_id, user_id=user_id, kind="weekly",
        text=text_sum, period_start=start, period_end=end
    )

async def rollup_topic_month(user_id: int, month_start_utc: datetime) -> None:
    start = datetime(month_start_utc.year, month_start_utc.month, month_start_utc.day, tzinfo=timezone.utc)
    # конец месяца
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    async with async_session() as s:
        weekly = (await s.execute(sql("""
            SELECT text FROM dialog_summaries
            WHERE user_id=:uid AND kind='weekly' AND period_start>=:st AND period_end<=:en
            ORDER BY period_start ASC
        """), {"uid": user_id, "st": start, "en": end})).mappings().all()
        items = [w["text"] for w in weekly]
        if not items:
            daily = (await s.execute(sql("""
                SELECT text FROM dialog_summaries
                WHERE user_id=:uid AND kind='daily' AND period_start>=:st AND period_end<=:en
                ORDER BY period_start ASC
            """), {"uid": user_id, "st": start, "en": end})).mappings().all()
            items = [d["text"] for d in daily]

    if not items:
        return

    joined = "\n\n".join(f"- {x}" for x in items)
    msgs = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": "Сделай месячную тематическую выжимку: ключевые темы, сдвиги, договорённости, риски.\n" + joined},
    ]
    out = await complete_chat(messages=msgs, temperature=0.2, max_tokens=800)
    if not out:
        return
    text_sum = out.strip()

    async with async_session() as s:
        existing = (await s.execute(sql("""
            SELECT id FROM dialog_summaries
            WHERE user_id=:uid AND kind='topic' AND period_start=:st AND period_end=:en
        """), {"uid": user_id, "st": start, "en": end})).scalar_one_or_none()
        if existing:
            await s.execute(sql("""
                UPDATE dialog_summaries
                SET text=:t, source_count=NULL, updated_at=now()
                WHERE id=:id
            """), {"t": text_sum, "id": existing})
            ds_id = existing
            await s.commit()
        else:
            ds_id = (await s.execute(sql("""
                INSERT INTO dialog_summaries (user_id, kind, period_start, period_end, text, created_at, updated_at)
                VALUES (:uid,'topic',:st,:en,:t, now(), now())
                RETURNING id
            """), {"uid": user_id, "st": start, "en": end, "t": text_sum})).scalar_one()
            await s.commit()

    await upsert_summary_point(
        summary_id=ds_id, user_id=user_id, kind="topic",
        text=text_sum, period_start=start, period_end=end
    )
