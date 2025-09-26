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
)

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

# БД для служебных операций (policy/приватность/очистка)
from sqlalchemy import text
from app.db import db_session

router = Router()


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

# ===== Локальные БД-хелперы под приватность/очистку =====
from sqlalchemy import text
from app.db.core import get_session

async def _ensure_user_id(tg_id: int) -> int:
    """
    Возвращает user.id по tg_id, создаёт при отсутствии.
    """
    async for session in get_session():
        row = (await session.execute(
            text("SELECT id FROM users WHERE tg_id = :tg LIMIT 1"),
            {"tg": int(tg_id)},
        )).first()
        if row:
            return int(row[0])

        # создаём
        new_id = (await session.execute(
            text("""
                INSERT INTO users (tg_id, privacy_level, created_at)
                VALUES (:tg, 'ask', now())
                RETURNING id
            """),
            {"tg": int(tg_id)},
        )).scalar_one()
        await session.commit()
        return int(new_id)
    
    # синхронный шорткат для старых Sync-участков кода
def _ensure_user_id_sync(tg_id: int) -> int:
    from sqlalchemy import text
    from app.db import db_session

    with db_session() as s:
        uid = s.execute(
            text("SELECT id FROM users WHERE tg_id = :tg"),
            {"tg": tg_id},
        ).scalar()

        if not uid:
            s.execute(
                text("INSERT INTO users (tg_id, privacy_level, created_at) "
                     "VALUES (:tg, 'all', now())"),
                {"tg": tg_id},
            )
            s.commit()
            uid = s.execute(
                text("SELECT id FROM users WHERE tg_id = :tg"),
                {"tg": tg_id},
            ).scalar()

        return int(uid)

def _db_get_privacy(tg_id: int) -> str:
    """Возвращает режим: 'none' | 'insights' (иные значения считаем как включено)."""
    with db_session() as s:
        mode = s.execute(text("SELECT privacy_level FROM users WHERE tg_id = :tg"), {"tg": tg_id}).scalar()
        return (mode or "insights").strip()

def await _db_set_privacy(tg_id: int, mode: str) -> None:
    mode = "none" if mode == "none" else "insights"
    with db_session() as s:
        uid = _ensure_user_id_sync(tg_id)   # ✅ без await
        s.execute(
            text("UPDATE users SET privacy_level = :m WHERE tg_id = :tg"),
            {"m": mode, "tg": tg_id},
        )
        s.commit()

def await _purge_user_history(tg_id: int) -> int:
    with db_session() as s:
        uid = _ensure_user_id_sync(tg_id)   # ✅ без await
        cnt = s.execute(
            text("SELECT COUNT(*) FROM bot_messages WHERE user_id = :u"),
            {"u": uid},
        ).scalar() or 0
        s.execute(
            text("DELETE FROM bot_messages WHERE user_id = :u"),
            {"u": uid},
        )
        s.commit()
        return int(cnt)

# --- Анти-штампы и эвристики «шаблонности»
BANNED_PHRASES = [
    "это, безусловно, очень трудная ситуация",
    "я понимаю, как ты себя чувствуешь",
    "важно дать себе время и пространство",
    "не забывай заботиться о себе",
    "если тебе нужно, можешь обратиться к друзьям"
]

def _has_banned_phrases(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in BANNED_PHRASES)

def _jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def _too_similar_to_recent(chat_id: int, candidate: str, *, lookback: int = 8, thr: float = 0.62) -> bool:
    try:
        recent = get_recent_messages(chat_id, limit=lookback*2)
        prev_bot = [m["text"] for m in recent if m["role"] == "bot"][-lookback:]
    except Exception:
        prev_bot = []
    return any(_jaccard(candidate, old) >= thr for old in prev_bot)

def _looks_templatey(text: str) -> bool:
    return _has_banned_phrases(text)

# --- «дневничковая» длина: когда можно расписать подробнее
STRUCTURE_KEYWORDS = [
    "что делать", "как поступить", "план", "шаги", "структур",
    "объясни", "разложи", "почему", "как справиться", "помоги разобраться",
]

def _wants_structure(user_text: str) -> bool:
    t = (user_text or "").lower()
    return (len(t) >= 240) or any(k in t for k in STRUCTURE_KEYWORDS)

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
    # 1) пробуем TOPICS (там у тебя есть emoji/title)
    meta = TOPICS.get(tid) or {}
    if isinstance(meta, dict):
        e = (meta.get("emoji") or "").strip()
        if e:
            return e
    # 2) устойчивый фолбэк по хешу
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
    # Структура EXERCISES плоская: EXERCISES[tid][eid] -> {title,intro,steps}
    for eid, ex in (EXERCISES.get(tid) or {}).items():
        if not isinstance(ex, dict):  # страховка на случай мусора
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

def kb_privacy_for(chat_id: int) -> InlineKeyboardMarkup:
    """
    Режимы users.privacy_level: 'none' -> хранение ВЫКЛ, иначе считаем ВКЛ.
    """
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
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Вперёд ➜", callback_data="onb:step2")]])

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

WHAT_NEXT_TEXT = (
    "Что дальше? Несколько вариантов:\n\n"
    "1) Если хочешь просто поговорить — нажми «Поговорить». Поделись, что у тебя на душе — я поддержу и помогу разобраться.\n"
    "2) Нужен оперативный разбор — заходи в «Разобраться». Там короткие упражнения по темам.\n"
    "3) Хочешь аудио-передышку — «Медитации».\n\n"
    "Пиши, как удобно — я рядом 🖤"
)

def kb_onb_step3() -> ReplyKeyboardMarkup:
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
    await cb.message.answer(ONB_2_TEXT, reply_markup=kb_onb_step2())
    await cb.answer()

@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    tg_id = cb.from_user.id
    uid = await _ensure_user_id(tg_id)
    # 1) фиксируем согласие
    try:
        with db_session() as s:
            s.execute(text("UPDATE users SET policy_accepted_at = CURRENT_TIMESTAMP WHERE id = :uid"), {"uid": uid})
            s.commit()
    except Exception:
        pass
    # 2) ответ
    try:
        await cb.answer("Спасибо! Принял ✅", show_alert=False)
    except Exception:
        pass
    await cb.message.answer(WHAT_NEXT_TEXT, reply_markup=kb_onb_step3())

# ===== Меню/навигация =====
@router.message(F.text.in_(["🌿 Разобраться", "/work"]))
async def on_work_menu(m: Message):
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
    await _safe_edit(cb.message, "Выбирай тему:", reply_markup=kb_topics())
    await cb.answer()

@router.callback_query(F.data.startswith("t:"))
async def on_topic_click(cb: CallbackQuery):
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
    tid = cb.data.split(":", 1)[1]
    await _safe_edit(cb.message, topic_button_title(tid), reply_markup=kb_exercises(tid))
    await cb.answer()

@router.callback_query(F.data.startswith("ex:"))
async def on_ex_click(cb: CallbackQuery):
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
        text = intro or (steps[0] if steps else "Шагов нет.")
        await _safe_edit(cb.message, text, reply_markup=step_keyboard_intro(tid, eid, total))
        await cb.answer(); return

    try:
        idx = int(action)
    except Exception:
        idx = 0
    idx = max(0, min(idx, total - 1))
    text = steps[idx] if steps else "Шагов нет."
    await _safe_edit(cb.message, text, reply_markup=step_keyboard(tid, eid, idx, total))
    await cb.answer()

# ===== Рефлексия =====
@router.callback_query(F.data == "reflect:start")
async def on_reflect_start(cb: CallbackQuery):
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
    img = get_onb_image("meditations")
    if img:
        try:
            await m.answer_photo(img, caption=MEDITATIONS_TEXT, reply_markup=kb_meditations_categories()); return
        except Exception:
            pass
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

@router.message(F.text == "🎧 Медитации")
async def on_meditations_btn(m: Message):
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

@router.callback_query(F.data == "med:cats")
async def on_med_cats(cb: CallbackQuery):
    await _safe_edit(cb.message, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories()); await cb.answer()

@router.callback_query(F.data.startswith("med:cat:"))
async def on_med_cat(cb: CallbackQuery):
    cid = cb.data.split(":", 2)[2]
    title = dict(get_categories()).get(cid, "Медитации")
    await _safe_edit(cb.message, f"🎧 {title}", reply_markup=kb_meditations_list(cid))
    await cb.answer()

@router.callback_query(F.data.startswith("med:play:"))
async def on_med_play(cb: CallbackQuery):
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

    # метрика (мягко)
    try:
        import json
        with db_session() as s:
            uid = await _ensure_user_id(cb.from_user.id)
            s.execute(
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
            s.commit()
    except Exception:
        pass

    await cb.answer("Запускай, я рядом 💛")

# ===== Настройки =====
@router.message(F.text.in_(["⚙️ Настройки", "/settings", "/setting"]))
async def on_settings(m: Message):
    await m.answer("Настройки:", reply_markup=kb_settings())

@router.callback_query(F.data == "menu:main")
async def on_menu_main(cb: CallbackQuery):
    await cb.message.answer("Меню:", reply_markup=kb_main_menu()); await cb.answer()

@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(cb: CallbackQuery):
    await _safe_edit(cb.message, "Настройки:", reply_markup=kb_settings()); await cb.answer()

@router.callback_query(F.data == "settings:tone")
async def on_settings_tone(cb: CallbackQuery):
    await _safe_edit(cb.message, "Выбери тон общения:", reply_markup=kb_tone_picker()); await cb.answer()

@router.callback_query(F.data == "settings:privacy")
async def on_settings_privacy(cb: CallbackQuery):
    await _safe_edit(cb.message, "Приватность:", reply_markup=kb_privacy_for(cb.message.chat.id)); await cb.answer()

@router.callback_query(F.data == "privacy:toggle")
async def on_privacy_toggle(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    mode = (await _db_get_privacy(chat_id) or "insights").lower()
    new_mode = "none" if mode != "none" else "insights"
    await _db_set_privacy(chat_id, new_mode)
    state_txt = "выключено" if new_mode == "none" else "включено"
    await _safe_edit(cb.message, f"Хранение истории сейчас: <b>{state_txt}</b>.", reply_markup=kb_privacy_for(chat_id))
    await cb.answer("Настройка применена")

@router.callback_query(F.data == "privacy:clear")
async def on_privacy_clear(cb: CallbackQuery):
    try:
        count = await _purge_user_history(cb.from_user.id)
    except Exception:
        await cb.answer("Не получилось очистить историю", show_alert=True); return
    await cb.answer("История удалена ✅", show_alert=True)
    text = f"Готово. Что дальше?\n\nУдалено записей: {count}."
    await _safe_edit(cb.message, text, reply_markup=kb_privacy_for(cb.message.chat.id))

@router.message(Command("privacy"))
async def on_privacy_cmd(m: Message):
    mode = (await _db_get_privacy(m.chat.id) or "insights").lower()
    state = "выключено" if mode == "none" else "включено"
    await m.answer(f"Хранение истории сейчас: <b>{state}</b>.", reply_markup=kb_privacy_for(m.chat.id))

# ===== Прочие команды =====
@router.message(Command("about"))
async def on_about(m: Message):
    await m.answer("«Помни» — тёплый помощник, который помогает выговориться и прояснить мысли. "
                   "Здесь бережно, безоценочно, с опорой на научный подход.")

@router.message(Command("help"))
async def on_help(m: Message):
    await m.answer("Если нужна помощь по сервису, напиши на selflect@proton.me — мы ответим.")

@router.message(Command("menu"))
async def on_menu(m: Message):
    await m.answer("Меню:", reply_markup=kb_main_menu())

# ===== Тон и режим разговора =====
@router.message(F.text.in_(["💬 Поговорить", "/talk"]))
async def on_talk(m: Message):
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
        recent = get_recent_messages(chat_id, limit=70)  # чуть больше окна
        for r in recent:
            role = "assistant" if r["role"] == "bot" else "user"
            history_msgs.append({"role": role, "content": r["text"]})
    except Exception:
        pass

    # 3) RAG-контекст (опционально)
    rag_ctx = ""
    if rag_search is not None:
        try:
            rag_ctx = await rag_search(user_text, k=6, max_chars=900, lang="ru")
        except Exception:
            rag_ctx = ""

    messages = [{"role": "system", "content": sys_prompt}]
    if rag_ctx:
        messages.append({"role": "system", "content": f"Фактический контекст по теме (используй по необходимости):\n{rag_ctx}"})
    messages += history_msgs
    messages.append({"role": "user", "content": user_text})

    # 4) Вызов LLM (две попытки: базовая + анти-штамповая)
    if chat_with_style is None:
        await m.answer("Я тебя слышу. Сейчас подключаюсь…")
        return

    # Хотим позволять 2–4 абзаца, если есть смысл — задаём потолок токенов
    LLM_MAX_TOKENS = 480

    def _needs_regen(text: str) -> bool:
        return not text or _looks_templatey(text) or _too_similar_to_recent(chat_id, text)

    # Первая попытка — обычная (чуть теплее и длиннее)
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

    # Если звучит шаблонно или повторяет прошлое — просим переписать живее, допускаем 2–4 абзаца
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

    await m.answer(reply, reply_markup=kb_main_menu())
    try:
        save_bot_message(chat_id, reply)
    except Exception:
        pass

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
    chat_id = m.chat.id
    # лог входящего
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


# === Async DB helpers (override legacy sync) =================================
from sqlalchemy import text as _sql_text
from app.db.core import async_session as _async_session

async def _db_get_privacy(tg_id: int) -> str | None:
    async with _async_session() as s2:
        val = (await s2.execute(
            _sql_text("SELECT privacy_level FROM users WHERE tg_id = :tg"),
            {"tg": str(tg_id)}
        )).scalar()
        return val

async def _db_set_privacy(tg_id: int, mode: str) -> None:
    await _ensure_user_id(tg_id)
    async with _async_session() as s2:
        await s2.execute(
            _sql_text("UPDATE users SET privacy_level = :m WHERE tg_id = :tg"),
            {"m": mode, "tg": str(tg_id)}
        )
        await s2.commit()

async def _purge_user_history(tg_id: int) -> int:
    uid = await _ensure_user_id(tg_id)
    async with _async_session() as s2:
        cnt = (await s2.execute(
            _sql_text("SELECT COUNT(*) FROM bot_messages WHERE user_id = :u"),
            {"u": uid}
        )).scalar() or 0
        await s2.execute(
            _sql_text("DELETE FROM bot_messages WHERE user_id = :u"),
            {"u": uid}
        )
        await s2.commit()
        return int(cnt)

# === /pay (временная заглушка, пока ЮKassa на модерации) =====================
from aiogram.filters import Command
from aiogram.types import Message

if "on_pay" not in globals():
    @router.message(Command("pay"))
    async def on_pay(m: Message):
        await m.answer("Подписка скоро появится. Мы готовим удобные тарифы.")

