from __future__ import annotations

import asyncio
import os
import hashlib
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
import logging

from aiogram import Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    LabeledPrice,
    PreCheckoutQuery,
)

# алиасы для клавиатуры (используются в нескольких местах, в т.ч. deep-link)
from aiogram.types import InlineKeyboardMarkup as _IKM, InlineKeyboardButton as _IKB

# рядом с остальными импортами aiogram.types
try:
    from aiogram.types import WebAppInfo
except Exception:
    from aiogram.types.web_app_info import WebAppInfo  # на случай другой версии aiogram

# ===== Модули продукта (оставляем только нужное) =====
from app.memory import get_recent_messages
from app.stats import stats_router
from app.prompts import SYSTEM_PROMPT, STYLE_SUFFIXES, LENGTH_HINTS
try:
    from app.prompts import REFLECTIVE_SUFFIX
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
from sqlalchemy import text, select
from app.db.core import async_session, get_session
from app.billing.yookassa_client import create_payment_link
from app.billing.service import (
    start_trial_for_user,
    check_access,
    is_trial_active,
    disable_auto_renew,
    cancel_subscription_now,
    get_active_subscription_row,
    apply_success_payment,
)
from app.services.access_state import TRIAL_DAYS, get_access_state
from app.services.tg_blocked import mark_user_unblocked
from app.intents import is_subscription_intent
from app.texts_pay import get_pay_help_text
from app.services.access_state import get_access_state
from app.billing.prices import plan_price_int, plan_price_stars, PLAN_PRICES_INT
from app.services.short_reply import is_short_reply, normalize_short_reply

from zoneinfo import ZoneInfo
from collections import deque
import re

router = Router()
logger = logging.getLogger(__name__)

# ===== Админы =====
def _parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


_ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip() or "53652078"
ADMIN_IDS_SET: set[int] = _parse_admin_ids(_ADMIN_IDS_RAW)


def is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in ADMIN_IDS_SET
    except Exception:
        return False

# === Диагностика (последние ошибки LLM и memory) ===
LAST_LLM_STATUS: dict = {"ts": None, "error": None, "meta": None}
LAST_MEMORY_STATUS: dict = {"ts": None, "error": None, "source": None, "summaries_count": 0, "qdrant_error": None}

def _ts_now() -> str:
    try:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"
    except Exception:
        return ""

def _record_llm_status(error: Optional[str], meta: Optional[dict]) -> None:
    LAST_LLM_STATUS["ts"] = _ts_now()
    LAST_LLM_STATUS["error"] = error
    LAST_LLM_STATUS["meta"] = meta or {}

def _record_memory_status(*, error: Optional[str], source: Optional[str], summaries_count: int = 0, qdrant_error: Optional[str] = None) -> None:
    LAST_MEMORY_STATUS["ts"] = _ts_now()
    LAST_MEMORY_STATUS["error"] = error
    LAST_MEMORY_STATUS["source"] = source
    LAST_MEMORY_STATUS["summaries_count"] = int(summaries_count or 0)
    LAST_MEMORY_STATUS["qdrant_error"] = qdrant_error


def _safe_excerpt(text: str, n: int = 120) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t if len(t) <= n else t[: n - 1] + "…"


def _fallback_reply(user_text: str) -> str:
    core = _safe_excerpt(user_text, 140)
    if core:
        return f"Слышу тебя: «{core}». Давай разберёмся, что самое важное сейчас? Расскажи в одном-двух предложениях."
    return "Слышу тебя. Подскажи, что сейчас волнует больше всего — в одном-двух предложениях, чтобы я мог помочь точнее."


def extract_opener(text: str) -> str:
    """
    Нормализует первую строку ответа, чтобы ловить повторяющиеся опенеры.
    """
    import re as _re

    first_line = (text or "").split("\n", 1)[0].strip()
    # убираем ведущие кавычки/пробелы/точки/тире/• и т.п.
    first_line = _re.sub(r'^[\s"\'“”«»„‚`´’\(\)\[\]{}·•…–—\-]+', "", first_line)

    def _cut_by_delims(s: str) -> str:
        # приоритет: ":" -> "—/–" -> "."/"!"/"?" -> "," -> "…"
        for delim in (":", "—", "–", ".", "!", "?", ",", "…"):
            idx = s.find(delim)
            if idx >= 3:  # не режем слишком рано
                return s[:idx]
        return s

    opener = _cut_by_delims(first_line)
    if not opener:
        opener = first_line

    if not opener:
        return ""

    # если нет разделителя — возьмём первые 10 слов
    if opener == first_line:
        words = opener.split()
        opener = " ".join(words[:10])

    opener = opener.lower()
    opener = _re.sub(r"\s+", " ", opener).strip()
    opener = _re.sub(r"([!?.,…])\1+", r"\1", opener)
    return opener[:60]


BANNED_OPENERS_PREFIXES = ["похоже", "понимаю", "слышу", "кажется", "звучит"]
_OPENER_PREFIX_MAX_WORDS = 2
_OPENER_PREFIX_HISTORY = 6
_OPENER_PREFIX_REPEAT_WINDOW = 2


def normalize_opener_prefix(text: str) -> str:
    line = (text or "").strip().split("\n", 1)[0]
    line = re.sub(r'^[\s"\'“”«»„‚`´’\(\)\[\]{}·•…–—\-]+', "", line)
    for delim in (",", "—", "–", ":", ".", "!", "?", ";"):
        idx = line.find(delim)
        if idx >= 2:
            line = line[:idx]
            break
    line = line.strip().lower()
    line = re.sub(r"[^\wа-яё]+", " ", line, flags=re.UNICODE)
    line = re.sub(r"\s+", " ", line).strip()
    if not line:
        return ""
    words = line.split()
    return " ".join(words[:_OPENER_PREFIX_MAX_WORDS])


def _is_banned_opener_prefix(prefix: str) -> bool:
    if not prefix:
        return False
    for banned in BANNED_OPENERS_PREFIXES:
        if prefix == banned or prefix.startswith(banned + " "):
            return True
    return False


def _is_repeat_opener_prefix(prefix: str, seen_prefixes: deque) -> bool:
    if not prefix or not seen_prefixes:
        return False
    tail = list(seen_prefixes)[-_OPENER_PREFIX_REPEAT_WINDOW :]
    return prefix in tail


def _strip_banned_prefix(text: str) -> str:
    if not text:
        return text
    prefix = normalize_opener_prefix(text)
    if not _is_banned_opener_prefix(prefix):
        return text
    for delim in (",", "—", "–", ":", ".", "!", "?", "\n", ";"):
        idx = text.find(delim)
        if idx >= 0:
            return text[idx + 1 :].lstrip()
    return text

# ========== AUTO-LOGGING В БД (bot_messages) ==========
class LogIncomingMiddleware(BaseMiddleware):
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


async def _log_message_by_tg(tg_id: int, role: str, text_: str) -> None:
    try:
        mode = (await _db_get_privacy(int(tg_id)) or "insights").lower()
        uid = await _ensure_user_id(int(tg_id))
        safe = (text_ or "")[:4000]
        if not safe:
            return
        r = (role or "").lower()
        role_norm = "user" if r == "user" else "bot"

        if mode == "none":
            _buf_push(int(tg_id), role_norm, safe)
            return

        async with async_session() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO bot_messages (user_id, role, text, created_at)
                    VALUES (:u, :r, :t, CURRENT_TIMESTAMP)
                """
                ),
                {"u": int(uid), "r": role_norm, "t": safe},
            )
            await s.commit()

        _buf_push(int(tg_id), role_norm, safe)
    except Exception as e:
        print("[log-db] error:", repr(e))


async def send_and_log(message: Message, text_: str, **kwargs):
    kwargs.setdefault("disable_web_page_preview", True)
    sent = await message.answer(text_, **kwargs)
    try:
        await _log_message_by_tg(message.from_user.id, "bot", text_)
    except Exception as e:
        print("[send-log] error:", repr(e))
    return sent


# ===== /start с paid_* deeplink =====
@router.message(F.text.regexp(r"^/start\s+paid_(ok|canceled|fail)$"))
async def on_start_payment_deeplink(m: Message):
    payload = (m.text or "").split(maxsplit=1)[1].strip().lower()

    if payload == "paid_ok":
        await m.answer(
            "Оплата прошла ✅\nДоступ активирован. Можно продолжать — выбери «Поговорить» или смотри другие разделы в мини-приложении.",
            reply_markup=kb_main_menu(),
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:open")],
            [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
        ]
    )
    await m.answer(
        "Похоже, оплата не завершилась или была отменена.\nМожно попробовать ещё раз — это безопасно и займёт минуту.",
        reply_markup=kb,
    )


# === /start <payload> (deeplink из рекламы и служебные) ===
@router.message(F.text.regexp(r"^/start(\s+.+)?$"))
async def on_start_with_payload(m: Message):
    from sqlalchemy import text as _sql
    import json

    raw = (m.text or "").strip()
    parts = raw.split(maxsplit=1)
    payload = (parts[1] if len(parts) > 1 else "").strip()
    if payload.lower().startswith("paid_"):
        return

    pl = payload.lower()
    if pl == "talk":
        CHAT_MODE[m.chat.id] = "talk"
        try:
            await m.answer("Я рядом. Можем поговорить — напиши, что сейчас волнует 💬")
        except Exception:
            pass
        return
    if pl == "miniapp":
        try:
            if MINIAPP_URL:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Открыть мини-приложение",
                                web_app=WebAppInfo(url=MINIAPP_URL),
                            )
                        ],
                        [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
                    ]
                )
                await m.answer(
                    "Открой мини-приложение, там «Упражнения» и «Медитации».", reply_markup=kb
                )
            else:
                await m.answer("Ссылка на мини-приложение не настроена. Укажи MINIAPP_URL в ENV.")
        except Exception:
            pass
        return

    saved = False
    if payload:
        try:
            ad_code = payload[:3].upper() if len(payload) >= 3 else None
            async with async_session() as s:
                ad_id = None
                if ad_code and ad_code.isalnum():
                    r = await s.execute(
                        _sql("SELECT id FROM ads WHERE code = :c LIMIT 1"), {"c": ad_code}
                    )
                    row = r.first()
                    ad_id = int(row[0]) if row else None

                raw_j = {
                    "text": m.text,
                    "date": getattr(m, "date", None).isoformat()
                    if getattr(m, "date", None)
                    else None,
                    "chat_id": m.chat.id,
                }
                await s.execute(
                    _sql(
                        """
                    INSERT INTO ad_starts (ad_id, start_code, tg_user_id, username, first_name, ref_channel, raw_payload, created_at)
                    VALUES (:ad_id, :code, :tg, :un, :fn, NULL, :raw, NOW())
                """
                    ),
                    {
                        "ad_id": ad_id,
                        "code": payload,
                        "tg": int(m.from_user.id),
                        "un": getattr(m.from_user, "username", None),
                        "fn": getattr(m.from_user, "first_name", None),
                        "raw": json.dumps(raw_j, ensure_ascii=False),
                    },
                )
                await s.commit()
                saved = True
        except Exception as e:
            print("[ads] start tracking error:", repr(e))

    CHAT_MODE[m.chat.id] = "talk"
    img = get_onb_image("cover")
    prefix = "Welcome!💛\n\n" if saved else ""
    if img:
        try:
            await m.answer_photo(img, caption=prefix + ONB_1_TEXT, reply_markup=kb_onb_step1())
            return
        except Exception:
            pass
    await m.answer(prefix + ONB_1_TEXT, reply_markup=kb_onb_step1())


# ===== async DB helpers / privacy / history =====
async def _ensure_user_id(tg_id: int) -> int:
    async with async_session() as s:
        r = await s.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        uid = r.scalar()
        if uid is None:
            r = await s.execute(
                text(
                    """
                    INSERT INTO users (tg_id, privacy_level, style_profile, created_at)
                    VALUES (:tg, 'ask', 'default', NOW())
                    RETURNING id
                """
                ),
                {"tg": int(tg_id)},
            )
            uid = r.scalar_one()
            await s.commit()
        return int(uid)


from sqlalchemy import text as _t


async def _load_history_from_db(
    tg_id: int, *, limit: int = 120, hours: int = 24 * 30
) -> list[dict]:
    uid = await _ensure_user_id(tg_id)
    try:
        mode = (await _db_get_privacy(int(tg_id)) or "insights").lower()
    except Exception:
        mode = "insights"

    if mode == "none":
        buf = get_recent_messages(int(tg_id), limit=min(limit, 120)) or []
        out: list[dict] = []
        for r in buf:
            role = "assistant" if (r.get("role") == "bot") else "user"
            out.append({"role": role, "content": r.get("text") or ""})
        return out

    async with async_session() as s:
        rows = (
            await s.execute(
                _t(
                    """
                SELECT id, role, text, created_at
                FROM bot_messages
                WHERE user_id = :uid
                  AND created_at >= NOW() - (:hours::text || ' hours')::interval
                ORDER BY id ASC
                LIMIT :lim
            """
                ),
                {"uid": int(uid), "hours": int(hours), "lim": int(limit)},
            )
        ).mappings().all()

    msgs: list[dict] = []
    seen_ids: set[int] = set()
    seen_keys: set[tuple] = set()
    for r in rows:
        try:
            mid = int(r.get("id"))
        except Exception:
            mid = None
        if mid is not None:
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
        role = "assistant" if (r["role"] or "").lower() == "bot" else "user"
        content = r["text"] or ""
        key = (role, content, r.get("created_at"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        msgs.append({"role": role, "content": content})

    try:
        tail_raw = get_recent_messages(int(tg_id), limit=10) or []
        if tail_raw:
            seen = {(m["role"], m["content"], None) for m in msgs}
            for r in tail_raw:
                role = "assistant" if (r.get("role") == "bot") else "user"
                content = r.get("text") or ""
                key = (role, content, r.get("created_at"))
                if key not in seen:
                    msgs.append({"role": role, "content": content})
                    seen.add(key)
    except Exception:
        pass
    return msgs


async def _db_get_privacy(tg_id: int) -> str:
    async with async_session() as s:
        r = await s.execute(
            text("SELECT privacy_level FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)}
        )
        val = r.scalar()
    return (val or "insights")


async def _db_set_privacy(tg_id: int, mode: str) -> None:
    async with async_session() as s:
        await s.execute(
            text("UPDATE users SET privacy_level = :m WHERE tg_id = :tg"),
            {"m": mode, "tg": int(tg_id)},
        )
        await s.commit()


async def _purge_user_history(tg_id: int) -> int:
    deleted = 0
    try:
        async with async_session() as s:
            r = await s.execute(
                text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)}
            )
            uid = r.scalar()
            if uid:
                res = await s.execute(
                    text("DELETE FROM bot_messages WHERE user_id = :u"), {"u": int(uid)}
                )
                await s.commit()
                try:
                    deleted = int(getattr(res, "rowcount", 0) or 0)
                except Exception:
                    deleted = 0
    except Exception:
        deleted = 0
    RECENT_BUFFER.pop(int(tg_id), None)
    return deleted


# --- Memory Q hook («что мы говорили X назад?») ---
_TIME_NUM = re.compile(r"(\d+)")


def _pick_window(txt: str) -> tuple[int, int, int, int]:
    t = (txt or "").lower()
    mins = hours = days = weeks = 0
    if "недавн" in t:
        hours = 3
    elif "мин" in t or "мину" in t:
        m = _TIME_NUM.search(t)
        mins = int(m.group(1)) if m else 10
    elif "час" in t:
        m = _TIME_NUM.search(t)
        hours = int(m.group(1)) if m else 3
    elif "дн" in t:
        m = _TIME_NUM.search(t)
        days = int(m.group(1)) if m else 1
    elif "недел" in t:
        m = _TIME_NUM.search(t)
        weeks = int(m.group(1)) if m else 1
    else:
        mins = 10
    return mins, hours, days, weeks


def _looks_like_memory_question(txt: str) -> bool:
    t = (txt or "").lower()
    keys = [
        "помнишь",
        "вспомни",
        "что мы говорили",
        "о чем мы говорили",
        "о чём мы говорили",
        "что я говорил",
        "что я писал",
        "что я спрашивал",
        "что было раньше",
        "мы обсуждали",
        "мин назад",
        "час назад",
        "день назад",
        "неделю назад",
        "вчера",
        "сегодня",
        "прошлый раз",
        "последний раз",
        "недавно",
    ]
    syn = ["разговаривали", "общались", "переписывались", "болтали"]
    if any(k in t for k in keys):
        return True
    if ("о чем" in t or "о чём" in t) and any(s in t for s in syn):
        return True
    if "не помнишь" in t and (
        ("о чем" in t) or ("о чём" in t) or ("что было" in t)
    ):
        return True
    return False


async def _maybe_answer_memory_question(m: Message, user_text: str) -> bool:
    if not _looks_like_memory_question(user_text):
        return False

    uid = await _ensure_user_id(m.from_user.id)
    mins, h, d, w = _pick_window(user_text)
    total_minutes = mins + h * 60 + d * 24 * 60 + w * 7 * 24 * 60
    if total_minutes <= 0:
        total_minutes = 10
    interval_txt = f"{total_minutes} minutes"

    async with async_session() as s:
        rows = (
            await s.execute(
                text(
                    """
            SELECT role, text, created_at
            FROM bot_messages
            WHERE user_id = :uid
              AND created_at >= NOW() - (:ival::text)::interval
            ORDER BY id ASC
            LIMIT 120
        """
                ),
                {"uid": int(uid), "ival": interval_txt},
            )
        ).mappings().all()

    if not rows:
        await send_and_log(
            m,
            "За этот промежуток ничего не вижу в истории. Подскажи тему — подхвачу.",
            reply_markup=kb_main_menu(),
        )
        return True

    def _short(s: str, n: int = 220) -> str:
        s = (s or "").strip().replace("\n", " ")
        return s if len(s) <= n else s[: n - 1] + "…"

    parts = []
    for r in rows[-14:]:
        who = "ты" if (r["role"] or "").lower() == "user" else "я"
        when = _fmt_dt(r["created_at"])
        parts.append(f"{when} — {who}: {_short(r['text'])}")

    header = "Коротко, что было в недавнем разговоре:\n"
    body = "\n".join(parts)
    tail = "\n\nПродолжим с этого места или поменяем тему?"
    await send_and_log(m, header + body + tail, reply_markup=kb_main_menu())
    return True


# --- Summaries helpers ---
from sqlalchemy import text as _sql_text


async def _fetch_summary_texts_by_ids(ids: List[int]) -> List[dict]:
    if not ids:
        return []
    async with async_session() as s:
        rows = (
            await s.execute(
                _sql_text(
                    """
            SELECT id, kind, period_start, period_end, text
            FROM dialog_summaries
            WHERE id = ANY(:ids)
        """
                ),
                {"ids": ids},
            )
        ).mappings().all()
    by_id = {r["id"]: r for r in rows}
    out: List[dict] = []
    for i in ids:
        r = by_id.get(i)
        if not r:
            continue
        out.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "period": f"{_fmt_dt(r['period_start'])} — {_fmt_dt(r['period_end'])}",
                "text": r["text"],
            }
        )
    return out


async def _purge_user_summaries_all(tg_id: int) -> int:
    async with async_session() as s:
        r = await s.execute(
            _sql_text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)}
        )
        uid = r.scalar()
        if not uid:
            return 0
        try:
            await delete_user_summaries(int(uid))
        except Exception:
            pass
        res = await s.execute(
            _sql_text("DELETE FROM dialog_summaries WHERE user_id = :uid"),
            {"uid": int(uid)},
        )
        await s.commit()
        try:
            return int(getattr(res, "rowcount", 0) or 0)
        except Exception:
            return 0


# ===== Онбординг: ссылки и картинки =====
POLICY_URL = os.getenv("POLICY_URL", "").strip()
TERMS_URL = os.getenv("TERMS_URL", "").strip()
MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip()  # <<< ДОБАВЛЕНО

def _legal_urls() -> tuple[str, str]:
    legal_policy = (os.getenv("LEGAL_POLICY_URL", "") or "").strip() or POLICY_URL
    legal_offer = (os.getenv("LEGAL_OFFER_URL", "") or "").strip() or TERMS_URL
    return legal_policy, legal_offer

DEFAULT_ONB_IMAGES = {
    "cover": os.getenv(
        "ONB_IMG_COVER",
        "https://file.garden/aML3M6Sqrg21TaIT/kind-creature-min.jpg",
    ),
    "talk": os.getenv(
        "ONB_IMG_TALK",
        "https://file.garden/aML3M6Sqrg21TaIT/warm-conversation-min.jpg",
    ),
}


def get_onb_image(key: str) -> str:
    return DEFAULT_ONB_IMAGES.get(key, "") or ""


# ===== Глобальные состояния чата =====
CHAT_MODE: Dict[int, str] = {}  # "talk" | "reflection"
USER_TONE: Dict[int, str] = {}  # "default" | "friend" | "therapist" | "18plus"

# ===== Talk debounce buffer =====
def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "")
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)

TALK_DEBOUNCE_SEC = _env_float("TALK_DEBOUNCE_SEC", 0.9)
TALK_DEBOUNCE_SHORT_SEC = _env_float("TALK_DEBOUNCE_SHORT_SEC", 1.1)
TALK_DEBOUNCE_MAX_WAIT_SEC = _env_float("TALK_DEBOUNCE_MAX_WAIT_SEC", 12.0)
TALK_TYPING_INTERVAL_SEC = 2.0
SOFT_QUESTIONS_IN_TALK = os.getenv("SOFT_QUESTIONS_IN_TALK", "1") == "1"
TALK_MAX_QUESTIONS = int(os.getenv("TALK_MAX_QUESTIONS", "2") or "2")

TALK_DEBOUNCE_BUFFER: Dict[int, Dict[str, Any]] = {}

def _debounce_stats(texts: List[str]) -> tuple[int, int]:
    parts = len(texts)
    chars = sum(len(t or "") for t in texts)
    return parts, chars

def _text_hint(text: str, limit: int = 40) -> str:
    t = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if len(t) > limit:
        t = t[:limit] + "…"
    h = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]
    return f"{t}#{h}"

async def _typing_loop(bot, chat_id: int) -> None:
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            await asyncio.sleep(TALK_TYPING_INTERVAL_SEC)
    except asyncio.CancelledError:
        return

async def _debounce_wait_and_flush(key: int, delay: float) -> None:
    try:
        await asyncio.sleep(max(0.0, float(delay)))
        await _flush_talk_buffer(key, reason="debounce")
    except asyncio.CancelledError:
        return

async def _enqueue_talk_message(m: Message, text: str, *, handler=None) -> None:
    if not text:
        return
    tg_id = int(getattr(getattr(m, "from_user", None), "id", 0) or 0)
    chat_id = int(getattr(getattr(m, "chat", None), "id", 0) or 0)
    if not tg_id or not chat_id:
        return

    now = time.monotonic()
    buf = TALK_DEBOUNCE_BUFFER.get(tg_id)
    if not buf:
        buf = {
            "texts": [],
            "timer_task": None,
            "typing_task": None,
            "last_msg_id": None,
            "chat_id": chat_id,
            "started_at": now,
            "message": m,
            "flush_handler": handler,
        }
        TALK_DEBOUNCE_BUFFER[tg_id] = buf

        bot = getattr(m, "bot", None)
        if bot is not None:
            buf["typing_task"] = asyncio.create_task(_typing_loop(bot, chat_id))

    buf["texts"].append(text)
    buf["last_msg_id"] = getattr(m, "message_id", None)
    buf["message"] = m
    if handler is not None:
        buf["flush_handler"] = handler

    parts, chars = _debounce_stats(buf["texts"])
    logger.debug("[debounce] enqueue parts=%s chars=%s", parts, chars)

    elapsed = now - float(buf.get("started_at") or now)
    if elapsed >= TALK_DEBOUNCE_MAX_WAIT_SEC:
        logger.info("[debounce] max_wait reached parts=%s chars=%s", parts, chars)
        task = buf.get("timer_task")
        if task and not task.done():
            task.cancel()
        await _flush_talk_buffer(tg_id, reason="max_wait")
        return

    delay = TALK_DEBOUNCE_SEC
    if is_short_reply(text):
        delay = max(delay, TALK_DEBOUNCE_SHORT_SEC)

    task = buf.get("timer_task")
    if task and not task.done():
        task.cancel()
    buf["timer_task"] = asyncio.create_task(_debounce_wait_and_flush(tg_id, delay))

async def _flush_talk_buffer(key: int, *, reason: str = "manual", handler=None) -> None:
    buf = TALK_DEBOUNCE_BUFFER.get(key)
    if not buf:
        return

    timer_task = buf.get("timer_task")
    if timer_task and timer_task is not asyncio.current_task() and not timer_task.done():
        timer_task.cancel()
    typing_task = buf.get("typing_task")
    if typing_task and not typing_task.done():
        typing_task.cancel()

    texts = list(buf.get("texts") or [])
    message = buf.get("message")
    buffer_handler = handler or buf.get("flush_handler")
    TALK_DEBOUNCE_BUFFER.pop(key, None)

    if not texts or message is None:
        return

    parts, chars = _debounce_stats(texts)
    combined = "\n".join(texts)
    logger.info(
        "[debounce] merge reason=%s parts=%s final_len=%s hint=%s",
        reason,
        parts,
        len(combined),
        _text_hint(combined),
    )

    try:
        if buffer_handler is not None:
            await buffer_handler(message, combined)
        else:
            await _answer_with_llm(message, combined)
    except asyncio.CancelledError:
        return
    except Exception:
        return

# ===== Эфемерный буфер (priv=none) =====
RECENT_BUFFER: Dict[int, deque] = {}
BUFFER_MAX = 120


def _buf_push(chat_id: int, role: str, text_: str) -> None:
    if not chat_id or not text_:
        return
    q = RECENT_BUFFER.get(chat_id)
    if q is None:
        q = deque(maxlen=BUFFER_MAX)
        RECENT_BUFFER[chat_id] = q
    role_norm = "assistant" if (role or "").lower() == "bot" else "user"
    q.append({"role": role_norm, "content": (text_ or "").strip()})


def _buf_get(chat_id: int, limit: int = 90) -> List[dict]:
    q = RECENT_BUFFER.get(chat_id)
    if not q:
        return []
    return list(q)[-int(limit) :]


# --- paywall helpers ---
async def _get_user_by_tg(session, tg_id: int):
    from app.db.models import User

    q = await session.execute(select(User).where(User.tg_id == tg_id))
    return q.scalar_one_or_none()


def _kb_paywall(_: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")]
        ]
    )


async def _enforce_access_or_paywall(msg_or_call, session, user_id: int) -> bool:
    if await check_access(session, user_id):
        return True
    if await is_trial_active(session, user_id):
        return True
    text_ = (
        "Доступ к разделу открыт по подписке.\n"
        "Оформи любой план — отменить автопродление можно в /pay в любой момент."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")],
        ]
    )
    if isinstance(msg_or_call, Message):
        await msg_or_call.answer(text_, reply_markup=kb)
    else:
        await msg_or_call.message.answer(text_, reply_markup=kb)
    return False


# --- pay status helpers ---
async def _access_status_text(session, user_id: int) -> str | None:
    try:
        state = await get_access_state(session, user_id)
    except Exception:
        state = None
    if not state:
        return None
    if state.get("reason") == "subscription":
        return "Подписка активна ✅\nДоступ ко всем функциям открыт."
    if state.get("reason") == "trial":
        until = state.get("trial_until")
        tail = f" до {_fmt_dt(until)}" if until else ""
        return f"Пробный период активен{tail} ✅\nДоступ ко всем функциям открыт."
    return None


# --- локализация времени ---
_TZ = ZoneInfo(os.getenv("BOT_TZ", "Europe/Moscow"))


def _fmt_dt(dt) -> str:
    try:
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(_TZ).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt)


_TRIAL_INTERVAL_SQL = f"INTERVAL '{int(TRIAL_DAYS)} days'"


# === Length hint picker + «один вопрос» ===
def _pick_len_hint(user_text: str, mode: str) -> str:
    t = (user_text or "").lower()
    deep_keywords = (
        "подроб",
        "деталь",
        "развернут",
        "план",
        "структур",
        "пошаг",
        "инструкц",
    )
    if any(k in t for k in deep_keywords):
        return "deep"
    n = len(t.strip())
    if n <= 50:
        return "micro"
    if n <= 220:
        return "short"
    if mode == "reflection":
        return "medium"
    return "medium"


def _count_questions(text: str) -> int:
    return (text or "").count("?")


def _limit_questions(text: str, max_questions: int) -> str:
    if not text:
        return text
    while "??" in text:
        text = text.replace("??", "?")
    if max_questions <= 0:
        return text.replace("?", ".").replace(" .", ".").replace(" ,", ",")
    q_positions = [i for i, ch in enumerate(text) if ch == "?"]
    if len(q_positions) <= max_questions:
        return text
    keep = set(q_positions[-max_questions:])
    chars = list(text)
    for i in q_positions:
        if i not in keep:
            chars[i] = "."
    out = "".join(chars)
    return out.replace(" .", ".").replace(" ,", ",")


def _postprocess_questions(text: str, mode: str) -> str:
    if mode == "talk" and SOFT_QUESTIONS_IN_TALK:
        before = _count_questions(text)
        max_q = max(0, int(TALK_MAX_QUESTIONS))
        out = _limit_questions(text, max_q)
        after = _count_questions(out)
        try:
            print(f"[post] questions_mode=talk_soft max={max_q} applied={before}->{after}")
        except Exception:
            pass
        return out
    before = _count_questions(text)
    out = _limit_questions(text, 1)
    after = _count_questions(out)
    try:
        print(f"[post] questions_mode=strict max=1 applied={before}->{after}")
    except Exception:
        pass
    return out


async def _get_active_subscription(session, user_id: int):
    row = await session.execute(
        text(
            """
        SELECT id, subscription_until, COALESCE(is_auto_renew, true) AS is_auto_renew
        FROM subscriptions
        WHERE user_id = :uid AND status = 'active'
        ORDER BY subscription_until DESC
        LIMIT 1
    """
        ),
        {"uid": user_id},
    )
    return row.mappings().first()


def _kb_trial_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")],
            [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
        ]
    )


def _kb_active_sub_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить подписку ❌", callback_data="sub:cancel")],
            [
                InlineKeyboardButton(
                    text="Отключить автопродление ⏹", callback_data="sub:auto_off"
                )
            ],
        ]
    )


def _kb_confirm(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, подтвердить", callback_data=f"sub:{action}:yes"
                ),
                InlineKeyboardButton(text="Назад", callback_data="sub:cancel_back"),
            ],
        ]
    )


@router.callback_query(lambda c: c.data == "sub:cancel_back")
async def cb_sub_cancel_back(call: CallbackQuery):
    await on_pay(call.message)
    await call.answer()


# ===== safe_edit =====
async def _safe_edit(
    msg: Message,
    text: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    try:
        if text is not None and reply_markup is not None:
            await msg.edit_text(
                text, reply_markup=reply_markup, disable_web_page_preview=True
            )
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


# ===== Правое меню (ровно 4 кнопки) =====
def kb_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💬 Поговорить")],
            [KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="💳 Подписка")],
            [KeyboardButton(text="ℹ️ О проекте")],
        ],
        resize_keyboard=True,
    )


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
            [
                InlineKeyboardButton(
                    text="✨ Универсальный (по умолчанию)", callback_data="tone:default"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤝 Друг/подруга", callback_data="tone:friend"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧠 Психологичный", callback_data="tone:therapist"
                )
            ],
            [InlineKeyboardButton(text="🌶️ 18+", callback_data="tone:18plus")],
        ]
    )


async def kb_privacy_for(chat_id: int) -> InlineKeyboardMarkup:
    try:
        mode = (await _db_get_privacy(chat_id) or "insights").lower()
    except Exception:
        mode = "insights"
    save_on = mode != "none"
    toggle_text = "🔔 Вкл. хранение" if not save_on else "🔕 Выкл. хранение"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="privacy:toggle")],
            [
                InlineKeyboardButton(
                    text="🗑 Очистить историю", callback_data="privacy:clear"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings")],
        ]
    )


@router.callback_query(lambda c: c.data in ("pay:open", "pay:plans"))
async def cb_pay_open_or_plans(call: CallbackQuery):
    try:
        async for session in get_session():
            from app.db.models import User

            u = (
                await session.execute(
                    select(User).where(User.tg_id == call.from_user.id)
                )
            ).scalar_one_or_none()
        trial_ever = getattr(u, "trial_started_at", None) is not None if u else False
    except Exception:
        trial_ever = False

    await call.message.answer(
        _pay_plans_text(trial_ever_started=trial_ever),
        reply_markup=_kb_pay_plans(),
        parse_mode="HTML",
    )
    await call.answer()


# --- автопродление / отмена подписки ---
@router.callback_query(lambda c: c.data == "sub:auto_off")
async def cb_sub_auto_off(call: CallbackQuery):
    async for session in get_session():
        from app.db.models import User

        u = (
            await session.execute(
                select(User).where(User.tg_id == call.from_user.id)
            )
        ).scalar_one_or_none()
        if not u:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        sub = await get_active_subscription_row(session, u.id)

    if not sub:
        await call.answer("Активной подписки нет.", show_alert=True)
        return

    until_str = _fmt_dt(sub["subscription_until"])
    await _safe_edit(
        call.message,
        text=(
            "Отключить автопродление?\nТекущий доступ останется до"
            f" <b>{until_str}</b>, дальше продлений не будет."
        ),
        reply_markup=_kb_confirm("auto_off"),
    )
    await call.answer()


@router.callback_query(lambda c: c.data == "sub:auto_off:yes")
async def cb_sub_auto_off_yes(call: CallbackQuery):
    async for session in get_session():
        from app.db.models import User

        u = (
            await session.execute(
                select(User).where(User.tg_id == call.from_user.id)
            )
        ).scalar_one_or_none()
        if not u:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        changed, until = await disable_auto_renew(session, u.id)

    if not changed:
        await _safe_edit(
            call.message,
            text="Автопродление уже было отключено ⏹",
            reply_markup=_kb_active_sub_actions(),
        )
        await call.answer()
        return

    until_str = _fmt_dt(until) if until else "конца периода"
    await _safe_edit(
        call.message,
        text=f"Автопродление отключено ⏹\nПодписка останется активной до {until_str}.",
        reply_markup=_kb_active_sub_actions(),
    )
    await call.answer()


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

        u = (
            await session.execute(
                select(User).where(User.tg_id == call.from_user.id)
            )
        ).scalar_one_or_none()
        if not u:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        ok = await cancel_subscription_now(session, u.id)

    if not ok:
        await _safe_edit(
            call.message,
            text="Активной подписки не найдено.",
            reply_markup=_kb_pay_plans(),
        )
        await call.answer()
        return

    await _safe_edit(
        call.message,
        text=(
            "Подписка отменена ❌\nЕсли захочешь вернуться — оформи новую в разделе"
            " /pay."
        ),
        reply_markup=_kb_pay_plans(),
    )
    await call.answer()


# ===== /policy =====
@router.message(Command("policy"))
async def cmd_policy(m: Message):
    parts = ["🔒 <b>Политика и правила</b>"]
    policy_url, offer_url = _legal_urls()
    if policy_url:
        parts.append(f"• <a href='{policy_url}'>Правила сервиса</a>")
    if policy_url:
        parts.append(f"• <a href='{policy_url}'>Политика конфиденциальности</a>")
    if not offer_url and not policy_url:
        parts.append(
            "Ссылки не настроены. Добавь переменные окружения POLICY_URL и TERMS_URL."
        )
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
    policy_url, offer_url = _legal_urls()
    if offer_url:
        link_row.append(InlineKeyboardButton(text="📄 Правила", url=offer_url))
    if policy_url:
        link_row.append(InlineKeyboardButton(text="🔐 Политика", url=policy_url))
    if link_row:
        rows.append(link_row)
    rows.append([InlineKeyboardButton(text="Принимаю ✅", callback_data="onb:agree")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


WHAT_NEXT_TEXT = """С чего начнём? 💛

💬 «Поговорить» — выговориться, навести ясность и наметить маленький шаг. 
💫 "Открыть" Mini App — упражнения и медитации в удобном приложении.

💡 Совет: Я буду лучше понимать, если писать мысль целиком в одном сообщении💛

<b>5 дней бесплатно</b> — пробная версия запустится после нажатия на любую кнопку или команду. После — можно выбрать удобный план."""



def kb_onb_step3() -> InlineKeyboardMarkup:
    """Кнопки финального шага онбординга: Mini App + fallback."""
    if MINIAPP_URL:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Открыть мини-приложение", web_app=WebAppInfo(url=MINIAPP_URL)
                    )
                ],
                [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
            ]
        )
    # Fallback, если MINIAPP_URL не задан
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")]]
    )


PAYWALL_POST_TEXT = (
    "Хочу продолжить помогать, но для этого нужна подписка.\n"
    "Оформи её в /pay и получи полный доступ ко всем функциям.\n\n"
    "🔒 Приватно и бережно, без оценок; историю можно очистить в /privacy.\n"
)


@router.callback_query(F.data == "onb:step2")
async def on_onb_step2(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        policy_ok, _, _ = await _gate_user_flags(int(cb.from_user.id))
    except Exception:
        policy_ok = False
    if not policy_ok:
        try:
            await cb.message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass
    await cb.message.answer(ONB_2_TEXT, reply_markup=kb_onb_step2())


@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    tg_id = cb.from_user.id
    uid = await _ensure_user_id(tg_id)
    try:
        async with async_session() as s:
            await s.execute(
                text(
                    "UPDATE users SET policy_accepted_at = CURRENT_TIMESTAMP WHERE id = :uid"
                ),
                {"uid": uid},
            )
            await s.commit()
    except Exception:
        pass
    try:
        await cb.answer("Спасибо! Принял ✅", show_alert=False)
    except Exception:
        pass
    # <<< ЗДЕСЬ МЕНЯЕМ КНОПКИ ШАГА 3
    await cb.message.answer(WHAT_NEXT_TEXT, reply_markup=kb_onb_step3())


# ===== Меню / навигация (правое меню) =====
@router.callback_query(F.data == "menu:main")
async def on_menu_main(cb: CallbackQuery):
    await cb.message.answer("Открываю меню", reply_markup=kb_main_menu())
    await cb.answer()


@router.message(F.text == "⚙️ Настройки")
@router.message(Command("settings"))
@router.message(Command("setting"))
async def on_settings(m: Message):
    async for session in get_session():
        u = await _get_user_by_tg(session, m.from_user.id)
        if not u or not await _enforce_access_or_paywall(m, session, u.id):
            return
    await m.answer("Настройки:", reply_markup=kb_settings())


@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(cb: CallbackQuery):
    async for session in get_session():
        u = await _get_user_by_tg(session, cb.from_user.id)
        if not u or not await _enforce_access_or_paywall(cb, session, u.id):
            return
    await _safe_edit(cb.message, "Настройки:", reply_markup=kb_settings())
    await cb.answer()


@router.callback_query(F.data == "settings:tone")
async def on_settings_tone(cb: CallbackQuery):
    async for session in get_session():
        u = await _get_user_by_tg(session, cb.from_user.id)
        if not u or not await _enforce_access_or_paywall(cb, session, u.id):
            return
    await _safe_edit(cb.message, "Выбери тон общения:", reply_markup=kb_tone_picker())
    await cb.answer()


@router.callback_query(F.data == "settings:privacy")
async def on_settings_privacy(cb: CallbackQuery):
    async for session in get_session():
        u = await _get_user_by_tg(session, cb.from_user.id)
        if (not u) or (not await _enforce_access_or_paywall(cb, session, u.id)):
            return
    rm = await kb_privacy_for(cb.message.chat.id)
    await _safe_edit(cb.message, "Приватность:", reply_markup=rm)
    await cb.answer()


@router.callback_query(F.data == "privacy:toggle")
async def on_privacy_toggle(cb: CallbackQuery):
    async for session in get_session():
        u = await _get_user_by_tg(session, cb.from_user.id)
        if not u or not await _enforce_access_or_paywall(cb, session, u.id):
            return
    chat_id = cb.message.chat.id
    mode = (await _db_get_privacy(chat_id) or "insights").lower()
    new_mode = "none" if mode != "none" else "insights"
    await _db_set_privacy(chat_id, new_mode)
    state_txt = "выключено" if new_mode == "none" else "включено"
    rm = await kb_privacy_for(chat_id)
    await _safe_edit(
        cb.message,
        f"Хранение истории сейчас: <b>{state_txt}</b>.",
        reply_markup=rm,
    )
    await cb.answer("Настройка применена")


@router.callback_query(F.data == "privacy:clear")
async def on_privacy_clear(cb: CallbackQuery):
    async for session in get_session():
        u = await _get_user_by_tg(session, cb.from_user.id)
        if not u or not await _enforce_access_or_paywall(cb, session, u.id):
            return
    try:
        msg_count = await _purge_user_history(cb.from_user.id)
    except Exception:
        await cb.answer("Не получилось очистить историю", show_alert=True)
        return
    try:
        sum_count = await _purge_user_summaries_all(cb.from_user.id)
    except Exception:
        sum_count = 0
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
    async for session in get_session():
        u = await _get_user_by_tg(session, m.from_user.id)
        if not u or not await _enforce_access_or_paywall(m, session, u.id):
            return
    mode = (await _db_get_privacy(m.chat.id) or "insights").lower()
    state = "выключено" if mode == "none" else "включено"
    rm = await kb_privacy_for(m.chat.id)
    await m.answer(
        f"Хранение истории сейчас: <b>{state}</b>.", reply_markup=rm
    )


@router.message(Command("help"))
async def on_help(m: Message):
    await m.answer(
        "Если нужна помощь по сервису, напиши на selflect@proton.me — мы ответим."
    )


@router.message(Command("diag_llm"))
@router.message(Command("diag"))
async def on_diag_llm(m: Message):
    if not is_admin(m.from_user.id):
        return
    try:
        import importlib.metadata as md
        qdrant_ver = md.version("qdrant-client")
    except Exception:
        qdrant_ver = "n/a"
    try:
        from app.qdrant_client import get_client
        cli = get_client()
        qdrant_method = "search_points" if hasattr(cli, "search_points") else "search" if hasattr(cli, "search") else "unknown"
    except Exception as e:
        qdrant_method = f"error: {e}"
    env_state = {
        "CHAT_MODEL": os.getenv("CHAT_MODEL"),
        "CHAT_MODEL_TALK": os.getenv("CHAT_MODEL_TALK"),
        "CHAT_MODEL_STRONG": os.getenv("CHAT_MODEL_STRONG"),
        "LLM_FALLBACK_TO_DEFAULT": os.getenv("LLM_FALLBACK_TO_DEFAULT"),
    }
    lm = LAST_MEMORY_STATUS.copy()
    llm = LAST_LLM_STATUS.copy()
    msg = (
        "<b>/diag_llm</b>\n"
        f"qdrant-client: {qdrant_ver} (method: {qdrant_method})\n"
        f"env: {env_state}\n"
        f"last memory: ts={lm.get('ts')} src={lm.get('source')} err={lm.get('error')} summaries={lm.get('summaries_count')} qdrant_err={lm.get('qdrant_error')}\n"
        f"last llm: ts={llm.get('ts')} model={llm.get('meta', {}).get('model')} fallback={llm.get('meta', {}).get('fallback_used')} status={llm.get('meta', {}).get('status')} err={llm.get('error') or llm.get('meta', {}).get('error')}"
    )
    await m.answer(msg)


@router.message(Command("menu"))
async def on_menu(m: Message):
    msg = await m.answer("Меню", reply_markup=kb_main_menu())
    try:
        await msg.delete()
    except Exception:
        pass


# ===== Тон и «Поговорить» =====
@router.message(F.text == "💬 Поговорить")
@router.message(Command("talk"))
async def on_talk(m: Message):
    async for session in get_session():
        u = await _get_user_by_tg(session, m.from_user.id)
        if not u:
            return
        if not await _enforce_access_or_paywall(m, session, u.id):
            return
    CHAT_MODE[m.chat.id] = "talk"
    await m.answer(
        "Я рядом и слушаю. О чём хочется поговорить? Я буду понимать лучше, если писать мысль целиком в одном сообщении💛", reply_markup=kb_main_menu()
    )


@router.message(Command("tone"))
async def on_tone_cmd(m: Message):
    await m.answer("Выбери тон общения:", reply_markup=kb_tone_picker())


@router.callback_query(F.data.startswith("tone:"))
async def on_tone_pick(cb: CallbackQuery):
    style = cb.data.split(":", 1)[1]
    USER_TONE[cb.message.chat.id] = style
    await cb.answer("Стиль обновлён ✅", show_alert=False)
    await _safe_edit(
        cb.message,
        f"Тон общения установлен: <b>{style}</b> ✅",
        reply_markup=kb_settings(),
    )

# ============================
# LLM-ответы (как было)
# ============================

async def _answer_with_llm(m: Message, user_text: str):
    import random
    chat_id = m.chat.id
    mode = CHAT_MODE.get(chat_id, "talk")

    try:
        if await _maybe_answer_memory_question(m, user_text):
            return
    except Exception:
        pass

    style = USER_TONE.get(chat_id, "default")
    sys_prompt = SYSTEM_PROMPT
    tone_suffix = STYLE_SUFFIXES.get(style, "")
    if tone_suffix: sys_prompt += "\n\n" + tone_suffix
    if mode == "reflection" and REFLECTIVE_SUFFIX:
        sys_prompt += "\n\n" + REFLECTIVE_SUFFIX
    sys_prompt += "\n\nОтвечай строго на русском языке. Не используй иностранные слова и символы без явного запроса."

    t = (user_text or "").lower()
    need_deep = any(x in t for x in ["разложи подробно", "подробно", "план", "что делать по шагам", "структурируй", "инструкция"])
    need_medium = any(x in t for x in ["объясни", "поясни", "структурируй", "как это работает", "почему"])
    if need_deep:
        sys_prompt += "\n\n" + LENGTH_HINTS["deep"]
    elif need_medium:
        sys_prompt += "\n\n" + LENGTH_HINTS["medium"]
    else:
        sys_prompt += "\n\n" + LENGTH_HINTS["short"]

    history_msgs: List[dict] = []
    try:
        history_msgs = await _load_history_from_db(m.from_user.id, limit=90, hours=24*90)
    except Exception:
        try:
            recent = get_recent_messages(chat_id, limit=90)
            for r in recent:
                role = "assistant" if r["role"] == "bot" else "user"
                history_msgs.append({"role": role, "content": r["text"]})
        except Exception:
            history_msgs = []

    try:
        turn_idx = len(history_msgs)
    except Exception:
        turn_idx = 0
    length_key = _pick_len_hint(user_text, mode="reflection" if mode == "reflection" else "talk")
    len_hint = LENGTH_HINTS.get(length_key, "")
    if len_hint:
        sys_prompt += "\n\n" + len_hint

    rag_ctx = ""
    if rag_search is not None:
        try:
            qlen = len((user_text or "").split())
            k = 3 if qlen < 8 else 6 if qlen < 20 else 8
            max_chars = 600 if qlen < 8 else 1000 if qlen < 30 else 1400
            rag_ctx = await rag_search(user_text, k=k, max_chars=max_chars, lang=os.getenv("RAG_LANG", "ru"))
            _record_memory_status(error=None, source="rag_qdrant", summaries_count=0, qdrant_error=None)
        except Exception as e:
            print(f"[memory] rag_qdrant error: {e!r}")
            _record_memory_status(error=str(e), source="rag_qdrant", summaries_count=0, qdrant_error=str(e))
            rag_ctx = ""

    sum_block = ""
    try:
        uid = await _ensure_user_id(m.from_user.id)
        hits = await search_summaries(user_id=uid, query=user_text, top_k=4)
        ids = [int(h.get("summary_id")) for h in (hits or []) if str(h.get("summary_id", "")).isdigit()]
        items = await _fetch_summary_texts_by_ids(ids)
        if items:
            def _short(s: str, n: int = 260) -> str:
                s = (s or "").strip().replace("\r", " ").replace("\n", " ")
                return s if len(s) <= n else (s[: n - 1] + "…")
            lines = [f"• [{it['period']}] {_short(it.get('text', ''))}" for it in items]
            sum_text = "\n".join(lines).strip()
            MAX_SUMMARY_BLOCK = 900
            if len(sum_text) > MAX_SUMMARY_BLOCK:
                sum_text = sum_text[: MAX_SUMMARY_BLOCK - 1] + "…"
            sum_block = "Заметки из прошлых разговоров (учитывай по мере уместности):\n" + sum_text
        _record_memory_status(error=None, source="summaries", summaries_count=len(items), qdrant_error=None)
    except Exception as e:
        print(f"[memory] summaries error: {e!r}")
        _record_memory_status(error=str(e), source="summaries", summaries_count=0, qdrant_error=str(e))
        sum_block = ""

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    if rag_ctx:
        messages.append({"role": "system", "content": f"Материалы из базы знаний по теме:\n{rag_ctx}"})
    if sum_block:
        messages.append({"role": "system", "content": sum_block})
    messages += history_msgs

    last_bot_turn: Optional[str] = None
    try:
        for hm in reversed(history_msgs):
            if hm.get("role") == "assistant" and hm.get("content"):
                last_bot_turn = hm.get("content")
                break
    except Exception:
        last_bot_turn = None

    user_text_for_llm = user_text
    short_flag = is_short_reply(user_text)
    if short_flag:
        words = [w for w in (user_text or "").strip().split() if w]
        logger.info(
            "[short-reply] detected user_id=%s len=%s words=%s hint=%s",
            getattr(m.from_user, "id", None),
            len(user_text or ""),
            len(words),
            _text_hint(user_text),
        )
        user_text_for_llm = normalize_short_reply(user_text, last_bot_turn)
    else:
        logger.debug(
            "[short-reply] detected user_id=%s len=%s words=%s hint=%s",
            getattr(m.from_user, "id", None),
            len(user_text or ""),
            len((user_text or "").split()),
            _text_hint(user_text),
        )

    messages.append({"role": "user", "content": user_text_for_llm})

    if chat_with_style is None:
        await send_and_log(m, "Я тебя слышу. Сейчас подключаюсь…", reply_markup=kb_main_menu())
        return

    salt = getattr(m, "message_id", None) or getattr(m, "date", None) or ""
    seed = f"{user_text_for_llm}|{turn_idx}|{salt}"
    temp = 0.66 + (abs(hash(seed)) % 17) / 100.0  # 0.66–0.82
    LLM_MAX_TOKENS = 480
    trace_info: Dict[str, Any] = {"route": "talk", "mode": mode, "user_id": m.from_user.id}
    try:
        reply = await chat_with_style(
            messages=messages,
            temperature=temp,
            max_completion_tokens=LLM_MAX_TOKENS,
            mode="talk",
            trace=trace_info,
        )
    except TypeError:
        reply = await chat_with_style(messages, temperature=temp, max_completion_tokens=LLM_MAX_TOKENS, mode="talk", trace=trace_info)
    except Exception as e:
        err_txt = str(e)
        print(f"[llm] talk error: {e!r}")
        _record_llm_status(error=str(e), meta=trace_info)
        # Для HTTP 400 — не маскируем под обычный ответ
        if "HTTP 400" in err_txt:
            reply = "Не удалось обработать запрос (LLM 400). Попробуй переформулировать или убери лишние требования."
        else:
            reply = ""

    # если за вызов trace не заполнился (например, исключение до adapter), обновим сами
    if trace_info and not trace_info.get("status"):
        trace_info["status"] = "error" if not reply else "ok"
    _record_llm_status(error=None if reply else "empty", meta=trace_info)
    try:
        if trace_info:
            import logging  # Render highlights ERROR as red
            msg = f"[llm] route={trace_info.get('route')} model={trace_info.get('model')} fallback={trace_info.get('fallback_used')} status={trace_info.get('status')} latency_ms={trace_info.get('latency_ms')} err={trace_info.get('error')}"
            if trace_info.get("status") == "ok" and not trace_info.get("error"):
                logging.info(msg)
            else:
                logging.error(msg)
    except Exception:
        pass

    if not reply or not reply.strip():
        reply = _fallback_reply(user_text)
    try:
        reply = _postprocess_questions(reply, mode=mode)
    except Exception:
        pass

    # антиповтор первых строк: фиксируем опенер и при совпадении просим LLM начать иначе
    try:
        store = globals().setdefault("LAST_OPENERS", {})
        prefix_store = globals().setdefault("LAST_OPENER_PREFIXES", {})
        from collections import deque as _dq
        if chat_id not in store:
            store[chat_id] = _dq(maxlen=5)
        if chat_id not in prefix_store:
            prefix_store[chat_id] = _dq(maxlen=_OPENER_PREFIX_HISTORY)
        opener_key = extract_opener(reply)
        opener_prefix = normalize_opener_prefix(reply)
        seen = store.get(chat_id)
        seen_prefixes = prefix_store.get(chat_id)
        prefix_repeat = _is_repeat_opener_prefix(opener_prefix, seen_prefixes)
        prefix_banned = _is_banned_opener_prefix(opener_prefix)
        should_regen = (opener_key in seen) or prefix_repeat or prefix_banned
        if should_regen and chat_with_style is not None:
            try:
                print(f"[opener] guard hit prefix={opener_prefix!r} repeat={prefix_repeat} banned={prefix_banned}")
            except Exception:
                pass
            banned_list = list(dict.fromkeys(list(seen)))
            banned_text = "; ".join(banned_list)
            for attempt in range(2):
                sys_prompt_r = sys_prompt + "\n\n" + (
                    "Не начинай ответ с этих стартов: " + banned_text + ". "
                    "Не начинай с префикса: " + (opener_prefix or "<пусто>") + ". "
                    "Сделай другой старт и другой ритм, чем в прошлом ответе. "
                    "Запрещены междометия в начале: «Ох», «Понимаю», «Мне жаль», «Слышу тебя». "
                    "Начни иначе: (a) с короткого факта без междометий, (b) с уточнения, (c) с микро-резюме смысла пользователя."
                )
                messages_r: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt_r}]
                if rag_ctx:
                    messages_r.append({"role": "system", "content": f"Материалы из базы знаний по теме:\n{rag_ctx}"})
                if sum_block:
                    messages_r.append({"role": "system", "content": sum_block})
                messages_r += history_msgs
                messages_r.append({"role": "user", "content": user_text_for_llm})
                try:
                    reply_r = await chat_with_style(messages=messages_r, temperature=temp, max_completion_tokens=LLM_MAX_TOKENS)
                except TypeError:
                    reply_r = await chat_with_style(messages_r, temperature=temp, max_completion_tokens=LLM_MAX_TOKENS)
                except Exception:
                    reply_r = ""
                opener_after = extract_opener(reply_r)
                prefix_after = normalize_opener_prefix(reply_r)
                prefix_repeat_after = _is_repeat_opener_prefix(prefix_after, seen_prefixes)
                prefix_banned_after = _is_banned_opener_prefix(prefix_after)
                try:
                    print(f"[llm] opener-regen attempt={attempt+1} before={opener_key!r} after={opener_after!r}")
                except Exception:
                    pass
                if reply_r and reply_r.strip() and opener_after and opener_after not in seen and not prefix_repeat_after and not prefix_banned_after:
                    try:
                        reply = _postprocess_questions(reply_r, mode=mode)
                    except Exception:
                        reply = reply_r
                    opener_key = opener_after
                    opener_prefix = prefix_after
                    break
            if _is_banned_opener_prefix(opener_prefix):
                reply = _strip_banned_prefix(reply)
                opener_key = extract_opener(reply)
                opener_prefix = normalize_opener_prefix(reply)
        seen.append(opener_key)
        seen_prefixes.append(opener_prefix)
    except Exception:
        pass

    await send_and_log(m, reply, reply_markup=kb_main_menu())

# ===== Текстовые сообщения =====
@router.message(F.text == "💳 Подписка")
async def on_pay_btn(m: Message):
    await on_pay(m)


@router.message(F.text == "ℹ️ О проекте")
async def on_about_btn(m: Message):
    await cmd_about(m)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    chat_id = m.chat.id
    if m.text and is_subscription_intent(m.text):
        await m.answer(get_pay_help_text(), reply_markup=kb_main_menu())
        return
    if CHAT_MODE.get(chat_id, "talk") in ("talk", "reflection"):
        await _enqueue_talk_message(m, m.text or "")
        return
    await m.answer("Я рядом и на связи. Нажми «Поговорить».", reply_markup=kb_main_menu())


# === /pay — планы =========================================
from aiogram.filters import Command as _CmdPay

_PLAN_LABELS = {
    "week": "Подписка на 1 неделю",
    "month": "Подписка на 1 месяц",
    "quarter": "Подписка на 3 месяца",
    "year": "Подписка на 1 год",
}

_PLANS = {
    "week": (plan_price_int("week"), _PLAN_LABELS["week"]),
    "month": (plan_price_int("month"), _PLAN_LABELS["month"]),
    "quarter": (plan_price_int("quarter"), _PLAN_LABELS["quarter"]),
    "year": (plan_price_int("year"), _PLAN_LABELS["year"]),
}

# Цены в Telegram Stars (XTR), 1 единица = 1 звезда.
_STARS_PRICES = {
    "week": plan_price_stars("week"),
    "month": plan_price_stars("month"),
    "quarter": plan_price_stars("quarter"),
    "year": plan_price_stars("year"),
}


def _kb_pay_plans() -> _IKM:
    return _IKM(
        inline_keyboard=[
            [_IKB(text=f"Неделя — {plan_price_int('week')} ₽", callback_data="pay:plan:week")],
            [_IKB(text=f"Месяц — {plan_price_int('month')} ₽", callback_data="pay:plan:month")],
            [_IKB(text=f"3 месяца — {plan_price_int('quarter')} ₽", callback_data="pay:plan:quarter")],
            [_IKB(text=f"Год — {plan_price_int('year')} ₽", callback_data="pay:plan:year")],
        ]
    )


def _pay_plans_text(trial_ever_started: bool) -> str:
    head = "Подписка «Помни»\n• Все функции без ограничений\n"
    tail = (
        "⚠️ <i>Важно: подписка с автопродлением. Его можно отключить в любой момент в /pay.</i>\n\n"
        "<b>Выбери план:</b>"
    )
    if trial_ever_started:
        return f"{head}\n{tail}"
    else:
        return f"{head}• 5 дней бесплатно, далее по тарифу\n\n{tail}"


@router.message(_CmdPay("pay"))
async def on_pay(m: Message):
    tg_id = m.from_user.id
    async for session in get_session():
        from app.db.models import User

        u = (
            await session.execute(select(User).where(User.tg_id == tg_id))
        ).scalar_one_or_none()
        if not u:
            await m.answer(
                "Нажми /start, чтобы завершить онбординг.", reply_markup=kb_main_menu()
            )
            return
        active_sub = await _get_active_subscription(session, u.id)
        if active_sub:
            until = active_sub["subscription_until"]
            await m.answer(
                "Подписка активна ✅\nДоступ открыт до"
                f" <b>{_fmt_dt(until)}</b>.\n\nЧто дальше?",
                reply_markup=_kb_active_sub_actions(),
            )
            return
        if await is_trial_active(session, u.id):
            until = getattr(u, "trial_expires_at", None)
            tail = f"до <b>{_fmt_dt(until)}</b>" if until else "сейчас"
            await m.answer(
                "Пробный период активирован — {tail}. ✅\nВсе функции открыты.\n\n"
                "Хочешь оформить подписку сразу? (Автопродление можно отключить в /pay.)".format(
                    tail=tail
                ),
                reply_markup=_kb_trial_pay(),
            )
            return
        trial_ever = getattr(u, "trial_started_at", None) is not None
        await m.answer(
            _pay_plans_text(trial_ever_started=trial_ever),
            reply_markup=_kb_pay_plans(),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("pay:plan:"))
async def on_pick_plan(cb: CallbackQuery):
    try:
        raw_plan = (cb.data or "").split(":", 2)[-1].strip().lower()
    except Exception:
        await cb.answer("Некорректный запрос", show_alert=True)
        return

    PLAN_ALIAS = {
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

    amount, desc = _PLANS[plan]
    kb = _IKM(
        inline_keyboard=[
            [
                _IKB(
                    text=f"Оплатить картой 💳 ({amount} ₽)",
                    callback_data=f"pay:yk:{plan}",
                )
            ],
            [
                _IKB(
                    text="Оплатить звёздами ⭐️",
                    callback_data=f"pay:stars:{plan}",
                )
            ],
        ]
    )
    await cb.message.answer(
        f"<b>{desc}</b>\nСумма: <b>{amount} ₽</b>\n\nВыбери способ оплаты:",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pay:yk:"))
async def on_pick_plan_yk(cb: CallbackQuery):
    try:
        raw_plan = (cb.data or "").split(":", 2)[-1].strip().lower()
    except Exception:
        await cb.answer("Некорректный запрос", show_alert=True)
        return

    PLAN_ALIAS = {
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

    amount, desc = _PLANS[plan]
    async for session in get_session():
        from app.db.models import User

        u = (
            await session.execute(
                select(User).where(User.tg_id == cb.from_user.id)
            )
        ).scalar_one_or_none()
        if not u:
            await cb.answer("Нажми /start, чтобы начать.", show_alert=True)
            return
        try:
            pay_url = create_payment_link(
                amount_rub=int(amount),
                description=desc,
                metadata={"user_id": int(u.id), "plan": plan},
            )
        except Exception as e:
            print(f"[pay] create_payment_link raised: {e}")
            pay_url = None

    if not pay_url:
        await cb.message.answer(
            "Не удалось сформировать платёж. Попробуй ещё раз позже."
        )
        await cb.answer()
        return

    kb = _IKM(inline_keyboard=[[_IKB(text="Оплатить 💳", url=pay_url)]])
    await cb.message.answer(
        f"<b>{desc}</b>\nСумма: <b>{amount} ₽</b>\n\nНажми «Оплатить 💳», чтобы перейти к форме.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pay:stars:"))
async def on_pick_plan_stars(cb: CallbackQuery):
    try:
        raw_plan = (cb.data or "").split(":", 2)[-1].strip().lower()
    except Exception:
        await cb.answer("Некорректный запрос", show_alert=True)
        return

    PLAN_ALIAS = {
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
    if plan not in _PLANS or plan not in _STARS_PRICES:
        await cb.answer("Неизвестный план", show_alert=True)
        return

    _, desc = _PLANS[plan]
    stars_amount = _STARS_PRICES[plan]

    prices = [LabeledPrice(label=desc, amount=stars_amount)]

    await cb.message.answer_invoice(
        title=desc,
        description="Оплата подписки через Telegram Stars.",
        provider_token="",  # пустая строка для Telegram Stars
        currency="XTR",
        prices=prices,
        payload=f"stars:{plan}",
        start_parameter=f"stars_{plan}",
    )
    await cb.answer()


# --- Telegram Payments: pre_checkout + успешная оплата Stars ---
@router.pre_checkout_query()
async def on_pre_checkout(pre_q: PreCheckoutQuery):
    try:
        await pre_q.answer(ok=True)
    except Exception as e:
        print("[stars] pre_checkout error:", e)


@router.message(F.successful_payment)
async def on_successful_payment(m: Message):
    sp = m.successful_payment
    if not sp:
        return
    # Нас интересуют только звёзды
    if (sp.currency or "").upper() != "XTR":
        return

    payload = sp.invoice_payload or ""
    plan = None
    if payload.startswith("stars:"):
        plan = payload.split(":", 1)[1].strip().lower()

    PLAN_ALIAS = {
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
    plan = PLAN_ALIAS.get(plan or "", plan or "")
    if plan not in _PLANS:
        await m.answer(
            "Оплата прошла, но не удалось определить план. Напиши, пожалуйста, в поддержку.",
            reply_markup=kb_main_menu(),
        )
        return

    raw_event = {}
    try:
        if hasattr(sp, "model_dump"):
            raw_event = sp.model_dump()
        elif hasattr(sp, "to_python"):
            raw_event = sp.to_python()
        else:
            raw_event = sp.__dict__
    except Exception:
        raw_event = {}

    async for session in get_session():
        from app.db.models import User

        u = (
            await session.execute(
                select(User).where(User.tg_id == m.from_user.id)
            )
        ).scalar_one_or_none()
        if not u:
            await m.answer(
                "Оплата звёздами прошла, но не удалось найти пользователя. Напиши в поддержку.",
                reply_markup=kb_main_menu(),
            )
            return
        try:
            await apply_success_payment(
                user_id=int(u.id),
                plan=plan,  # type: ignore[arg-type]
                provider_payment_id=sp.telegram_payment_charge_id,
                payment_method_id=None,
                customer_id=None,
                session=session,
                raw_event=raw_event,
                provider="tg_stars",
                currency=sp.currency or "XTR",
                is_recurring=False,
                amount_override=int(sp.total_amount),
            )
        except Exception as e:
            print("[stars] apply_success_payment error:", e)
            await m.answer(
                "Оплата звёздами прошла, но не получилось обновить подписку. Напиши, пожалуйста, в поддержку.",
                reply_markup=kb_main_menu(),
            )
            return

    await m.answer(
        "Спасибо! Оплата через Telegram Stars прошла ✅\nДоступ к «Помни» открыт. Можно продолжать.",
        reply_markup=kb_main_menu(),
    )

# ===== Gate middleware =====
AllowedEvent = Union[Message, CallbackQuery]
ALLOWED_CB_PREFIXES = ("pay:", "yk:", "sub:")

async def _gate_send_paywall(event: AllowedEvent) -> None:
    text_ = (
        "Хочу продолжить помогать, но для этого нужна подписка.\n"
        "Оформи её в /pay и получи полный доступ ко всем функциям."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")]]
    )
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text_, reply_markup=kb)

async def _gate_user_flags(tg_id: int) -> Tuple[bool, bool, bool]:
    async with async_session() as s:
        r = await s.execute(
            text("SELECT id, policy_accepted_at, trial_started_at, trial_expires_at FROM users WHERE tg_id = :tg"),
            {"tg": int(tg_id)},
        )
        row = r.first()
        if not row:
            return False, False, False
        uid = int(row[0])
        policy_ok = bool(row[1])
        trial_ever = bool(row[2] or row[3])

        try:
            access_ok = bool((await get_access_state(s, uid)).get("has_access"))
        except Exception:
            access_ok = False

        if not trial_ever:
            try:
                r2 = await s.execute(
                    text("SELECT 1 FROM subscriptions WHERE user_id = :uid LIMIT 1"),
                    {"uid": int(uid)},
                )
                trial_ever = r2.first() is not None
            except Exception:
                trial_ever = False

    return policy_ok, access_ok, trial_ever

async def _gate_send_policy(event: AllowedEvent) -> None:
    policy_url, offer_url = _legal_urls()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📄 Правила", url=offer_url or "https://s.craft.me/APV7T8gRf3w2Ay"),
            InlineKeyboardButton(text="🔐 Политика", url=policy_url or "https://s.craft.me/APV7T8gRf3w2Ay"),
        ],
        [InlineKeyboardButton(text="Принимаю ✅", callback_data="onb:agree")],
    ])
    text_msg = ("Прежде чем мы познакомимся, подтвердим правила и политику. "
                "Это нужно, чтобы нам обоим было спокойно и безопасно.")
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text_msg, reply_markup=kb)

# ---------- ПРАВКА №2: усилили защиту автозапуска триала ----------
async def _maybe_start_trial_on_first_action(event: AllowedEvent) -> None:
    try:
        tg_id = getattr(getattr(event, "from_user", None), "id", None)
        if not tg_id:
            return
        async with async_session() as session:
            from app.db.models import User
            u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
            if not u:
                return
            # 0) есть доступ — выходим
            if (await get_access_state(session, u.id)).get("has_access"):
                return
            # 1) если триал когда-либо запускался ИЛИ есть зафиксированный его конец — не новый
            trial_ever = bool(getattr(u, "trial_started_at", None) or getattr(u, "trial_expires_at", None))
            if not trial_ever:
                row = await session.execute(
                    text("SELECT 1 FROM subscriptions WHERE user_id = :uid LIMIT 1"),
                    {"uid": int(u.id)},
                )
                trial_ever = row.first() is not None
            if trial_ever:
                return

            # 2) только теперь можно автозапускать триал
            started, expires = await start_trial_for_user(session, u.id)
            await session.commit()
            if not started:
                return
        target_msg = event.message if isinstance(event, CallbackQuery) else event
        try:
            await target_msg.answer(
                f"Пробный период активирован ✅\nДоступ открыт до {_fmt_dt(expires)}.",
                reply_markup=kb_main_menu()
            )
        except Exception:
            pass
    except Exception:
        return
# -------------------------------------------------------

class GateMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            tg_id = getattr(getattr(event, "from_user", None), "id", None)
            if not tg_id:
                return await handler(event, data)

            try:
                async with async_session() as session:
                    from app.db.models import User
                    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
                    if u and getattr(u, "tg_is_blocked", False):
                        await mark_user_unblocked(session, int(u.id))
                        print(f"[tg] user active again; marked unblocked user_id={u.id} tg_id={tg_id}")
            except Exception:
                pass
            
            # успешные платежи не блокируем, даём им пройти к хендлеру
            if isinstance(event, Message) and getattr(event, "successful_payment", None):
                return await handler(event, data)
            # pay intent: ответить подсказкой и не блокировать policy
            if isinstance(event, Message) and event.text and is_subscription_intent(event.text):
                try:
                    await event.answer(get_pay_help_text(), reply_markup=kb_main_menu())
                except Exception:
                    pass
                return

            policy_ok, access_ok, trial_ever = await _gate_user_flags(int(tg_id))

            if not policy_ok:
                if isinstance(event, Message) and (event.text or "").startswith("/start"):
                    return await handler(event, data)
                if isinstance(event, CallbackQuery) and (event.data or "").startswith("onb:"):
                    return await handler(event, data)
                await _gate_send_policy(event); return

            if not access_ok:
                if isinstance(event, Message):
                    t = (event.text or "")
                    if t.startswith("/pay"):
                        return await handler(event, data)
                    if not trial_ever:
                        await _maybe_start_trial_on_first_action(event)
                        return await handler(event, data)
                    await _gate_send_paywall(event); return

                if isinstance(event, CallbackQuery):
                    d = (event.data or "")
                    if d.startswith(ALLOWED_CB_PREFIXES):
                        return await handler(event, data)
                    if not trial_ever:
                        await _maybe_start_trial_on_first_action(event)
                        return await handler(event, data)
                    await _gate_send_paywall(event); return

            return await handler(event, data)
        except Exception:
            return await handler(event, data)

# --- Однократный mount ---
if not getattr(router, "_gate_mounted", False):
    router.message.middleware(GateMiddleware())
    router.callback_query.middleware(GateMiddleware())
    router._gate_mounted = True

# Логирование входящих после Gate
router.message.middleware(LogIncomingMiddleware())
router.callback_query.middleware(LogIncomingMiddleware())

# --- stats router ---
router.include_router(stats_router)

@router.message(Command("about"))
async def cmd_about(m: Message):
    email = os.getenv("CONTACT_EMAIL") or "support@example.com"
    txt = (
        "«Помни» — тёплый помощник, который помогает выговориться и прояснить мысли. "
        "Бережная и безоценочная поддержка, опираясь на современный научный подход.\n\n"
        "Что внутри:\n"
        "• «Поговорить» — бот эмоциональной поддержки с функцией дневника: разложить ситуацию, найти опору, наметить 1 маленький шаг.\n"
        "• «Открыть» Mini App - разделы с упражнениями и медитациями доступны в мини-приложении.\n\n"
        "Наши внутренние правила:\n"
        "— мягкое и дружеское общение, без лекций — сам решай как и о чём говорить;\n"
        "— бережные рамки КПТ/АКТ/гештальта; нормализация и маленькие поведенческие шаги;\n"
        "— приватность по умолчанию: можно отключить хранение истории в /privacy (не запоминаются разговоры и не работает память).\n\n"
        f"Если есть идеи или обратная связь — напиши: {email}"
    )
    await m.answer(txt)
