# -*- coding: utf-8 -*-
"""
app/bot.py — ReflectAI
Полная рабочая версия под aiogram 3.x
"""

from __future__ import annotations

import os
import sqlite3
import hashlib
from contextlib import contextmanager
from collections import defaultdict, deque
from typing import Dict, Deque, Optional, Tuple, List, Any

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import types
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ===== внешние модули проекта (существуют у тебя) =====
# llm / prompts / rag / упражнения
try:
    from app.llm_adapter import chat_with_style
except Exception:
    from llm_adapter import chat_with_style  # fallback на корень

try:
    import app.prompts as PROMPTS
except Exception:
    try:
        import prompts as PROMPTS
    except Exception:
        PROMPTS = None

try:
    import app.exercises as EX
except Exception:
    import exercises as EX  # должно существовать

try:
    import app.rag_qdrant as RAG
    rag_search_fn = RAG.search
except Exception:
    try:
        import rag_qdrant as RAG
        rag_search_fn = RAG.search
    except Exception:
        rag_search_fn = None

# ====== Роутер ======
router = Router(name="reflectai-bot")

# ====== Глобальные настройки / состояния ======
EMO_HERB = "🌿"

# картинка-онбординг (из env; можно задать file_id/url)
ONB_IMAGES = {
    "cover": os.getenv("ONB_IMG_COVER", ""),
    "talk": os.getenv("ONB_IMG_TALK", ""),
    "work": os.getenv("ONB_IMG_WORK", ""),
    "meditations": os.getenv("ONB_IMG_MEDIT", "")
}

# Фоллбэки на случай пустых env (замени на свои ссылки/ID)
DEFAULT_ONB_IMAGES = {
    "cover": "https://file.garden/aML3M6Sqrg21TaIT/kind-creature-min.jpg",
    "talk": "https://file.garden/aML3M6Sqrg21TaIT/_practices-min.jpg",
    "work": "https://example.com/reflectai/work.jpg",
    "meditations": "https://file.garden/aML3M6Sqrg21TaIT/meditation%20(1)-min.jpg",
}

def get_onb_image(key: str) -> str:
    val = (ONB_IMAGES.get(key) or "").strip()
    if val:
        return val
    return DEFAULT_ONB_IMAGES.get(key, "")

# тихие ссылки
POLICY_URL = os.getenv("POLICY_URL", "https://s.craft.me/APV7T8gRf3w2Ay")
TERMS_URL  = os.getenv("TERMS_URL",  "https://s.craft.me/APV7T8gRf3w2Ay")

# Диалоги в памяти (по чату)
DIALOG_HISTORY: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=20))
CHAT_MODE: Dict[int, str] = defaultdict(lambda: "talk")  # talk | reflection

# SQLite хранилка простых настроек
DB_PATH = os.getenv("BOT_DB_PATH", "bot.sqlite3")

@contextmanager
def db_session():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def _ensure_tables():
    with db_session() as s:
        s.execute("""
        CREATE TABLE IF NOT EXISTS user_prefs (
            tg_id TEXT PRIMARY KEY,
            voice_style TEXT DEFAULT 'default',
            consent_save_all INTEGER DEFAULT 0,
            goals TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        s.commit()

def _get_user_voice(tg_id: str) -> str:
    _ensure_tables()
    with db_session() as s:
        row = s.execute("SELECT voice_style FROM user_prefs WHERE tg_id=?", (tg_id,)).fetchone()
        return (row[0] if row and row[0] else "default")

def _set_user_voice(tg_id: str, style: str):
    _ensure_tables()
    with db_session() as s:
        s.execute("""
        INSERT INTO user_prefs (tg_id, voice_style) VALUES (?, ?)
        ON CONFLICT(tg_id) DO UPDATE SET voice_style=excluded.voice_style, updated_at=CURRENT_TIMESTAMP
        """, (tg_id, style))
        s.commit()

def _append_goal(tg_id: str, goal_key: str):
    _ensure_tables()
    with db_session() as s:
        row = s.execute("SELECT goals FROM user_prefs WHERE tg_id=?", (tg_id,)).fetchone()
        goals = set((row[0].split(",") if (row and row[0]) else []))
        if goal_key not in goals:
            goals.add(goal_key)
        s.execute("""
            INSERT INTO user_prefs (tg_id, goals) VALUES (?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET goals=?, updated_at=CURRENT_TIMESTAMP
        """, (tg_id, ",".join([g for g in goals if g])))
        s.commit()

# ====== PROMPTS: базовый + тона ======
PROMPT_SOURCE = "fallback"
SYSTEM_PROMPT: str = ""

if PROMPTS is not None:
    PROMPT_SOURCE = "prompts"
    # приоритет: TALK_SYSTEM_PROMPT > (SYSTEM_PROMPT + STYLE_TALK) > SYSTEM_PROMPT
    talk = getattr(PROMPTS, "TALK_SYSTEM_PROMPT", None)
    if isinstance(talk, str) and talk.strip():
        SYSTEM_PROMPT = talk
        PROMPT_SOURCE += ".TALK_SYSTEM_PROMPT"
    else:
        base = getattr(PROMPTS, "SYSTEM_PROMPT", "")
        style_talk = getattr(PROMPTS, "STYLE_TALK", "")
        if base.strip() and style_talk.strip():
            SYSTEM_PROMPT = base + "\n\n" + style_talk
            PROMPT_SOURCE += ".SYSTEM_PROMPT+STYLE_TALK"
        elif base.strip():
            SYSTEM_PROMPT = base
            PROMPT_SOURCE += ".SYSTEM_PROMPT"

if not SYSTEM_PROMPT:
    SYSTEM_PROMPT = (
        "Ты — «Помни» (ReflectAI), тёплый русскоязычный ассистент. Общайся на «ты», просто и бережно. "
        "Не ставь диагнозов и не замещай врача; при рисках мягко предложи обратиться к специалисту. "
        "Поддерживай, задавай уточняющие вопросы, помогай структурировать мысли. Без ссылок на источники."
    )

# оверлеи тонов
VOICE_STYLES = {
    "default": "",
    "friend": getattr(PROMPTS, "STYLE_FRIEND", "Стиль: тёплый друг, просто и поддерживающе, на «ты»."),
    "pro":    getattr(PROMPTS, "STYLE_PRO",    "Стиль: клинический психолог, аккуратно, по делу, без жаргона."),
    "dark":   getattr(PROMPTS, "STYLE_DARK",   "Стиль: взрослая ирония (18+), умно и бережно, без токсичности.")
}

def _style_overlay(style_key: str) -> str:
    key = (style_key or "default").lower()
    if key == "default":
        return ""
    return VOICE_STYLES.get(key, "")

# ====== Утилиты UI ======
def _valid_url(u: str) -> bool:
    return bool(u) and (u.startswith("http://") or u.startswith("https://"))

def safe_kb_onb_step2() -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row = []
    if _valid_url(POLICY_URL): row.append(InlineKeyboardButton(text="Политика", url=POLICY_URL))
    if _valid_url(TERMS_URL):  row.append(InlineKeyboardButton(text="Правила",  url=TERMS_URL))
    if row: buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Привет, хорошо ✅", callback_data="onb:agree")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_goals() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😰 Тревога", callback_data="goal:anxiety"),
         InlineKeyboardButton(text="🌀 Стресс", callback_data="goal:stress")],
        [InlineKeyboardButton(text="💤 Сон", callback_data="goal:sleep"),
         InlineKeyboardButton(text="🧭 Ясность", callback_data="goal:clarity")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="goal:done")],
    ])

def kb_settings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎚️ Тон", callback_data="settings:tone")],
        [InlineKeyboardButton(text="🔒 Privacy", callback_data="settings:privacy")],
    ])

def kb_tone() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎚️ По умолчанию", callback_data="tone:set:default")],
        [InlineKeyboardButton(text="🤝 Друг",         callback_data="tone:set:friend")],
        [InlineKeyboardButton(text="🧠 Про",          callback_data="tone:set:pro")],
        [InlineKeyboardButton(text="🕶️ Ирония 18+",   callback_data="tone:set:dark")],
    ])

# ====== Эмодзи по темам ======
def topic_emoji(tid: str, title: str) -> str:
    t = (tid or "").lower()
    name = (title or "").lower()
    def has(*keys): return any(k in t or k in name for k in keys)

    if has("reflection", "рефлекс"): return "🪞"
    if has("anx", "тревог"): return "😰"
    if has("panic", "паник"): return "💥"
    if has("stress", "стресс"): return "🌀"
    if has("sleep", "сон", "бессон"): return "🌙"
    if has("mind", "осознан", "медитац", "дыхани", "тело"): return "🧘"
    if has("procrast", "прокраст"): return "⏳"
    if has("burnout", "выгора", "устал"): return "🪫"
    if has("clarity", "ясност", "цель", "план", "решен", "неопредел"): return "🧭"
    if has("relat", "отношен", "семь", "друз"): return "💞"
    if has("self", "самооцен", "уверенн"): return "🌱"
    if has("grief", "горе", "потер"): return "🖤"
    if has("anger", "злост", "раздраж"): return "😤"
    if has("depress", "депресс"): return "🌧"
    return EMO_HERB

def _topic_title(tid: str) -> str:
    t = getattr(EX, "TOPICS", {}).get(tid, {})
    title = t.get("title", tid)
    emoji = (t.get("emoji") or "").strip() or topic_emoji(tid, title)
    if not emoji or emoji == EMO_HERB:
        pool = ["🌈","✨","🫶","🛡️","🧩","📈","🪴","🌊","☀️","🌙","🧠","🫁","🧪","🫧","🧲","🎯","💡","🎈","🪄"]
        idx = int(hashlib.md5((tid or title).encode("utf-8")).hexdigest(), 16) % len(pool)
        emoji = pool[idx]
    return f"{emoji} {title}"

def kb_topics() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    topics: Dict[str, Dict[str, Any]] = getattr(EX, "TOPICS", {})
    ordered_ids = list(topics.keys())
    if "reflection" in ordered_ids:
        ordered_ids.remove("reflection")
        ordered_ids.insert(0, "reflection")
    for tid in ordered_ids:
        title = _topic_title(tid)
        rows.append([InlineKeyboardButton(text=title, callback_data=f"topic:{tid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="topics:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ====== Безопасное редактирование ======
async def _safe_edit_text(msg: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        if getattr(msg, "text", None) is not None:
            if msg.text != text:
                await msg.edit_text(text, reply_markup=reply_markup)
                return
        if getattr(msg, "caption", None) is not None:
            if msg.caption != text:
                await msg.edit_caption(text, reply_markup=reply_markup)
                return
    except Exception:
        pass
    try:
        await msg.answer(text, reply_markup=reply_markup)
    except Exception:
        await msg.answer(text)

async def _silent_ack(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass

# ====== Онбординг тексты ======
def onb_text_1() -> str:
    return (
        "Привет! Здесь ты можешь выговориться, посоветоваться или просто получить поддержку. "
        "Для меня не бывает «неважных тем» и «глупых вопросов». Забота о своём эмоциональном состоянии — важна. 💜\n\n"
        "Нажми на <b>Старт</b> и начни заботиться о себе."
    )

def onb_text_2() -> str:
    return (
        "Привет. Я — бот эмоциональной поддержки. Прежде чем мы познакомимся, подтвердим правила.\n\n"
        "Продолжая, ты принимаешь правила и политику сервиса:"
    )

def onb_text_3() -> str:
    return (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Если хочешь просто поговорить — нажми «Поговорить». Поделись, что у тебя на душе, а я поддержу и помогу разобраться.\n"
        "2) Нужен оперативный разбор — заходи в «Разобраться». Там короткие упражнения на разные темы.\n"
        "3) Хочешь аудио-передышку — «Медитации». (Скоро добавим подборку коротких аудио.)\n\n"
        "Выбирай, с чего начнём. Я рядом. 🌿"
    )

def kb_onb_step1() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Старт ✅", callback_data="onb:start")]
    ])

def kb_onb_step2() -> InlineKeyboardMarkup:
    return safe_kb_onb_step2()

def kb_onb_step3() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{EMO_HERB} Разобраться", callback_data="menu:work")],
        [InlineKeyboardButton(text="💬 Поговорить", callback_data="menu:talk")],
        [InlineKeyboardButton(text="🎧 Медитации", callback_data="menu:meditations")],
    ])

# ====== Главное меню ======
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{EMO_HERB} Разобраться", callback_data="menu:work")],
        [InlineKeyboardButton(text="💬 Поговорить", callback_data="menu:talk")],
        [InlineKeyboardButton(text="🎧 Медитации", callback_data="menu:meditations")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings")],
    ])

def get_home_text() -> str:
    return "Выбери раздел:"

# ====== /start ======
@router.message(Command("start"))
async def on_start(m: Message):
    CHAT_MODE[m.chat.id] = "talk"
    img = get_onb_image("cover")
    caption = onb_text_1()
    if img:
        try:
            await m.answer_photo(img, caption=caption, reply_markup=kb_onb_step1())
            return
        except Exception:
            pass
    await m.answer(caption, reply_markup=kb_onb_step1())

@router.callback_query(F.data == "onb:start")
async def on_onb_start(cb: CallbackQuery):
    await _silent_ack(cb)
    caption = onb_text_2() if 'onb_text_2' in globals() else "Привет. Я — бот эмоциональной поддержки. Продолжая, ты подтверждаешь правила и политику сервиса."
    # пробуем картинку, иначе просто текст
    img = None
    try:
        img = get_onb_image("cover") if 'get_onb_image' in globals() else (ONB_IMAGES.get("cover") or "")
    except Exception:
        img = ONB_IMAGES.get("cover") or ""
    if img:
        # сначала пробуем отредактировать медиа, если первое сообщение было фото
        try:
            await cb.message.edit_media(
                media=types.InputMediaPhoto(media=img, caption=caption),
                reply_markup=kb_onb_step2()
            )
            return
        except Exception:
            pass
        # если отредактировать не получилось — пришлём новое фото
        try:
            await cb.message.answer_photo(img, caption=caption, reply_markup=kb_onb_step2())
            return
        except Exception:
            pass
    await _safe_edit_text(cb.message, caption, reply_markup=kb_onb_step2())

@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    await _silent_ack(cb)
    caption = onb_text_3()
    await _safe_edit_text(cb.message, caption, reply_markup=kb_onb_step3())

# ====== Меню ======
@router.callback_query(F.data == "menu:work")
async def on_menu_work(cb: CallbackQuery):
    await _silent_ack(cb)
    img = get_onb_image("work")
    if img:
        try:
            await cb.message.edit_media()  # не трогаем медиа, просто отправим новое
        except Exception:
            pass
        try:
            await cb.message.answer_photo(img, caption="Выбирай тему:", reply_markup=kb_topics())
            return
        except Exception:
            pass
    await _safe_edit_text(cb.message, "Выбирай тему:", kb_topics())

@router.callback_query(F.data == "menu:talk")
async def on_menu_talk(cb: CallbackQuery):
    await _silent_ack(cb)
    CHAT_MODE[cb.message.chat.id] = "talk"
    img = get_onb_image("talk")
    caption = "Я рядом и слушаю. О чём хочется поговорить?"
    if img:
        try:
            await cb.message.answer_photo(img, caption=caption)
            return
        except Exception:
            pass
    await _safe_edit_text(cb.message, caption, None)

@router.callback_query(F.data == "menu:meditations")
async def on_menu_meditations(cb: CallbackQuery):
    await _silent_ack(cb)
    img = get_onb_image("meditations")
    caption = "🎧 Медитации — скоро добавим аудио-подборки. Пока можно попробовать дыхательные практики в упражнениях."
    if img:
        try:
            await cb.message.answer_photo(img, caption=caption)
            return
        except Exception:
            pass
    await _safe_edit_text(cb.message, caption)

@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(cb: CallbackQuery):
    await _silent_ack(cb)
    await _safe_edit_text(cb.message, "Настройки:", kb_settings())

# ====== Настройки -> тон ======
@router.callback_query(F.data == "settings:tone")
async def on_settings_tone(cb: CallbackQuery):
    await _silent_ack(cb)
    await _safe_edit_text(cb.message, "Выбери тон общения:", kb_tone())

@router.callback_query(F.data.startswith("tone:set:"))
async def on_tone_set(cb: CallbackQuery):
    await _silent_ack(cb)
    _, _, style = cb.data.partition("tone:set:")
    tg_id = str(cb.from_user.id)
    _set_user_voice(tg_id, style)
    await cb.message.answer("Стиль обновлён ✅")

# ====== Тексты «меню» по словам ======
@router.message(F.text.in_({"Меню", "меню"}))
async def on_menu_text(m: Message):
    await m.answer(get_home_text(), reply_markup=kb_main())

# ====== Раздел «Разобраться» ======
@router.message(F.text == f"{EMO_HERB} Разобраться")
async def on_work_section(m: Message):
    img = get_onb_image("work")
    if img:
        try:
            await m.answer_photo(img, caption="Выбирай тему:", reply_markup=kb_topics())
            return
        except Exception:
            pass
    await m.answer("Выбирай тему:", reply_markup=kb_topics())

@router.callback_query(F.data == "topics:back")
async def on_topics_back(cb: CallbackQuery):
    await _silent_ack(cb)
    await _safe_edit_text(cb.message, "Выбирай тему:", kb_topics())

# ====== Рефлексия — отдельная тема (свободный чат) ======
async def _start_reflection_chat(message: Message):
    chat_id = message.chat.id
    CHAT_MODE[chat_id] = "reflection"
    txt = (
        "Окей, давай в свободном формате поразбираемся. "
        "Я буду отвечать в рефлексивном ключе: помогать замечать мысли, чувства и потребности, "
        "замедлять и задавать мягкие вопросы. Напиши, с чего хочется начать."
    )
    await message.answer(txt)

# ====== Обработка выбора темы/упражнений ======
def kb_exercises(tid: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    topic = getattr(EX, "TOPICS", {}).get(tid, {})
    for ex in topic.get("exercises", []):
        title = ex.get("title", "Упражнение")
        eid = ex.get("id", "")
        rows.append([InlineKeyboardButton(text=title, callback_data=f"ex:{tid}:{eid}:start")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к темам", callback_data="topics:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def step_keyboard(tid: str, eid: str, idx: int, total: int) -> InlineKeyboardMarkup:
    prev_idx = max(0, idx-1)
    next_idx = min(total-1, idx+1)
    buttons = [
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex:{tid}:{eid}:step:{prev_idx}"),
        InlineKeyboardButton(text="➡️ Далее", callback_data=f"ex:{tid}:{eid}:step:{next_idx}"),
        InlineKeyboardButton(text="✅ Завершить", callback_data=f"ex:{tid}:{eid}:finish"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

@router.callback_query(F.data.startswith("topic:"))
async def on_topic_pick(cb: CallbackQuery):
    await _silent_ack(cb)
    _, tid = cb.data.split(":", 1)

    # Рефлексия — свободный чат
    if tid == "reflection":
        await _start_reflection_chat(cb.message)
        return

    topic = getattr(EX, "TOPICS", {}).get(tid)
    if not topic:
        await cb.message.answer("Тема пока недоступна.")
        return

    intro = topic.get("intro", "Начнём с короткого описания и потом к шагам 🌿")
    await cb.message.answer(intro, reply_markup=kb_exercises(tid))

# запуск упражнения
@router.callback_query(F.data.startswith("ex:"))
async def on_ex_action(cb: CallbackQuery):
    await _silent_ack(cb)
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.message.answer("Не хватает параметров упражнения.")
        return

    _, tid, eid, action, *rest = parts
    topic = getattr(EX, "TOPICS", {}).get(tid, {})
    ex = None
    for it in topic.get("exercises", []):
        if it.get("id") == eid:
            ex = it
            break
    if not ex:
        await cb.message.answer("Упражнение не найдено.")
        return

    steps: List[str] = ex.get("steps", [])
    if action == "start":
        # если упражнение без шагов — сразу сообщение
        if not steps:
            text = ex.get("text", "Попробуй сформулировать, что важно прямо сейчас. Я рядом.")
            await cb.message.answer(text, reply_markup=kb_exercises(tid))
            return
        # иначе показываем шаг 0
        step_text = steps[0]
        await cb.message.answer(step_text, reply_markup=step_keyboard(tid, eid, 0, len(steps)))
        return

    if action == "step":
        if not rest:
            await cb.message.answer("Нужен номер шага.")
            return
        try:
            idx = int(rest[0])
        except Exception:
            idx = 0
        if idx < 0 or idx >= len(steps):
            await cb.message.answer("Это все шаги. Завершаем?", reply_markup=step_keyboard(tid, eid, len(steps)-1, len(steps)))
            return

        step_text = steps[idx]
        await cb.message.answer(step_text, reply_markup=step_keyboard(tid, eid, idx, len(steps)))
        return

    if action == "finish":
        await cb.message.answer("Готово. Вернёмся к теме?", reply_markup=kb_exercises(tid))
        return

# ====== Настройки (кнопка меню справа) ======
@router.message(F.text == "⚙️ Настройки")
async def on_settings(m: Message):
    txt = (
        "Настройки:\n"
        "• Выбрать тон ответа — кнопка ниже.\n"
        "• Privacy — базовая информация о данных.\n"
    )
    await m.answer(txt, reply_markup=kb_settings())

# ====== Поговорить / Рефлексия (текстовые сообщения) ======

def _push(chat_id: int, role: str, content: str):
    DIALOG_HISTORY[chat_id].append({"role": role, "content": content})

@router.message(F.text == "💬 Поговорить")
async def on_talk_button(m: Message):
    CHAT_MODE[m.chat.id] = "talk"
    img = get_onb_image("talk")
    caption = "Я рядом и слушаю. О чём хочется поговорить?"
    if img:
        try:
            await m.answer_photo(img, caption=caption)
            return
        except Exception:
            pass
    await m.answer(caption)

@router.message(F.text & ~F.text.regexp(r'^/'))
async def on_text(m: Message):
    chat_id = m.chat.id
    tg_id = str(m.from_user.id)
    user_text = (m.text or "").strip()
    mode = CHAT_MODE.get(chat_id, "talk")  # talk | reflection

    # RAG — аккуратно, без падений
    rag_ctx = ""
    if rag_search_fn:
        try:
            rag_ctx = await rag_search_fn(user_text, k=3, max_chars=1200, lang="ru")
        except TypeError:
            try:
                rag_ctx = await rag_search_fn(user_text, k=3, max_chars=1200)
            except Exception:
                rag_ctx = ""
        except Exception:
            rag_ctx = ""

    # Системный промпт: базовый + выбранный тон + мод рефлексии + RAG-контекст
    style_key = _get_user_voice(tg_id)
    sys_prompt = SYSTEM_PROMPT
    overlay = _style_overlay(style_key)
    if overlay:
        sys_prompt = sys_prompt + "\n\n" + overlay
    if mode == "reflection":
        sys_prompt = sys_prompt + "\n\nСтиль: рефлексия. Помогай замечать мысли/чувства/потребности, задавай мягкие вопросы."
    if rag_ctx:
        sys_prompt = (
            sys_prompt
            + "\n\n[Контекст из проверенных источников — используй аккуратно, не раскрывай ссылки пользователю]\n"
            + rag_ctx
        ).strip()

    # История
    history = list(DIALOG_HISTORY[chat_id])
    messages = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": user_text}]

    # Вызов LLM
    try:
        answer = await chat_with_style(
            system=sys_prompt,  # дублируем в аргумент — на случай адаптера
            messages=messages,
            style_hint=overlay or VOICE_STYLES.get(style_key, ""),
            temperature=0.6,
        )
    except Exception:
        answer = "Похоже, модель сейчас недоступна. Я рядом 🌿 Попробуешь ещё раз?"

    _push(chat_id, "user", user_text)
    _push(chat_id, "assistant", answer)
    await m.answer(answer)

# ====== Service ======

@router.message(F.text.regexp(r'(?i)^(стоп|stop)$'))
async def on_stop_word(m: Message):
    chat_id = m.chat.id
    if CHAT_MODE.get(chat_id) == "reflection":
        CHAT_MODE[chat_id] = "talk"
        await m.answer("Окей, выходим из режима рефлексии. Можем продолжить обычный разговор 💬")

@router.message(Command("ping"))
async def on_ping(m: Message):
    await m.answer("pong ✅")

def kb_voice_picker() -> InlineKeyboardMarkup:
    # Выбор стиля общения
    rows = [
        [InlineKeyboardButton(text="🌿 Универсальный", callback_data="voice:default")],
        [InlineKeyboardButton(text="🤝 Друг/подруга", callback_data="voice:friend")],
        [InlineKeyboardButton(text="🧠 Психолог (pro)", callback_data="voice:pro")],
        [InlineKeyboardButton(text="🖤 18+ ирония", callback_data="voice:dark")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("tone"))
async def on_tone_cmd(m: Message):
    await m.answer("Выбери тон:", reply_markup=kb_tone())

@router.message(Command("privacy"))
async def on_privacy(m: Message):
    txt = "Privacy: мы бережно относимся к данным. Подробнее по ссылке ниже."
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Политика и правила", url=POLICY_URL),
    ]])
    await m.answer(txt, reply_markup=kb)

@router.message(Command("debug_prompt"))
async def on_debug_prompt(m: Message):
    preview = SYSTEM_PROMPT[:400] + ("…" if len(SYSTEM_PROMPT) > 400 else "")
    await m.answer(
        f"Источник промпта: <code>{PROMPT_SOURCE}</code>\n"
        f"Длина: {len(SYSTEM_PROMPT)}\n\n"
        f"<code>{preview}</code>"
    )


# ===== Статические команды / заглушки =====
@router.message(Command("help"))
async def on_help(m: Message):
    txt = (
        "Помогу с тёплой поддержкой и короткими упражнениями.\n\n"
        "• /talk — просто поговорить\n"
        "• /work — упражнения «Разобраться»\n"
        "• /meditations — аудио-передышки\n"
        "• /settings — быстрые настройки\n"
        "• /tone — выбрать стиль ответа\n"
        "• /policy — политика и правила\n"
    )
    await m.answer(txt)

@router.message(Command("about"))
async def on_about(m: Message):
    txt = "«Помни» — тёплая поддержка и микро-практики. Не замена клинической помощи. Береги себя 🌿"
    await m.answer(txt)

@router.message(Command("policy"))
async def on_policy(m: Message):
    policy = os.getenv("POLICY_URL", "https://s.craft.me/APV7T8gRf3w2Ay")
    terms  = os.getenv("TERMS_URL",  "https://s.craft.me/APV7T8gRf3w2Ay")
    await m.answer(f"Политика: {policy}\nПравила: {terms}")

@router.message(Command("pay"))
async def on_pay(m: Message):
    await m.answer("Поддержать проект: скоро добавим удобные способы. Спасибо за доверие 💜")

@router.message(Command("settings"))
async def on_settings(m: Message):
    try:
        await m.answer("Настройки:\n— Выбери тон ответа — кнопка ниже.", reply_markup=kb_settings())
    except Exception:
        await on_tone(m)

@router.message(Command("tone"))
async def on_tone(m: Message):
    try:
        await m.answer("Выбери стиль общения:", reply_markup=kb_voice_picker())
    except Exception:
        await m.answer("Выбери стиль: /voice default | friend | pro | dark")

@router.message(Command("meditations"))
@router.message(Command("meditation"))
async def on_meditations(m: Message):
    caption = "Скоро добавим подборку коротких аудио-передышек. Пока — 3 глубоких вдоха ✨"
    try:
        img = get_onb_image("meditations") if 'get_onb_image' in globals() else (ONB_IMAGES.get("meditations") or "")
    except Exception:
        img = ONB_IMAGES.get("meditations") or ""
    if img:
        try:
            await m.answer_photo(img, caption=caption)
            return
        except Exception:
            pass
    await m.answer(caption)

@router.message(F.text == "🎧 Медитации")
async def on_meditations_btn(m: Message):
    await on_meditations(m)

@router.message(F.text == "🎛 Тон")
@router.message(F.text == "🎚️ Тон")
async def on_tone_btn(m: Message):
    await on_tone(m)
