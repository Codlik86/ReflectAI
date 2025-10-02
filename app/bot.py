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
from app.prompts import SYSTEM_PROMPT as BASE_PROMPT
from app.prompts import TALK_SYSTEM_PROMPT as TALK_PROMPT
try:
    from app.prompts import REFLECTIVE_SUFFIX  # опционально
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

router = Router()

# ========== AUTO-LOGGING В БД (bot_messages) ==========
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

async def _log_message_by_tg(tg_id: int, role: str, text_: str) -> None:
    """
    Лог в bot_messages c учётом users.id.
    Режем текст до 4000 символов.
    """
    try:
        # уважаем приватность: если выключена — не пишем
        mode = (await _db_get_privacy(int(tg_id)) or "insights").lower()
        if mode == "none":
            return

        uid = await _ensure_user_id(int(tg_id))
        safe = (text_ or "")[:4000]
        if not safe:
            return
        async with async_session() as s:
            await s.execute(
                text("""
                    INSERT INTO bot_messages (user_id, role, text, created_at)
                    VALUES (:u, :r, :t, CURRENT_TIMESTAMP)
                """),
                {"u": int(uid), "r": role, "t": safe},
            )
            await s.commit()
    except Exception as e:
        # не роняем обработку
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

async def send_and_log(message: Message, text_: str, **kwargs):
    """
    Хелпер для исходящих реплик бота (role='assistant').
    Можно использовать точечно, где важно фиксировать ответ.
    """
    sent = await message.answer(text_, **kwargs)
    try:
        await _log_message_by_tg(message.from_user.id, "assistant", text_)
    except Exception as e:
        print("[send-log] error:", repr(e))
    return sent
# =======================================================

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

# =============================== Московское время ===============================
import os
from zoneinfo import ZoneInfo

BOT_TZ = os.getenv("BOT_TZ", "Europe/Moscow")

def _fmt_local(dt_utc) -> str:
    """
    Принимает TZ-aware UTC datetime и форматирует в локальной зоне BOT_TZ.
    На случай нераспарсенных/naive дат — делает максимально мягкое приведение.
    """
    try:
        tz = ZoneInfo(BOT_TZ)
        # если вдруг dt без tz — считаем, что это UTC
        if getattr(dt_utc, "tzinfo", None) is None:
            from datetime import timezone
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(tz).strftime('%d.%m.%Y %H:%M')
    except Exception:
        # запасной вариант — без конвертации, но хотя бы отформатируем
        return dt_utc.strftime('%d.%m.%Y %H:%M')
    
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
    async with async_session() as s:
        r = await s.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        uid = r.scalar()
        if not uid:
            return 0
        r = await s.execute(text("DELETE FROM bot_messages WHERE user_id = :u"), {"u": int(uid)})
        await s.commit()
        return int(getattr(r, "rowcount", 0) or 0)

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

# --- Анти-штампы и эвристики «шаблонности»
BANNED_PHRASES = [
    "это, безусловно, очень трудная ситуация",
    "я понимаю, как ты себя чувствуешь",
    "важно дать себе время и пространство",
    "не забывай заботиться о себе",
    "если тебе нужно, можешь обратиться к друзьям"
]

def _has_banned_phrases(text_: str) -> bool:
    t = (text_ or "").lower()
    return any(p in t for p in BANNED_PHRASES)

def _jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def _too_similar_to_recent(chat_id: int, candidate: str, *, lookback: int = 8, thr: float = 0.62) -> bool:
    try:
        recent = get_recent_messages(chat_id, limit=lookback * 2)
        prev_bot = [m["text"] for m in recent if m["role"] == "bot"][-lookback:]
    except Exception:
        prev_bot = []
    return any(_jaccard(candidate, old) >= thr for old in prev_bot)

def _looks_templatey(text_: str) -> bool:
    return _has_banned_phrases(text_)

# --- «дневничковая» длина: когда можно расписать подробнее
STRUCTURE_KEYWORDS = [
    "что делать", "как поступить", "план", "шаги", "структур",
    "объясни", "разложи", "почему", "как справиться", "помоги разобраться",
]

def _wants_structure(user_text: str) -> bool:
    t = (user_text or "").lower()
    return (len(t) >= 240) or any(k in t for k in STRUCTURE_KEYWORDS)

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
        f"Доступ открыт до {_fmt_local(expires)}\n\n"
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

# ===== LLM-помощник =====
def _style_overlay(style_key: str | None) -> str:
    if not style_key or style_key == "default":
        return ("Начинай с наблюдения или уточнения, без клише. "
                "Если уместно — мини-план 2–5 пунктов или 2–4 абзаца пояснения. "
                "Всегда 1 вопрос/вилка в конце.")
    if style_key == "friend":
        return ("Разговорно и по-простому. Без клише «понимаю/держись». "
                "Можно мини-план, если человеку нужна структура. В конце — вопрос/вилка.")
    if style_key == "therapist":
        return ("Психологичный, но конкретный: проясняй фокус, объясняй кратко, "
                "давай 1 небольшой шаг или мини-план. Без лекций и штампов.")
    if style_key == "18plus":
        return ("Можно смелее формулировки (без грубости). Конкретика, при необходимости мини-план. "
                "Финал — вопрос/вилка.")
    return ""

async def _answer_with_llm(m: Message, user_text: str):
    chat_id = m.chat.id
    mode = CHAT_MODE.get(chat_id, "talk")
    style_key = USER_TONE.get(chat_id, "default")

    # 1) System prompt
    sys_prompt = TALK_PROMPT if mode in ("talk", "reflection") else BASE_PROMPT
    overlay = _style_overlay(style_key)
    if overlay:
        sys_prompt = sys_prompt + "\n\n" + overlay
    if mode == "reflection" and REFLECTIVE_SUFFIX:
        sys_prompt = sys_prompt + "\n\n" + REFLECTIVE_SUFFIX

    # 2) История (старые → новые)
    history_msgs: List[dict] = []
    try:
        recent = get_recent_messages(chat_id, limit=70)
        for r in recent:
            role = "assistant" if r["role"] == "bot" else "user"
            history_msgs.append({"role": role, "content": r["text"]})
    except Exception:
        pass

    # 3) RAG-контекст (опционально, из внешних источников)
    rag_ctx = ""
    if rag_search is not None:
        try:
            rag_ctx = await rag_search(user_text, k=6, max_chars=900, lang="ru")
        except Exception:
            rag_ctx = ""

    # 4) Долгая память: заметки из саммарей пользователя (daily/weekly/topic)
    #    ищем релевантные саммари в Qdrant и подтягиваем тексты из БД
    sum_block = ""
    try:
        uid = await _ensure_user_id(m.from_user.id)  # users.id
        hits = await search_summaries(user_id=uid, query=user_text, top_k=4)  # [{'summary_id', 'kind', ...}]
        ids = [int(h["summary_id"]) for h in hits] if hits else []
        items = await _fetch_summary_texts_by_ids(ids)
        if items:
            # делаем компактный блок
            lines = [f"• [{it['period']}] {it['text']}" for it in items]
            sum_block = "— Полезные заметки из прошлых разговоров (пригодится, но не обязательно):\n" + "\n".join(lines)
    except Exception:
        sum_block = ""

    # 5) Сбор финальных messages
    messages = [{"role": "system", "content": sys_prompt}]
    if rag_ctx:
        messages.append({"role": "system", "content": f"Фактический контекст по теме (используй по необходимости):\n{rag_ctx}"})
    if sum_block:
        messages.append({"role": "system", "content": sum_block})
    messages += history_msgs
    messages.append({"role": "user", "content": user_text})

    if chat_with_style is None:
        await m.answer("Я тебя слышу. Сейчас подключаюсь…")
        return

    LLM_MAX_TOKENS = 480

    def _needs_regen(text_: str) -> bool:
        return not text_ or _looks_templatey(text_) or _too_similar_to_recent(chat_id, text_)

    # Первая попытка
    try:
        reply = await chat_with_style(
            messages=messages,
            style_hint=None,
            temperature=0.85,
            max_tokens=LLM_MAX_TOKENS,
        )
    except TypeError:
        reply = await chat_with_style(messages, temperature=0.85, max_tokens=LLM_MAX_TOKENS)
    except Exception:
        reply = ""

    # Переписываем при шаблонности
    if _needs_regen(reply):
        fixer_system = (
            "Перепиши ответ живее, без клише и общих слов.\n"
            "Формат: начни с наблюдения/уточнения или короткого вывода (не с «понимаю/это сложно»), "
            "пиши конкретно, по делу. Допускается 2–4 коротких абзаца ИЛИ 2–5 пунктов, если это помогает. "
            "Один мягкий вопрос/вилка в конце. Избегай фраз из чёрного списка: "
            + "; ".join(BANNED_PHRASES) + "."
        )
        refine_msgs = [
            {"role": "system", "content": fixer_system},
            {"role": "user", "content": f"Черновик ответа (перепиши в духе требований):\n\n{reply or '(пусто)'}"},
        ]
        try:
            better = await chat_with_style(
                messages=refine_msgs,
                temperature=0.8,
                max_tokens=LLM_MAX_TOKENS,
            )
        except TypeError:
            better = await chat_with_style(refine_msgs, temperature=0.8, max_tokens=LLM_MAX_TOKENS)
        except Exception:
            better = ""
        if better and not _needs_regen(better):
            reply = better

    if not reply:
        reply = "Давай сузим: какой момент здесь для тебя самый болезненный? Два-три предложения."

    await send_and_log(m, reply, reply_markup=kb_main_menu())

@router.message(Command("debug_prompt"))
async def on_debug_prompt(m: Message):
    mode = CHAT_MODE.get(m.chat.id, "talk")
    style_key = USER_TONE.get(m.chat.id, "default")
    sys_prompt = TALK_PROMPT if mode in ("talk", "reflection") else BASE_PROMPT
    overlay = _style_overlay(style_key)
    if overlay:
        sys_prompt += "\n\n" + overlay
    if mode == "reflection" and REFLECTIVE_SUFFIX:
        sys_prompt += "\n\n" + REFLECTIVE_SUFFIX
    preview = sys_prompt[:1200]
    await m.answer(f"<b>mode</b>: {mode}\n<b>tone</b>: {style_key}\n\n<code>{preview}</code>")

# ===== Текстовые сообщения =====
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    if _require_access_msg(m.message if hasattr(m, "message") else m): return
    chat_id = m.chat.id
    try:
        save_user_message(chat_id, m.text or "")
    except Exception:
        pass

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
