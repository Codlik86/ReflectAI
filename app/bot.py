# app/bot.py
from __future__ import annotations

import os
import hashlib
from typing import Dict, List, Optional

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
 ReplyKeyboardRemove)
# алиасы для клавиатуры (используются в нескольких местах, в т.ч. deep-link)
from aiogram.types import InlineKeyboardMarkup as _IKM, InlineKeyboardButton as _IKB

# ===== Модули продукта =====
from app.meditations import get_categories, get_items, get_item
from app.memory import save_user_message, save_bot_message, get_recent_messages
from app.exercises import TOPICS, EXERCISES
from app.prompts import SYSTEM_PROMPT, STYLE_SUFFIXES, LENGTH_HINTS
try:
    # В новом prompts.py REFLECTIVE_SUFFIX не обязателен — пусть остаётся дефолт.
    from app.prompts import REFLECTIVE_SUFFIX  # если добавишь в будущем — подхватится
except Exception:
    REFLECTIVE_SUFFIX = "\n\n(Режим рефлексии: мягко замедляй темп, задавай вопросы, помогающие осмыслению.)"

# LLM
try:
    from app.llm_adapter import chat_with_style
except Exception:
    chat_with_style = None  # при отладке не падаем

# RAG (опционально)
try:
    from app.rag_qdrant import search as rag_search
except Exception:
    rag_search = None

# RAG summaries (долгая память)
from app.rag_summaries import search_summaries, delete_user_summaries

# БД (async)
from sqlalchemy import text
from app.db.core import async_session

from sqlalchemy import select
from app.db.core import get_session
from app.billing.yookassa_client import create_payment_link
from app.billing.service import start_trial_for_user, check_access, is_trial_active
from app.billing.service import disable_auto_renew, cancel_subscription_now, get_active_subscription_row

from zoneinfo import ZoneInfo

from collections import deque

router = Router()

# ========== AUTO-LOGGING В БД (bot_messages) ==========
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

async def _log_message_by_tg(tg_id: int, role: str, text_: str) -> None:
    """
    Лог в bot_messages c учётом users.id.
    Режем текст до 4000 символов. Роль жёстко приводим к {'user','bot'}.
    Когда приватность = 'none' — пишем только в эфемерный буфер (без БД).
    """
    try:
        mode = (await _db_get_privacy(int(tg_id)) or "insights").lower()

        uid = await _ensure_user_id(int(tg_id))
        safe = (text_ or "")[:4000]
        if not safe:
            return

        r = (role or "").lower()
        role_norm = "user" if r == "user" else "bot"

        if mode == "none":
            # приватность отключена — сохраняем только в оперативный буфер
            _buf_push(int(tg_id), role_norm, safe)
            return

        # приватность включена — пишем в БД
        async with async_session() as s:
            await s.execute(
                text("""
                    INSERT INTO bot_messages (user_id, role, text, created_at)
                    VALUES (:u, :r, :t, CURRENT_TIMESTAMP)
                """),
                {"u": int(uid), "r": role_norm, "t": safe},
            )
            await s.commit()

        # и дублируем в короткий буфер (не обяз., но удобно)
        _buf_push(int(tg_id), role_norm, safe)

    except Exception as e:
        print("[log-db] error:", repr(e))

class LogIncomingMiddleware(BaseMiddleware):
    """
    Пишем ВСЕ входящие сообщения/колбэки в bot_messages (role='user'),
    но только если хранение не выключено (/privacy).
    """
    async def __call__(self, handler, event, data):
        try:
            tg_id = getattr(getattr(event, "from_user", None), "id", None)
            if tg_id:
                if isinstance(event, Message):
                    txt = event.text or event.caption
                    if txt:
                        await _log_message_by_tg(int(tg_id), "user", txt)
                elif isinstance(event, CallbackQuery):
                    if event.data:
                        await _log_message_by_tg(int(tg_id), "user", f"[cb] {event.data}")
        except Exception as e:
            print("[log-mw] error:", repr(e))
        return await handler(event, data)

async def send_and_log(
    message: Message,
    text_: str,
    **kwargs
):
    """
    Отправка ответа пользователю + запись в bot_messages как role='bot'.
    """
    # по умолчанию выключим предпросмотр ссылок (можно переопределить в kwargs)
    kwargs.setdefault("disable_web_page_preview", True)

    sent = await message.answer(text_, **kwargs)
    try:
        await _log_message_by_tg(message.from_user.id, "bot", text_)
        # или можно вызвать прямой логгер:
        # await _db_log_bot_message(message.from_user.id, text_)
    except Exception as e:
        print("[send-log] error:", repr(e))
    return sent

# обрабатываем ТОЛЬКО deep-link вида: /start paid_ok | paid_canceled | paid_fail
@router.message(F.text.regexp(r"^/start\s+paid_(ok|canceled|fail)$"))
async def on_start_payment_deeplink(m: Message):
    payload = (m.text or "").split(maxsplit=1)[1].strip().lower()

    if payload == "paid_ok":
        await m.answer(
            "Оплата прошла ✅\nДоступ активирован. Можно продолжать — «Поговорить», «Разобраться» или «Медитации».",
            reply_markup=kb_main_menu(),
        )
        return

    # paid_canceled / paid_fail
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:open")],
        [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
    ])
    await m.answer(
        "Похоже, оплата не завершилась или была отменена.\nМожно попробовать ещё раз — это безопасно и займёт минуту.",
        reply_markup=kb,
    )

# ===== Универсальный paywall в рантайме ======================================
def _require_access_msg(_: Message) -> bool:
    """
    LEGACY: раньше показывали пейволл из памяти процесса.
    Теперь доступ проверяется через _enforce_access_or_paywall(...) с БД.
    Отключаем этот хук, чтобы он не блокировал обработчики.
    """
    return False
    
# --- async DB helpers (privacy, users, history) -----------------
async def _ensure_user_id(tg_id: int) -> int:
    """Вернёт users.id по tg_id, создаст пользователя при отсутствии."""
    async with async_session() as s:
        r = await s.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        uid = r.scalar()
        if uid is None:
            r = await s.execute(
                text("""
                    INSERT INTO users (tg_id, privacy_level, style_profile, created_at)
            VALUES (:tg, 'ask', 'default', NOW())
            RETURNING id
                """),
                {"tg": int(tg_id)},
            )
            uid = r.scalar_one()
            await s.commit()
        return int(uid)
    
# --- История диалога из БД (для устойчивой памяти) ---
from sqlalchemy import text as _t

async def _load_history_from_db(tg_id: int, *, limit: int = 120, hours: int = 24*30) -> list[dict]:
    """
    Возвращает историю диалога пользователя за последние `hours` часов,
    в формате OpenAI messages: [{"role":"user|assistant","content": "..."}]
    Порядок: старые -> новые.

    Поведение:
    - Если приватность = "none" — возвращаем историю из оперативного буфера процесса (in-memory).
    - Если приватность включена — берём из БД и в конце домешиваем до 10 последних из буфера,
      чтобы нивелировать возможную миллисекундную задержку записи в БД.
    """
    uid = await _ensure_user_id(tg_id)

    # 0) режим приватности
    try:
        mode = (await _db_get_privacy(int(tg_id)) or "insights").lower()
    except Exception:
        mode = "insights"

    # 1) приватность выключена — только оперативный буфер (без обращения к БД)
    if mode == "none":
        # используем уже импортированный get_recent_messages(chat_id, limit)
        buf = get_recent_messages(int(tg_id), limit=min(limit, 120)) or []
        out: list[dict] = []
        for r in buf:
            role = "assistant" if (r.get("role") == "bot") else "user"
            out.append({"role": role, "content": r.get("text") or ""})
        return out

    # 2) приватность включена — читаем историю из БД
    async with async_session() as s:
        rows = (await s.execute(
            _t("""
                SELECT role, text
                FROM bot_messages
                WHERE user_id = :uid
                  AND created_at >= NOW() - (:hours::text || ' hours')::interval
                ORDER BY id ASC
                LIMIT :lim
            """),
            {"uid": int(uid), "hours": int(hours), "lim": int(limit)}
        )).mappings().all()

    msgs: list[dict] = []
    for r in rows:
        role = "assistant" if (r["role"] or "").lower() == "bot" else "user"
        msgs.append({"role": role, "content": r["text"] or ""})

    # 3) опционально: домешиваем до 10 свежих из оперативного буфера (если вдруг БД отстала на миллисекунды)
    try:
        tail_raw = get_recent_messages(int(tg_id), limit=10) or []
        if tail_raw:
            seen = {(m["role"], m["content"]) for m in msgs}
            for r in tail_raw:
                role = "assistant" if (r.get("role") == "bot") else "user"
                content = r.get("text") or ""
                key = (role, content)
                if key not in seen:
                    msgs.append({"role": role, "content": content})
                    seen.add(key)
    except Exception:
        # если буфер недоступен — просто возвращаем БД-историю
        pass

    return msgs

async def _db_get_privacy(tg_id: int) -> str:
    async with async_session() as s:
        r = await s.execute(text("SELECT privacy_level FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        val = r.scalar()
    return (val or "insights")

async def _db_set_privacy(tg_id: int, mode: str) -> None:
    async with async_session() as s:
        await s.execute(text("UPDATE users SET privacy_level = :m WHERE tg_id = :tg"),
                        {"m": mode, "tg": int(tg_id)})
        await s.commit()

async def _purge_user_history(tg_id: int) -> int:
    """
    Удаляет историю в БД (если пользователь есть) и всегда очищает эфемерный буфер.
    Возвращает число удалённых записей из БД.
    """
    deleted = 0
    try:
        async with async_session() as s:
            r = await s.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
            uid = r.scalar()
            if uid:
                res = await s.execute(text("DELETE FROM bot_messages WHERE user_id = :u"), {"u": int(uid)})
                await s.commit()
                try:
                    deleted = int(getattr(res, "rowcount", 0) or 0)
                except Exception:
                    deleted = 0
    except Exception:
        deleted = 0
    # всегда чистим оперативный буфер
    RECENT_BUFFER.pop(int(tg_id), None)
    return deleted
    
# --- Memory Q hook: "что мы говорили X часов/дней/недель назад?" ----------
import re
from sqlalchemy import text as _pg

_TIME_NUM = re.compile(r"(\d+)")

def _pick_window(txt: str) -> tuple[int, int, int, int]:
    """
    Возвращает (minutes, hours, days, weeks). По умолчанию 10 минут.
    Поддерживает: 'мин', 'мину', 'час', 'дн', 'недел', 'недавн'.
    """
    t = (txt or "").lower()
    mins = hours = days = weeks = 0
    if "недавн" in t:           # «недавно»
        hours = 3
    elif "мин" in t or "мину" in t:
        m = _TIME_NUM.search(t); mins = int(m.group(1)) if m else 10
    elif "час" in t:
        m = _TIME_NUM.search(t); hours = int(m.group(1)) if m else 3
    elif "дн" in t:
        m = _TIME_NUM.search(t); days = int(m.group(1)) if m else 1
    elif "недел" in t:
        m = _TIME_NUM.search(t); weeks = int(m.group(1)) if m else 1
    else:
        mins = 10
    return mins, hours, days, weeks

def _looks_like_memory_question(txt: str) -> bool:
    t = (txt or "").lower()
    # базовые ключи
    keys = [
        "помнишь", "вспомни", "что мы говорили", "о чем мы говорили", "о чём мы говорили",
        "что я говорил", "что я писал", "что я спрашивал",
        "что было раньше", "мы обсуждали",
        "мин назад", "час назад", "день назад", "неделю назад", "вчера", "сегодня",
        "прошлый раз", "последний раз", "недавно",
    ]
    # синонимы «говорили»
    syn = ["разговаривали", "общались", "переписывались", "болтали"]
    if any(k in t for k in keys):
        return True
    if ("о чем" in t or "о чём" in t) and any(s in t for s in syn):
        return True
    if "не помнишь" in t and ("о чем" in t or "о чём" in t or "что было" in t):
        return True
    return False

async def _maybe_answer_memory_question(m: Message, user_text: str) -> bool:
    """Если пользователь спрашивает «что мы говорили X назад/недавно», отдаём пересказ без LLM."""
    if not _looks_like_memory_question(user_text):
        return False

    uid = await _ensure_user_id(m.from_user.id)
    mins, h, d, w = _pick_window(user_text)
    total_minutes = mins + h*60 + d*24*60 + w*7*24*60
    if total_minutes <= 0:
        total_minutes = 10
    interval_txt = f"{total_minutes} minutes"

    async with async_session() as s:
        rows = (await s.execute(_pg("""
            SELECT role, text, created_at
            FROM bot_messages
            WHERE user_id = :uid
              AND created_at >= NOW() - (:ival::text)::interval
            ORDER BY id ASC
            LIMIT 120
        """), {"uid": int(uid), "ival": interval_txt})).mappings().all()

    if not rows:
        await send_and_log(
            m,
            "За этот промежуток ничего не вижу в истории. Подскажи тему — подхвачу.",
            reply_markup=kb_main_menu(),
        )
        return True

    # Сжимаем «диалог» в компактный пересказ
    def _short(s: str, n: int = 220) -> str:
        s = (s or "").strip().replace("\n", " ")
        return s if len(s) <= n else s[:n - 1] + "…"

    parts = []
    for r in rows[-14:]:  # берём последние 14 реплик, чтобы не раздувать
        who = "ты" if (r["role"] or "").lower() == "user" else "я"
        when = _fmt_dt(r["created_at"])
        parts.append(f"{when} — {who}: {_short(r['text'])}")

    header = "Коротко, что было в недавнем разговоре:\n"
    body = "\n".join(parts)
    tail = "\n\nПродолжим с этого места или поменяем тему?"

    await send_and_log(m, header + body + tail, reply_markup=kb_main_menu())
    return True
    
# --- Summaries helpers (fetch texts by ids, purge all for user) ---
from sqlalchemy import text as _sql_text

async def _fetch_summary_texts_by_ids(ids: List[int]) -> List[dict]:
    """Возвращает тексты саммарей в порядке ids."""
    if not ids:
        return []
    async with async_session() as s:
        rows = (await s.execute(_sql_text("""
            SELECT id, kind, period_start, period_end, text
            FROM dialog_summaries
            WHERE id = ANY(:ids)
        """), {"ids": ids})).mappings().all()
    by_id = {r["id"]: r for r in rows}
    out: List[dict] = []
    for i in ids:
        r = by_id.get(i)
        if not r:
            continue
        out.append({
            "id": r["id"],
            "kind": r["kind"],
            "period": f"{_fmt_dt(r['period_start'])} — {_fmt_dt(r['period_end'])}",
            "text": r["text"],
        })
    return out

async def _purge_user_summaries_all(tg_id: int) -> int:
    """Удаляет ВСЕ саммари пользователя (БД + Qdrant). Возвращает кол-во удалённых записей из БД."""
    # получаем users.id
    async with async_session() as s:
        r = await s.execute(_sql_text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        uid = r.scalar()
        if not uid:
            return 0
        # сначала удаляем векторы (не критично, если упадёт)
        try:
            await delete_user_summaries(int(uid))
        except Exception:
            pass
        # затем чистим БД
        res = await s.execute(_sql_text("DELETE FROM dialog_summaries WHERE user_id = :uid"), {"uid": int(uid)})
        await s.commit()
        try:
            return int(getattr(res, "rowcount", 0) or 0)
        except Exception:
            return 0
# -----------------------------------------------------------------

# ===== Онбординг: ссылки и картинки =====
POLICY_URL = os.getenv("POLICY_URL", "").strip()
TERMS_URL  = os.getenv("TERMS_URL", "").strip()

DEFAULT_ONB_IMAGES = {
    "cover":       os.getenv("ONB_IMG_COVER", "https://file.garden/aML3M6Sqrg21TaIT/kind-creature-min.jpg"),
    "talk":        os.getenv("ONB_IMG_TALK", "https://file.garden/aML3M6Sqrg21TaIT/warm-conversation-min.jpg"),
    "work":        os.getenv("ONB_IMG_WORK", "https://file.garden/aML3M6Sqrg21TaIT/_practices-min.jpg"),
    "meditations": os.getenv("ONB_IMG_MEDIT", "https://file.garden/aML3M6Sqrg21TaIT/meditation%20(1)-min.jpg"),
}

def get_onb_image(key: str) -> str:
    return DEFAULT_ONB_IMAGES.get(key, "") or ""

# ===== Глобальные состояния чата (в памяти процесса) =====
CHAT_MODE: Dict[int, str] = {}        # chat_id -> "talk" | "work" | "reflection"
USER_TONE: Dict[int, str] = {}        # chat_id -> "default" | "friend" | "therapist" | "18plus"

# ===== Эфемерный буфер диалога, когда приватность = none =====
# chat_id -> deque([{"role": "user|assistant", "content": "..."}])
RECENT_BUFFER: Dict[int, deque] = {}
BUFFER_MAX = 120  # сколько сообщений держим в памяти процесса

def _buf_push(chat_id: int, role: str, text_: str) -> None:
    if not chat_id or not text_:
        return
    q = RECENT_BUFFER.get(chat_id)
    if q is None:
        q = deque(maxlen=BUFFER_MAX)
        RECENT_BUFFER[chat_id] = q
    # нормализуем роль под OpenAI-схему
    role_norm = "assistant" if (role or "").lower() == "bot" else "user"
    q.append({"role": role_norm, "content": (text_ or "").strip()})

def _buf_get(chat_id: int, limit: int = 90) -> List[dict]:
    q = RECENT_BUFFER.get(chat_id)
    if not q:
        return []
    # возвращаем старые -> новые
    items = list(q)[-int(limit):]
    return items


# --- paywall helpers ---
async def _get_user_by_tg(session, tg_id: int):
    from app.db.models import User
    q = await session.execute(select(User).where(User.tg_id == tg_id))
    return q.scalar_one_or_none()

def _kb_paywall(show_trial: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if show_trial:
        rows.append([InlineKeyboardButton(text="Начать пробный период ⭐", callback_data="trial:start")])
    rows.append([InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _enforce_access_or_paywall(msg_or_call, session, user_id: int) -> bool:
    """True — доступ есть; False — показан пейволл и нужно прекратить обработку."""
    if await check_access(session, user_id):
        return True
    trial_active = await is_trial_active(session, user_id)
    show_trial = not trial_active
    text_ = (
        "Доступ к разделу открыт по подписке.\n"
        "Можно начать 5-дневный пробный период бесплатно, затем — по выбранному плану."
    )
    if isinstance(msg_or_call, Message):
        await msg_or_call.answer(text_, reply_markup=_kb_paywall(show_trial))
    else:
        await msg_or_call.message.answer(text_, reply_markup=_kb_paywall(show_trial))
    return False

# --- pay status helpers ---
async def _access_status_text(session, user_id: int) -> str | None:
    """Возвращает человекочитаемый статус доступа или None, если доступа нет."""
    # подписка?
    try:
        from app.db.models import User
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    except Exception:
        u = None
    if u and (getattr(u, "subscription_status", None) or "") == "active":
        return "Подписка активна ✅\nДоступ ко всем функциям открыт."

    # триал?
    if await is_trial_active(session, user_id):
        until = getattr(u, "trial_expires_at", None)
        tail = f" до {_fmt_dt(until)}" if until else ""
        return f"Пробный период активен{tail} ✅\nДоступ ко всем функциям открыт."
    return None

# --- локализация времени для сообщений ---
from zoneinfo import ZoneInfo
import os

_TZ = ZoneInfo(os.getenv("BOT_TZ", "Europe/Moscow"))

def _fmt_dt(dt) -> str:
    try:
        # если пришёл naive-datetime, считаем, что это UTC
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(_TZ).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return str(dt)
    
# === Length hint picker + "one-question" post-filter =======================

def _pick_len_hint(user_text: str, mode: str) -> str:
    """
    Возвращает ключ из LENGTH_HINTS: 'micro' | 'short' | 'medium' | 'deep'
    Логика:
      — 'deep' при явном запросе «подробно/план/структура/инструкция»;
      — по длине текста: <=50 символов -> micro; <=220 -> short; иначе -> medium;
      — в режиме reflection избегаем 'deep', если нет явной просьбы.
    """
    t = (user_text or "").lower()
    deep_keywords = ("подроб", "деталь", "развернут", "план", "структур", "пошаг", "инструкц")

    if any(k in t for k in deep_keywords):
        return "deep"

    n = len(t.strip())
    if n <= 50:
        return "micro"
    if n <= 220:
        return "short"

    # длинное сообщение — средний разбор по умолчанию
    if mode == "reflection":
        return "medium"
    return "medium"


def _enforce_single_question(text: str) -> str:
    """
    Оставляет максимум один вопросительный знак (последний по тексту).
    Остальные '?' превращаются в точки. Схлопывает '??' -> '?'.
    """
    if not text:
        return text

    # схлопнуть повторы
    while "??" in text:
        text = text.replace("??", "?")

    last_q = text.rfind("?")
    if last_q == -1:
        return text

    # заменить все предыдущие '?' на '.'
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch == "?" and i != last_q:
            chars[i] = "."
    out = "".join(chars)

    # небольшой косметический трим пробелов перед знаками
    out = out.replace(" .", ".").replace(" ,", ",")
    return out


async def _get_active_subscription(session, user_id: int):
    # минимально: читаем любую активную подписку с максимальным сроком
    row = await session.execute(text("""
        SELECT id, subscription_until, COALESCE(is_auto_renew, true) AS is_auto_renew
        FROM subscriptions
        WHERE user_id = :uid AND status = 'active'
        ORDER BY subscription_until DESC
        LIMIT 1
    """), {"uid": user_id})
    return row.mappings().first()

def _kb_trial_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")],
        [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
    ])

def _kb_active_sub_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отменить подписку ❌", callback_data="sub:cancel")],
        [InlineKeyboardButton(text="Отменить автопродление ⏹", callback_data="sub:auto_off")],
    ])
def _kb_confirm(action: str) -> InlineKeyboardMarkup:
    # action: 'cancel' | 'auto_off'
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да, подтвердить", callback_data=f"sub:{action}:yes"),
            InlineKeyboardButton(text="Назад", callback_data="sub:cancel_back"),
        ],
    ])

@router.callback_query(lambda c: c.data == "sub:cancel_back")
async def cb_sub_cancel_back(call: CallbackQuery):
    # показываем экран /pay с кнопками «Отменить подписку / отключить автопродление»
    await on_pay(call.message)
    await call.answer()

# ===== Универсальный safe_edit (не роняет UX) =====
async def _safe_edit(msg: Message, text: Optional[str] = None, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        if text is not None and reply_markup is not None:
            await msg.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
        elif text is not None:
            await msg.edit_text(text, disable_web_page_preview=True)
        elif reply_markup is not None:
            await msg.edit_reply_markup(reply_markup=reply_markup)
        else:
            return
    except Exception:
        if text is not None:
            try:
                await msg.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)
            except Exception:
                pass
        elif reply_markup is not None:
            try:
                await msg.answer(".", reply_markup=reply_markup)
            except Exception:
                pass

# ===== Топики/клавиатуры =====
EMO_DEFAULTS = {
    "sleep": "😴", "body": "💡", "procrastination": "🌿",
    "burnout": "☀️", "decisions": "🎯", "social_anxiety": "🫥",
    "reflection": "✨",
}

def _emoji_by_topic(tid: str, title: str) -> str:
    meta = TOPICS.get(tid) or {}
    if isinstance(meta, dict):
        e = (meta.get("emoji") or "").strip()
        if e:
            return e
    pool = ["🌱", "🌿", "🌸", "🌙", "☀️", "🔥", "🧭", "🧠", "🛠️", "💡", "🧩", "🎯", "🌊", "🫶", "✨"]
    idx = int(hashlib.md5((tid or title).encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]

def _topic_title_with_emoji(tid: str) -> str:
    meta = TOPICS.get(tid) or {}
    title = (meta.get("title") or tid).strip()
    emo = (meta.get("emoji") or _emoji_by_topic(tid, title)).strip()
    return f"{emo} {title}"

def topic_button_title(tid: str) -> str:
    meta = TOPICS.get(tid, {})
    title = (meta.get("title") or tid).strip()
    emoji = (meta.get("emoji") or EMO_DEFAULTS.get(tid, "🌱")).strip()
    return f"{emoji} {title}"

def kb_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Разобраться")],
            [KeyboardButton(text="💬 Поговорить")],
            [KeyboardButton(text="🎧 Медитации")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
    )

def kb_topics() -> InlineKeyboardMarkup:
    order = TOPICS.get("__order__") or [k for k in TOPICS.keys() if not k.startswith("__")]
    rows = []
    for tid in order:
        if tid.startswith("__"):
            continue
        rows.append([InlineKeyboardButton(text=topic_button_title(tid), callback_data=f"t:{tid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_exercises(tid: str) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for eid, ex in (EXERCISES.get(tid) or {}).items():
        if not isinstance(ex, dict):
            continue
        title = ex.get("title", eid)
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"ex:{tid}:{eid}:start")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="work:topics")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_settings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎚 Тон общения", callback_data="settings:tone")],
            [InlineKeyboardButton(text="🔒 Приватность", callback_data="settings:privacy")],
        ]
    )

def kb_tone_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Универсальный (по умолчанию)", callback_data="tone:default")],
            [InlineKeyboardButton(text="🤝 Друг/подруга",                   callback_data="tone:friend")],
            [InlineKeyboardButton(text="🧠 Психологичный",                  callback_data="tone:therapist")],
            [InlineKeyboardButton(text="🌶️ 18+",                           callback_data="tone:18plus")],
        ]
    )

async def kb_privacy_for(chat_id: int) -> InlineKeyboardMarkup:
    """Строим клавиатуру с учётом текущего режима приватности."""
    try:
        mode = (await _db_get_privacy(chat_id) or "insights").lower()
    except Exception:
        mode = "insights"
    save_on = (mode != "none")
    toggle_text = "🔔 Вкл. хранение" if not save_on else "🔕 Выкл. хранение"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text,          callback_data="privacy:toggle")],
            [InlineKeyboardButton(text="🗑 Очистить историю", callback_data="privacy:clear")],
            [InlineKeyboardButton(text="⬅️ Назад",            callback_data="menu:settings")],
        ]
    )

# ===== 5) Триал: реальная активация в БД =====================================
@router.callback_query(lambda c: c.data == "trial:start")
async def cb_trial_start(call: CallbackQuery):
    """
    Стартуем триал пользователю. Если уже активен — сообщаем.
    После успешного старта — удаляем CTA и шлём сообщение с ПРАВОЙ клавиатурой.
    """
    tg_id = call.from_user.id

    async for session in get_session():
        from app.db.models import User
        q = await session.execute(select(User).where(User.tg_id == tg_id))
        u = q.scalar_one_or_none()

        if not u:
            await call.answer("Нажми /start, чтобы завершить онбординг.", show_alert=True)
            return

        # если триал уже активен — просто сообщим
        if await is_trial_active(session, u.id):
            await call.answer("Триал уже активен ✅", show_alert=True)
            return

        started, expires = await start_trial_for_user(session, u.id)
        await session.commit()

    # 1) удаляем CTA-сообщение (чтобы не плодить «лишние»)
    try:
        await call.message.delete()
    except Exception:
        pass

    # 2) шлём новое сообщение с ПРАВОЙ клавиатурой (главное меню)
    text = (
        f"Пробный период активирован ✅\n"
        f"Доступ открыт до {_fmt_dt(expires)}\n\n"
        f"Готов продолжать: выбрать «Поговорить», «Разобраться» или «Медитации»."
    )
    try:
        await call.message.answer(text, reply_markup=kb_main_menu())
    except Exception:
        # запасной вариант — без клавиатуры
        await call.message.answer(text)

    await call.answer()

@router.callback_query(lambda c: c.data == "pay:open")
async def cb_pay_open(call: CallbackQuery):
    """Открыть список планов (работает и из онбординга)."""
    # гарантируем наличие пользователя в БД, чтобы дальше не падать в /start
    try:
        await _ensure_user_id(call.from_user.id)
    except Exception:
        # не блокируем поток — просто продолжаем
        pass

    await call.message.answer(
        "Подписка «Помни»\n"
        "• Все функции без ограничений\n"
        "• 5 дней бесплатно, далее по тарифу\n\n"
        "⚠️ <i>Важно: подписка с автопродлением. Его можно отключить в любой момент в /pay.</i>\n\n"
        "<b>Выбери план:</b>",
        reply_markup=_kb_pay_plans(),
        parse_mode="HTML",
    )
    await call.answer()

@router.callback_query(lambda c: c.data == "pay:plans")
async def cb_pay_plans(call: CallbackQuery):
    """Явно показать планы (дубликат для любых мест, в т.ч. онбординг)."""
    # тоже страхуемся: создадим/найдём пользователя в БД
    try:
        await _ensure_user_id(call.from_user.id)
    except Exception:
        pass

    await call.message.answer(
        "Подписка «Помни»\n"
        "• Все функции без ограничений\n"
        "• 5 дней бесплатно, далее по тарифу\n\n"
        "⚠️ <i>Важно: подписка с автопродлением. Его можно отключить в любой момент в /pay.</i>\n\n"
        "<b>Выбери план:</b>",
        reply_markup=_kb_pay_plans(),
        parse_mode="HTML",
    )
    await call.answer()

# --- отключить автопродление ---
@router.callback_query(lambda c: c.data == "sub:auto_off")
async def cb_sub_auto_off(call: CallbackQuery):
    async for session in get_session():
        from app.db.models import User
        u = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one_or_none()
        if not u:
            await call.answer("Пользователь не найден", show_alert=True); return
        sub = await get_active_subscription_row(session, u.id)

    if not sub:
        await call.answer("Активной подписки нет.", show_alert=True); return

    until_str = _fmt_dt(sub["subscription_until"])
    await _safe_edit(
        call.message,
        text=f"Отключить автопродление?\nТекущий доступ останется до <b>{until_str}</b>, дальше продлений не будет.",
        reply_markup=_kb_confirm("auto_off"),
    )
    await call.answer()

@router.callback_query(lambda c: c.data == "sub:auto_off:yes")
async def cb_sub_auto_off_yes(call: CallbackQuery):
    async for session in get_session():
        from app.db.models import User
        u = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one_or_none()
        if not u:
            await call.answer("Пользователь не найден", show_alert=True); return
        changed, until = await disable_auto_renew(session, u.id)

    if not changed:
        await _safe_edit(call.message, text="Автопродление уже было отключено ⏹", reply_markup=_kb_active_sub_actions())
        await call.answer(); return

    until_str = _fmt_dt(until) if until else "конца периода"
    await _safe_edit(
        call.message,
        text=f"Автопродление отключено ⏹\nПодписка останется активной до {until_str}.",
        reply_markup=_kb_active_sub_actions(),
    )
    await call.answer()

# --- отменить подписку полностью ---
@router.callback_query(lambda c: c.data == "sub:cancel")
async def cb_sub_cancel(call: CallbackQuery):
    await _safe_edit(
        call.message,
        text="Отменить подписку сейчас?\nДоступ закроется сразу и восстановлению не подлежит.",
        reply_markup=_kb_confirm("cancel"),
    )
    await call.answer()

@router.callback_query(lambda c: c.data == "sub:cancel:yes")
async def cb_sub_cancel_yes(call: CallbackQuery):
    async for session in get_session():
        from app.db.models import User
        u = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one_or_none()
        if not u:
            await call.answer("Пользователь не найден", show_alert=True); return
        ok = await cancel_subscription_now(session, u.id)

    if not ok:
        await _safe_edit(call.message, text="Активной подписки не найдено.", reply_markup=_kb_pay_plans())
        await call.answer(); return

    await _safe_edit(
        call.message,
        text="Подписка отменена ❌\nЕсли захочешь вернуться — оформи новую в разделе /pay.",
        reply_markup=_kb_pay_plans(),
    )
    await call.answer()

# ===== /policy =====
@router.message(Command("policy"))
async def cmd_policy(m: Message):
    parts = ["🔒 <b>Политика и правила</b>"]
    if TERMS_URL:
        parts.append(f"• <a href='{TERMS_URL}'>Правила сервиса</a>")
    if POLICY_URL:
        parts.append(f"• <a href='{POLICY_URL}'>Политика конфиденциальности</a>")
    if not TERMS_URL and not POLICY_URL:
        parts.append("Ссылки не настроены. Добавь переменные окружения POLICY_URL и TERMS_URL.")
    await m.answer("\n".join(parts), disable_web_page_preview=True)

# ===== Онбординг =====
ONB_1_TEXT = (
    "Привет! Здесь ты можешь выговориться, разобрать ситуацию и найти опору.\n"
    "Я рядом и помогу — бережно и без оценок."
)

def kb_onb_step1() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Вперёд ➜", callback_data="onb:step2")]]
    )

ONB_2_TEXT = (
    "Прежде чем мы познакомимся, подтвердим правила и политику. "
    "Это нужно, чтобы нам обоим было спокойно и безопасно."
)

def kb_onb_step2() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    link_row: list[InlineKeyboardButton] = []
    if TERMS_URL:
        link_row.append(InlineKeyboardButton(text="📄 Правила", url=TERMS_URL))
    if POLICY_URL:
        link_row.append(InlineKeyboardButton(text="🔐 Политика", url=POLICY_URL))
    if link_row:
        rows.append(link_row)
    rows.append([InlineKeyboardButton(text="Принимаю ✅", callback_data="onb:agree")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

WHAT_NEXT_TEXT = """С чего начнём? 💛

💬 «Поговорить» — место, где можно выговориться, порефлексировать и просто навести ясность. Заботливый психолог, тёплый друг или бережный дневник событий и мыслей — то, что нужно именно сейчас.
🌿 «Разобраться» — короткие упражнения и практики под разные запросы: стресс, прокрастинация, решения и др.
🎧 «Медитации» — спокойные аудио-паузы, чтобы переключиться и дать себе передышку.

Чтобы открыть все функции, начните пробный период — 5 дней бесплатно. После — можно выбрать удобный план."""

PAYWALL_POST_TEXT = (
    "Хочу продолжить помогать, но для этого нужна подписка.\n"
    "Оформи её в /pay и получи полный доступ ко всем функциям.\n\n"
    "💬 Поддержка 24/7: выговориться, навести ясность и наметить маленькие шаги.\n"
    "🌿 Короткие практики под запрос: стресс, прокрастинация, решения.\n"
    "🎧 Аудио-медитации, чтобы переключиться и восстановиться.\n"
    "🔒 Приватно и бережно, без лекций и оценок; историю можно очистить в /privacy.\n"
    "🔬 Основано на современных подходах психологии.\n\n"
    "Подписка с автопродлением — его можно отключить в /pay после оформления."
)

def kb_onb_step3() -> ReplyKeyboardMarkup:
    # не используем на 3-м шаге: правую клавиатуру прячем до CTA
    return kb_main_menu()


@router.message(CommandStart())
async def on_start(m: Message):
    CHAT_MODE[m.chat.id] = "talk"
    img = get_onb_image("cover")
    if img:
        try:
            await m.answer_photo(img, caption=ONB_1_TEXT, reply_markup=kb_onb_step1())
            return
        except Exception:
            pass
    await m.answer(ONB_1_TEXT, reply_markup=kb_onb_step1())


@router.callback_query(F.data == "onb:step2")
async def on_onb_step2(cb: CallbackQuery):
    """ШАГ 2: экран правил/политики.
    Прячем правую клавиатуру только если политика ещё не принята — без «пустых» пузырей для тех, кто уже всё проходил.
    """
    try:
        await cb.answer()
    except Exception:
        pass

    # проверим флаги пользователя (функция уже есть в файле)
    try:
        policy_ok, _ = await _gate_user_flags(int(cb.from_user.id))
    except Exception:
        policy_ok = False  # на всякий случай

    # Только для тех, кто ещё НЕ принял политику — убираем правую клавиатуру
    if not policy_ok:
        try:
            # ничего не печатаем пользователю, просто убираем клавиатуру
            await cb.message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass

    await cb.message.answer(ONB_2_TEXT, reply_markup=kb_onb_step2())

# ===== 3) Онбординг: согласие с правилами (шаг 3) ============================
@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    """
    ШАГ 3: фиксируем согласие и показываем корректный CTA:
    - если доступ уже открыт (активный триал/подписка) — «С чего начнём?» + главное меню (для платной);
      а для активного триала — «С чего начнём?» + КНОПКА «Оформить подписку»
    - если доступа нет и триал не запускался — стартовый пейвол (с кнопкой триала);
    - если доступа нет и триал уже был — послетриальный пейвол (без кнопки триала).
    """
    tg_id = cb.from_user.id
    uid = await _ensure_user_id(tg_id)

    # фиксируем согласие
    try:
        async with async_session() as s:
            await s.execute(
                text("UPDATE users SET policy_accepted_at = CURRENT_TIMESTAMP WHERE id = :uid"),
                {"uid": uid},
            )
            await s.commit()
    except Exception:
        pass

    try:
        await cb.answer("Спасибо! Принял ✅", show_alert=False)
    except Exception:
        pass

    # выбираем правильный экран
    from app.billing.service import check_access, is_trial_active

    text_out = WHAT_NEXT_TEXT
    kb = _kb_paywall(True)

    try:
        async with async_session() as s:
            access_ok = await check_access(s, uid)     # триал ИЛИ подписка активны?
            trial_ok  = await is_trial_active(s, uid)  # именно активный триал?

            if access_ok and not trial_ok:
                # платная подписка активна -> «С чего начнём?» + правая клавиатура
                text_out = WHAT_NEXT_TEXT
                kb = kb_main_menu()
            elif trial_ok:
                # активен триал -> «С чего начнём?» + ТОЛЬКО «Оформить подписку»
                text_out = WHAT_NEXT_TEXT
                kb = _kb_paywall(False)
            else:
                # доступа нет: различаем стартовый и пост-пейвол по «был ли триал или платная подписка»
                r1 = await s.execute(text("SELECT trial_started_at FROM users WHERE id = :uid"), {"uid": uid})
                trial_started = r1.scalar() is not None

                r2 = await s.execute(text("""
                    SELECT 1
                    FROM subscriptions
                    WHERE user_id = :uid
                    LIMIT 1
                """), {"uid": uid})
                had_paid = r2.first() is not None

                if (not trial_started) and (not had_paid):
                    # совсем «чистый» пользователь: стартовый пейвол (кнопка триала)
                    text_out = WHAT_NEXT_TEXT
                    kb = _kb_paywall(True)
                else:
                    # триал был ИЛИ платная подписка когда-то была -> короткий пост-пейвол
                    text_out = PAYWALL_POST_TEXT
                    kb = _kb_paywall(False)
    except Exception:
        pass

    await cb.message.answer(text_out, reply_markup=kb)

# ===== Меню/навигация =====
@router.message(F.text == "🌿 Разобраться")
@router.message(Command("work"))
async def on_work_menu(m: Message):
    async for session in get_session():
        u = await _get_user_by_tg(session, m.from_user.id)
        if not u:
            # hint removed
            return
        if not await _enforce_access_or_paywall(m, session, u.id):
            return
    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    CHAT_MODE[m.chat.id] = "work"
    img = get_onb_image("work")
    if img:
        try:
            await m.answer_photo(img, caption="Выбирай тему:", reply_markup=kb_topics())
            return
        except Exception:
            pass
    await m.answer("Выбирай тему:", reply_markup=kb_topics())

@router.callback_query(F.data == "work:topics")
async def on_back_to_topics(cb: CallbackQuery):
    async for session in get_session():
        u = await _get_user_by_tg(session, cb.from_user.id)
        if not u:
            await cb.answer("Нажми /start, чтобы начать.", show_alert=True)
            return
        if not await _enforce_access_or_paywall(cb, session, u.id):
            return
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    await _safe_edit(cb.message, "Выбирай тему:", reply_markup=kb_topics())
    await cb.answer()

@router.callback_query(F.data.startswith("t:"))
async def on_topic_click(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    tid = cb.data.split(":", 1)[1]
    await _safe_edit(cb.message, topic_button_title(tid), reply_markup=kb_exercises(tid))
    await cb.answer()

# ===== Упражнения: шаги =====
def step_keyboard(tid: str, eid: str, idx: int, total: int) -> InlineKeyboardMarkup:
    prev_idx = max(0, idx - 1)
    next_idx = min(total - 1, idx + 1)
    nav: List[InlineKeyboardButton] = []
    if idx == 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"exlist:{tid}"))
    else:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex:{tid}:{eid}:{prev_idx}"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"ex:{tid}:{eid}:{next_idx}"))
    else:
        nav.append(InlineKeyboardButton(text="✅ Завершить", callback_data=f"ex:{tid}:{eid}:finish"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])

def step_keyboard_intro(tid: str, eid: str, total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"exlist:{tid}"),
            InlineKeyboardButton(text="➡️ Далее", callback_data=f"ex:{tid}:{eid}:0"),
        ]]
    )

@router.callback_query(F.data.startswith("exlist:"))
async def on_exlist(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    tid = cb.data.split(":", 1)[1]
    await _safe_edit(cb.message, topic_button_title(tid), reply_markup=kb_exercises(tid))
    await cb.answer()

@router.callback_query(F.data.startswith("ex:"))
async def on_ex_click(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    # ex:<tid>:<eid>:<idx|start|finish>
    try:
        parts = cb.data.split(":", 3)
        _, tid, eid = parts[0], parts[1], parts[2]
        action = parts[3] if len(parts) > 3 else "start"
    except Exception:
        await cb.answer(); return

    if eid == "reflection":
        await cb.answer()
        await _safe_edit(cb.message, "Я рядом и слушаю. О чём хочется поговорить?", reply_markup=None)
        return

    ex = (EXERCISES.get(tid) or {}).get(eid)
    if not ex:
        await cb.answer("Упражнение не найдено", show_alert=True)
        return

    steps = ex.get("steps") or []
    intro = ex.get("intro") or ""
    total = max(1, len(steps))

    if action == "finish":
        await _safe_edit(cb.message, "Готово. Вернёмся к теме?", reply_markup=kb_exercises(tid))
        await cb.answer(); return

    if action == "start":
        text_ = intro or (steps[0] if steps else "Шагов нет.")
        await _safe_edit(cb.message, text_, reply_markup=step_keyboard_intro(tid, eid, total))
        await cb.answer(); return

    try:
        idx = int(action)
    except Exception:
        idx = 0
    idx = max(0, min(idx, total - 1))
    text_ = steps[idx] if steps else "Шагов нет."
    await _safe_edit(cb.message, text_, reply_markup=step_keyboard(tid, eid, idx, total))
    await cb.answer()

# ===== Рефлексия =====
@router.callback_query(F.data == "reflect:start")
async def on_reflect_start(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    CHAT_MODE[cb.message.chat.id] = "reflection"
    await _safe_edit(cb.message, "Давай немного притормозим и прислушаемся к себе. "
                                  "Можешь начать с того, что больше всего откликается сейчас.")
    await cb.answer()

# ===== Медитации =====
def _as_track(item: object) -> dict:
    if isinstance(item, dict):
        return {
            "id": item.get("id") or item.get("key") or item.get("uid") or "",
            "title": item.get("title", "Медитация"),
            "duration": item.get("duration", ""),
            "url": item.get("url"),
        }
    if isinstance(item, (tuple, list)):
        if len(item) == 2 and isinstance(item[1], dict):
            meta = item[1]
            return {
                "id": meta.get("id") or item[0],
                "title": meta.get("title", "Медитация"),
                "duration": meta.get("duration", ""),
                "url": meta.get("url"),
            }
        if len(item) >= 3:
            return {"id": item[0], "title": item[1] or "Медитация", "url": item[2], "duration": item[3] if len(item) > 3 else ""}
        return {"id": str(item[0]), "title": str(item[-1]), "duration": "", "url": None}
    return {"id": "", "title": str(item), "duration": "", "url": None}

def kb_meditations_categories() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cid, label in get_categories():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"med:cat:{cid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_meditations_list(cid: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for raw in get_items(cid):
        tr = _as_track(raw)
        label = f"{tr['title']} · {tr.get('duration','')}".strip(" ·")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"med:play:{cid}:{tr['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Категории", callback_data="med:cats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

MEDITATIONS_TEXT = (
    "🎧 Медитации.\n"
    "Выбери тему — пришлю короткую практику.\n"
    "Начинай с того, что откликается."
)

@router.message(Command(commands=["meditations", "meditions", "meditation"]))
async def cmd_meditations(m: Message):
    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    img = get_onb_image("meditations")
    if img:
        try:
            await m.answer_photo(img, caption=MEDITATIONS_TEXT, reply_markup=kb_meditations_categories()); return
        except Exception:
            pass
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

@router.message(F.text == "🎧 Медитации")
async def on_meditations_btn(m: Message):
    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

@router.callback_query(F.data == "med:cats")
async def on_med_cats(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    await _safe_edit(cb.message, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories()); await cb.answer()

@router.callback_query(F.data.startswith("med:cat:"))
async def on_med_cat(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    cid = cb.data.split(":", 2)[2]
    title = dict(get_categories()).get(cid, "Медитации")
    await _safe_edit(cb.message, f"🎧 {title}", reply_markup=kb_meditations_list(cid))
    await cb.answer()

@router.callback_query(F.data.startswith("med:play:"))
async def on_med_play(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    _, _, cid, mid = cb.data.split(":", 3)
    raw = get_item(cid, mid)
    tr = _as_track(raw) if raw is not None else None
    if not tr:
        await cb.answer("Не нашёл аудио", show_alert=True); return

    caption = f"🎧 {tr.get('title','Медитация')} · {tr.get('duration','')}".strip(" ·")
    url = tr.get("url")

    try:
        await cb.bot.send_chat_action(cb.message.chat.id, "upload_audio")
    except Exception:
        pass

    sent_ok = False
    if url:
        try:
            await cb.message.answer_audio(url, caption=caption)
            sent_ok = True
        except Exception:
            try:
                await cb.message.answer(f"{caption}\n{url}")
                sent_ok = True
            except Exception:
                pass
    if not sent_ok:
        await cb.message.answer(caption)

    # мягкая метрика
    try:
        import json
        uid = await _ensure_user_id(cb.from_user.id)
        async with async_session() as s:
            await s.execute(
                text("""
                    INSERT INTO bot_events (user_id, event_type, payload, created_at)
                    VALUES (:uid, :etype, :payload, CURRENT_TIMESTAMP)
                """),
                {
                    "uid": uid,
                    "etype": "audio_play",
                    "payload": json.dumps(
                        {"cid": cid, "mid": mid, "title": tr.get("title"), "duration": tr.get("duration"), "url": tr.get("url")},
                        ensure_ascii=False,
                    ),
                },
            )
            await s.commit()
    except Exception:
        pass

    await cb.answer("Запускай, я рядом 💛")

# ===== Настройки =====
@router.message(F.text == "⚙️ Настройки")
@router.message(Command("settings"))
@router.message(Command("setting"))
async def on_settings(m: Message):
    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    await m.answer("Настройки:", reply_markup=kb_settings())

@router.callback_query(F.data == "menu:main")
async def on_menu_main(cb: CallbackQuery):
    await cb.message.answer("\u2063", reply_markup=kb_main_menu()); await cb.answer()

@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(cb: CallbackQuery):
    if _require_access_msg(cb.message if hasattr(cb, "message") else cb): return
    await _safe_edit(cb.message, "Настройки:", reply_markup=kb_settings()); await cb.answer()

@router.callback_query(F.data == "settings:tone")
async def on_settings_tone(cb: CallbackQuery):
    await _safe_edit(cb.message, "Выбери тон общения:", reply_markup=kb_tone_picker()); await cb.answer()

@router.callback_query(F.data == "settings:privacy")
async def on_settings_privacy(cb: CallbackQuery):
    rm = await kb_privacy_for(cb.message.chat.id)
    await _safe_edit(cb.message, "Приватность:", reply_markup=rm); await cb.answer()

@router.callback_query(F.data == "privacy:toggle")
async def on_privacy_toggle(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    mode = (await _db_get_privacy(chat_id) or "insights").lower()
    new_mode = "none" if mode != "none" else "insights"
    await _db_set_privacy(chat_id, new_mode)
    state_txt = "выключено" if new_mode == "none" else "включено"
    rm = await kb_privacy_for(chat_id)
    await _safe_edit(cb.message, f"Хранение истории сейчас: <b>{state_txt}</b>.", reply_markup=rm)
    await cb.answer("Настройка применена")

@router.callback_query(F.data == "privacy:clear")
async def on_privacy_clear(cb: CallbackQuery):
    # 1) чистим историю сообщений
    try:
        msg_count = await _purge_user_history(cb.from_user.id)
    except Exception:
        await cb.answer("Не получилось очистить историю", show_alert=True)
        return

    # 2) чистим саммари (БД + Qdrant)
    try:
        sum_count = await _purge_user_summaries_all(cb.from_user.id)
    except Exception:
        sum_count = 0  # не критично для UX

    await cb.answer("История удалена ✅", show_alert=True)
    text_ = (
        "Готово. Что дальше?\n\n"
        f"Удалено записей диалога: {msg_count}.\n"
        f"Удалено саммарей: {sum_count}."
    )
    rm = await kb_privacy_for(cb.message.chat.id)
    await _safe_edit(cb.message, text_, reply_markup=rm)

@router.message(Command("privacy"))
async def on_privacy_cmd(m: Message):
    mode = (await _db_get_privacy(m.chat.id) or "insights").lower()
    state = "выключено" if mode == "none" else "включено"
    rm = await kb_privacy_for(m.chat.id)
    await m.answer(f"Хранение истории сейчас: <b>{state}</b>.", reply_markup=rm)

@router.message(Command("help"))
async def on_help(m: Message):
    await m.answer("Если нужна помощь по сервису, напиши на selflect@proton.me — мы ответим.")

@router.message(Command("menu"))
async def on_menu(m: Message):
    msg = await m.answer('Меню', reply_markup=kb_main_menu())
    try:
        await msg.delete()
    except Exception:
        pass

# ===== Тон и режим разговора =====
@router.message(F.text == "💬 Поговорить")
@router.message(Command("talk"))
async def on_talk(m: Message):
    async for session in get_session():
        u = await _get_user_by_tg(session, m.from_user.id)
        if not u:
            # hint removed
            return
        if not await _enforce_access_or_paywall(m, session, u.id):
            return

    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    CHAT_MODE[m.chat.id] = "talk"
    await m.answer("Я рядом и слушаю. О чём хочется поговорить?", reply_markup=kb_main_menu())

@router.message(Command("tone"))
async def on_tone_cmd(m: Message):
    await m.answer("Выбери тон общения:", reply_markup=kb_tone_picker())

@router.callback_query(F.data.startswith("tone:"))
async def on_tone_pick(cb: CallbackQuery):
    style = cb.data.split(":", 1)[1]
    USER_TONE[cb.message.chat.id] = style
    await cb.answer("Стиль обновлён ✅", show_alert=False)
    await _safe_edit(cb.message, f"Тон общения установлен: <b>{style}</b> ✅", reply_markup=kb_settings())

async def _answer_with_llm(m: Message, user_text: str):
    """
    Приоритеты:
    1) Полностью полагаемся на промпты из app/prompts.py (SYSTEM_PROMPT).
    2) Сверху к системному промпту подмешиваем: выбранный тон, (опц.) рефлексию, хинт по длине ответа.
    3) Подмешиваем RAG (reflectai_corpus_v2) отдельным system-сообщением.
    4) Подмешиваем «долгую память» (daily/weekly/monthly summaries) отдельным system-сообщением.
    5) История диалога из БД (~90 последних за длительный период), затем текущее сообщение.
    """

    # Локальные импорты, чтобы не править верх файла
    import random
    from app.prompts import LENGTH_HINTS

    chat_id = m.chat.id
    mode = CHAT_MODE.get(chat_id, "talk")

    # 0) Спец-ответ на запросы «что мы говорили X назад/недавно…»
    try:
        if await _maybe_answer_memory_question(m, user_text):
            return
    except Exception:
        pass  # не мешаем основному потоку

    # 1) SYSTEM_PROMPT + тон + (опц.) рефлексия
    style = USER_TONE.get(chat_id, "default")  # "default" | "friend" | "therapist" | "18plus"
    sys_prompt = SYSTEM_PROMPT
    tone_suffix = STYLE_SUFFIXES.get(style, "")
    if tone_suffix:
        sys_prompt += "\n\n" + tone_suffix
    if mode == "reflection" and REFLECTIVE_SUFFIX:
        sys_prompt += "\n\n" + REFLECTIVE_SUFFIX

    # ЯВНАЯ подсказка длины по словам-триггерам (перекрывает общий picker)
    try:
        t = (user_text or "").lower()
        need_deep = any(x in t for x in ["разложи подробно", "подробно", "план", "что делать по шагам", "структурируй", "инструкция"])
        need_medium = any(x in t for x in ["объясни", "поясни", "структурируй", "как это работает", "почему"])

        if need_deep:
            sys_prompt += "\n\n" + LENGTH_HINTS["deep"]
        elif need_medium:
            sys_prompt += "\n\n" + LENGTH_HINTS["medium"]
        else:
            # по умолчанию подсказываем короткий формат, остальное дорегулируем ниже
            sys_prompt += "\n\n" + LENGTH_HINTS["short"]
    except Exception:
        pass

    # 1.1) Вариативность объёма ответа (микро/короткий/средний/глубокий)
    def _pick_length_profile(ut: str, turns: int) -> str:
        t = (ut or "").lower()
        # Запросы «что делать / план / разложи / подробно»
        if any(k in t for k in ("что делать", "план", "структур", "разложи", "подробн", "как сделать", "шаг")):
            return "deep" if "подроб" in t or "план" in t else "medium"
        # Быстрые «успокой / паническая атака / тревога / дыхание / прямо сейчас»
        if any(k in t for k in ("паническ", "тревог", "успокой", "дышан", "прямо сейчас", "помоги сейчас")):
            return "micro" if "прямо сейчас" in t or "паническ" in t else "short"
        # По умолчанию — мягкая вариативность + чуть глубже на 1-м и 5–6-м ходах
        base = [("micro", 0.15), ("short", 0.45), ("medium", 0.30), ("deep", 0.10)]
        if turns in (0, 1, 5, 6):
            base = [("micro", 0.10), ("short", 0.40), ("medium", 0.35), ("deep", 0.15)]
        r = random.random()
        acc = 0.0
        for key, p in base:
            acc += p
            if r <= acc:
                return key
        return "short"

    # 2) История беседы из БД (старые -> новые)
    history_msgs: List[dict] = []
    try:
        history_msgs = await _load_history_from_db(m.from_user.id, limit=90, hours=24*90)
    except Exception:
        # запасной вариант: in-memory
        try:
            recent = get_recent_messages(chat_id, limit=90)
            for r in recent:
                role = "assistant" if r["role"] == "bot" else "user"
                history_msgs.append({"role": role, "content": r["text"]})
        except Exception:
            history_msgs = []

    # 1.2) Применяем хинт по длине (после того как знаем номер хода)
    try:
        turn_idx = len(history_msgs)  # сколько сообщений уже в переписке (перед текущим юзер-сообщением)
    except Exception:
        turn_idx = 0
    length_key = _pick_length_profile(user_text, turn_idx)
    len_hint = LENGTH_HINTS.get(length_key, "")
    if len_hint:
        sys_prompt += "\n\n" + len_hint

    # 3) RAG-контекст — отдельным system-сообщением
    rag_ctx = ""
    if rag_search is not None:
        try:
            # адаптивный k/max_chars от длины запроса
            qlen = len((user_text or "").split())
            k = 3 if qlen < 8 else 6 if qlen < 20 else 8
            max_chars = 600 if qlen < 8 else 1000 if qlen < 30 else 1400
            rag_ctx = await rag_search(user_text, k=k, max_chars=max_chars, lang="ru")
        except Exception:
            rag_ctx = ""

    # 4) Долгая память (саммари): daily/weekly/monthly — как мягкий «прошлый опыт»
    sum_block = ""
    try:
        uid = await _ensure_user_id(m.from_user.id)
        hits = await search_summaries(user_id=uid, query=user_text, top_k=4)
        ids = [int(h.get("summary_id")) for h in (hits or []) if str(h.get("summary_id", "")).isdigit()]
        items = await _fetch_summary_texts_by_ids(ids)

        if items:
            # ограничим длину каждого саммари и общий блок, чтобы не «съедать» контекст
            def _short(s: str, n: int = 260) -> str:
                s = (s or "").strip().replace("\r", " ").replace("\n", " ")
                return s if len(s) <= n else (s[: n - 1] + "…")

            lines = [f"• [{it['period']}] {_short(it.get('text', ''))}" for it in items]
            sum_text = "\n".join(lines).strip()
            MAX_SUMMARY_BLOCK = 900
            if len(sum_text) > MAX_SUMMARY_BLOCK:
                sum_text = sum_text[: MAX_SUMMARY_BLOCK - 1] + "…"

            sum_block = "Заметки из прошлых разговоров (учитывай по мере уместности):\n" + sum_text
    except Exception:
        sum_block = ""

    # 5) Сбор сообщений для LLM
    # Порядок: system стиль -> system RAG -> system summaries -> история -> текущее сообщение
    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    if rag_ctx:
        messages.append({"role": "system", "content": f"Материалы из базы знаний по теме:\n{rag_ctx}"})
    if sum_block:
        messages.append({"role": "system", "content": sum_block})

    messages += history_msgs
    messages.append({"role": "user", "content": user_text})

    # 6) Вызов LLM
    if chat_with_style is None:
        await send_and_log(m, "Я тебя слышу. Сейчас подключаюсь…", reply_markup=kb_main_menu())
        return

    # Живая речь: температура слегка варьируется детерминированно от текста и длины истории
    seed = f"{user_text}|{turn_idx}"
    temp = 0.66 + (abs(hash(seed)) % 17) / 100.0  # 0.66–0.82
    LLM_MAX_TOKENS = 480

    try:
        reply = await chat_with_style(
            messages=messages,
            temperature=temp,
            max_tokens=LLM_MAX_TOKENS,
        )
    except TypeError:
        # на случай старой сигнатуры
        reply = await chat_with_style(messages, temperature=temp, max_tokens=LLM_MAX_TOKENS)
    except Exception:
        reply = ""

    if not reply or not reply.strip():
        reply = "Хочу понять суть поточнее. Что в этой ситуации сейчас болит сильнее всего — один-два предложения?"

    # NEW: пост-фильтр — максимум один вопрос
    try:
        reply = _enforce_single_question(reply)
    except Exception:
        pass

    # --- Анти-повторы стартовых формул: мягкая перегенерация при совпадении ---
    try:
        # берём/создаём in-memory LRU в модуле
        store = globals().setdefault("LAST_OPENERS", {})
        from collections import deque as _dq
        if chat_id not in store:
            store[chat_id] = _dq(maxlen=3)

        def _extract_opener_local(text: str) -> str:
            line = (text or "").strip().split("\n", 1)[0]
            return line[:60].lower()

        opener = _extract_opener_local(reply)
        seen = store.get(chat_id)
        opener_seen = opener in seen

        if opener_seen and chat_with_style is not None:
            # аккуратный хинт: переписать ПЕРВУЮ строку без клише/повторов
            sys_prompt_r = sys_prompt + "\n\n(Перепиши первую строку без клише и повторов, начни с наблюдения по сути ситуации.)"

            messages_r: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt_r}]
            if rag_ctx:
                messages_r.append({"role": "system", "content": f"Материалы из базы знаний по теме:\n{rag_ctx}"})
            if sum_block:
                messages_r.append({"role": "system", "content": sum_block})
            messages_r += history_msgs
            messages_r.append({"role": "user", "content": user_text})

            try:
                reply_r = await chat_with_style(messages=messages_r, temperature=temp, max_tokens=LLM_MAX_TOKENS)
            except TypeError:
                reply_r = await chat_with_style(messages_r, temperature=temp, max_tokens=LLM_MAX_TOKENS)
            except Exception:
                reply_r = ""

            if reply_r and reply_r.strip():
                try:
                    reply = _enforce_single_question(reply_r)
                except Exception:
                    reply = reply_r

        # обновляем LRU
        seen.append(_extract_opener_local(reply))
    except Exception:
        pass

    # 7) Отправляем и логируем как 'bot' (для устойчивой памяти)
    await send_and_log(m, reply, reply_markup=kb_main_menu())

# ===== Текстовые сообщения =====
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    chat_id = m.chat.id

    if CHAT_MODE.get(chat_id, "talk") in ("talk", "reflection"):
        await _answer_with_llm(m, m.text or ""); return

    if CHAT_MODE.get(chat_id) == "work":
        await m.answer(
            "Если хочешь обсудить — нажми «Поговорить». Если упражнение — выбери тему в «Разобраться».",
            reply_markup=kb_main_menu(),
        ); return

    await m.answer("Я рядом и на связи. Нажми «Поговорить» или «Разобраться».", reply_markup=kb_main_menu())

# === /pay — планы с 4 тарифами =========================================
from aiogram.filters import Command as _CmdPay

_PLANS = {
    "week":  (499,  "Подписка на 1 неделю"),
    "month": (1190, "Подписка на 1 месяц"),
    "q3":    (2990, "Подписка на 3 месяца"),
    "year":  (7990, "Подписка на 1 год"),
}

def _kb_pay_plans() -> _IKM:
    return _IKM(inline_keyboard=[
        [_IKB(text="Неделя — 499 ₽",    callback_data="pay:plan:week")],
        [_IKB(text="Месяц — 1190 ₽",    callback_data="pay:plan:month")],
        [_IKB(text="3 месяца — 2990 ₽", callback_data="pay:plan:q3")],
        [_IKB(text="Год — 7990 ₽",      callback_data="pay:plan:year")],
    ])

@router.message(_CmdPay("pay"))
async def on_pay(m: Message):
    tg_id = m.from_user.id

    async for session in get_session():
        from app.db.models import User
        u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if not u:
            await m.answer("Нажми /start, чтобы завершить онбординг.", reply_markup=kb_main_menu())
            return

        # 1) активная подписка?
        active_sub = await _get_active_subscription(session, u.id)
        if active_sub:
            until = active_sub["subscription_until"]
            await m.answer(
                f"Подписка активна ✅\nДоступ открыт до <b>{_fmt_dt(until)}</b>.\n\n"
                f"Что дальше?",
                reply_markup=_kb_active_sub_actions()
            )
            return

        # 2) активный триал?
        if await is_trial_active(session, u.id):
            until = getattr(u, "trial_expires_at", None)
            tail = f"до <b>{_fmt_dt(until)}</b>" if until else "сейчас"
            await m.answer(
                f"Пробный период активирован — {tail}. ✅\n"
                f"Все функции открыты.\n\n"
                f"Хочешь оформить подписку сразу? (Можно в любой момент отменить автопродление в /pay.)",
                reply_markup=_kb_trial_pay()
            )
            return

    # 3) доступа нет — показываем тарифы + предупреждение
    await m.answer(
        "Подписка «Помни»\n"
        "• Все функции без ограничений\n"
        "• 5 дней бесплатно, далее по тарифу\n\n"
        "⚠️ <i>Важно: подписка с автопродлением. Его можно отключить в любой момент в /pay.</i>\n\n"
        "<b>Выбери план:</b>",
        reply_markup=_kb_pay_plans()
    )

@router.callback_query(F.data.startswith("pay:plan:"))
async def on_pick_plan(cb: CallbackQuery):
    """
    Пользователь выбрал тариф. Создаём платёж в ЮKassa и даём ссылку.
    """
    # raw: pay:plan:month | pay:plan:week | pay:plan:q3 | pay:plan:year ...
    try:
        raw_plan = (cb.data or "").split(":", 2)[-1].strip().lower()
    except Exception:
        await cb.answer("Некорректный запрос", show_alert=True)
        return

    # Алиасы -> нормализованный план (должен совпадать с тем, что обрабатывает вебхук)
    PLAN_ALIAS = {
        "q3": "quarter",
        "3m": "quarter",
        "quarter": "quarter",
        "week": "week",
        "weekly": "week",
        "month": "month",
        "year": "year",
        "annual": "year",
        "y": "year",
        "q": "quarter",
    }
    plan = PLAN_ALIAS.get(raw_plan, raw_plan)

    if plan not in _PLANS:
        await cb.answer("Неизвестный план", show_alert=True)
        return

    amount, desc = _PLANS[plan]  # int RUB, str

    # Находим пользователя по tg_id
    async for session in get_session():
        from app.db.models import User
        u = (await session.execute(
            select(User).where(User.tg_id == cb.from_user.id)
        )).scalar_one_or_none()

        if not u:
            await cb.answer("Нажми /start, чтобы начать.", show_alert=True)
            return

        # Пытаемся создать платёж и получить redirect URL
        try:
            pay_url = create_payment_link(
                amount_rub=int(amount),
                description=desc,
                metadata={"user_id": int(u.id), "plan": plan},
                # return_url берётся из YK_RETURN_URL (ENV), можно не передавать
            )
        except Exception as e:
            # НЕ глотаем причину — логируем
            print(f"[pay] create_payment_link raised: {e}")
            pay_url = None

    if not pay_url:
        # если прилетит 401/422 и т.п., подробности будут в логах из yookassa_client.py
        await cb.message.answer("Не удалось сформировать платёж. Попробуй ещё раз позже.")
        await cb.answer()
        return

    kb = _IKM(inline_keyboard=[[ _IKB(text="Оплатить 💳", url=pay_url) ]])
    await cb.message.answer(
        f"<b>{desc}</b>\nСумма: <b>{amount} ₽</b>\n\nНажми «Оплатить 💳», чтобы перейти к форме.",
        reply_markup=kb
    )
    await cb.answer()

# ===== Gate middleware: единственная версия и однократный mount =====
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Awaitable, Any, Dict, Tuple, Union

AllowedEvent = Union[Message, CallbackQuery]
ALLOWED_CB_PREFIXES = ("trial:", "pay:", "plan:", "tariff:", "yk:")

async def _gate_user_flags(tg_id: int) -> Tuple[bool, bool]:
    """
    Возвращает: (policy_ok, access_ok)
    policy_ok — принят ли экран правил;
    access_ok — есть ли доступ (триал или подписка).
    """
    from sqlalchemy import text
    from app.db.core import async_session
    from app.billing.service import check_access

    async with async_session() as s:
        r = await s.execute(text("SELECT id, policy_accepted_at FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        row = r.first()
        if not row:
            return False, False
        uid = int(row[0])
        policy_ok = bool(row[1])

    async with async_session() as s2:
        try:
            access_ok = await check_access(s2, uid)
        except Exception:
            access_ok = False

    return policy_ok, access_ok


async def _gate_send_policy(event: AllowedEvent) -> None:
    """Показываем экран с «Принимаю»."""
    import os
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📄 Правила", url=os.getenv("LEGAL_POLICY_URL") or "https://example.com/policy"),
            InlineKeyboardButton(text="🔐 Политика", url=os.getenv("LEGAL_OFFER_URL")  or "https://example.com/offer"),
        ],
        [InlineKeyboardButton(text="Принимаю ✅", callback_data="onb:agree")],
    ])
    text = ("Прежде чем мы познакомимся, подтвердим правила и политику. "
            "Это нужно, чтобы нам обоим было спокойно и безопасно.")
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text, reply_markup=kb)


async def _gate_send_trial_cta(event: Union[Message, CallbackQuery]) -> None:
    """
    Пейвол в «закрытых» местах:
    - если триала ещё не было И не было платной подписки — стартовый (кнопка «Начать пробный…»)
    - иначе (триал уже был ИЛИ платная подписка уже была, но истекла/отключена) — пост-триальный короткий пейвол (только «Оформить подписку»)
    """
    from sqlalchemy import text
    from app.db.core import async_session

    tg_id = getattr(getattr(event, "from_user", None), "id", None)
    show_trial = False
    try:
        async with async_session() as s:
            # был ли когда-либо триал
            trial_started = False
            if tg_id:
                r1 = await s.execute(
                    text("SELECT trial_started_at FROM users WHERE tg_id = :tg"),
                    {"tg": int(tg_id)},
                )
                trial_started = r1.scalar() is not None

            # была ли когда-либо платная подписка (любая запись в subscriptions)
            had_paid = False
            if tg_id:
                r2 = await s.execute(text("""
                    SELECT 1
                    FROM subscriptions AS s
                    JOIN users u ON u.id = s.user_id
                    WHERE u.tg_id = :tg
                    LIMIT 1
                """), {"tg": int(tg_id)})
                had_paid = r2.first() is not None

            # показываем кнопку триала только если не было НИ триала, НИ платной подписки
            show_trial = (not trial_started) and (not had_paid)
    except Exception:
        show_trial = False  # безопасно: короткий пост-пейвол

    text_out = WHAT_NEXT_TEXT if show_trial else PAYWALL_POST_TEXT
    kb = _kb_paywall(show_trial)

    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text_out, reply_markup=kb)


class GateMiddleware(BaseMiddleware):
    """
    1) Пока не принят policy — разрешены только /start и onb:* (остальное — экран policy).
    2) После policy, но до доступа — разрешены только /pay и trial/pay/plan/tariff/yk:* (остальное — CTA).
    3) Когда доступ открыт — пропускаем всё.
    """
    async def __call__(
        self,
        handler: Callable[[AllowedEvent, Dict[str, Any]], Awaitable[Any]],
        event: AllowedEvent,
        data: Dict[str, Any]
    ) -> Any:
        try:
            tg_id = getattr(getattr(event, "from_user", None), "id", None)
            if not tg_id:
                return await handler(event, data)

            policy_ok, access_ok = await _gate_user_flags(int(tg_id))

            # 1) policy ещё не принят
            if not policy_ok:
                if isinstance(event, Message):
                    if (event.text or "").startswith("/start"):
                        return await handler(event, data)
                elif isinstance(event, CallbackQuery):
                    if (event.data or "").startswith("onb:"):
                        return await handler(event, data)
                await _gate_send_policy(event)
                return

            # 2) policy принят, но доступа нет
            if not access_ok:
                if isinstance(event, Message):
                    if (event.text or "").startswith("/pay"):
                        return await handler(event, data)
                elif isinstance(event, CallbackQuery):
                    d = (event.data or "")
                    if d.startswith(ALLOWED_CB_PREFIXES):
                        return await handler(event, data)
                await _gate_send_trial_cta(event)
                return

            # 3) доступ открыт — пропускаем всё
            return await handler(event, data)

        except Exception:
            # fail-open — не блокируем на исключениях
            return await handler(event, data)


# --- Однократный mount, чтобы не было дублей ---
if not getattr(router, "_gate_mounted", False):
    router.message.middleware(GateMiddleware())
    router.callback_query.middleware(GateMiddleware())
    router._gate_mounted = True

# Логирование входящих после Gate (чтобы не писать экраны-перехваты)
router.message.middleware(LogIncomingMiddleware())
router.callback_query.middleware(LogIncomingMiddleware())

@router.message(Command("about"))
async def cmd_about(m: Message):
    import os
    email = os.getenv("CONTACT_EMAIL") or "support@example.com"
    txt = (
        "«Помни» — тёплый помощник, который помогает выговориться и прояснить мысли. "
        "Бережная и безоценочная поддержка, опираясь на современный научный подход.\n\n"
        "Что внутри:\n"
        "• «Поговорить» — бот эмоциональной поддержки с функцией дневника: разложить ситуацию, найти опору, наметить 1 маленький шаг.\n"
        "• «Разобраться» — мини-практики и упражнения под запросы: стресс, прокрастинация, выгорание, решения и др.\n"
        "• «Медитации» — спокойные аудио-паузы, чтобы переключиться и дать себе передышку.\n\n"
        "Наши внутренние правила:\n"
        "— мягкое и дружеское общение, без правил и лекций — сам решай как и о чем хочется вести диалог;\n"
        "— бережные рамки КПТ/АКТ/гештальта; нормализация и маленькие поведенческие шаги;\n"
        "— приватность по умолчанию: можно отключить хранение истории в /privacy (тогда мы не будем запоминать разговоры).\n\n"
        "Мы развиваем «Помни»: новые практики, тональности, режим дневника, напоминания и больше медитаций.\n"
        "Если есть идеи или обратная связь — напиши нам на почту: {email}"
    ).format(email=email)
    await m.answer(txt)
