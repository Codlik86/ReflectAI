
import os
import asyncio
import sqlite3
from contextlib import contextmanager
from collections import defaultdict, deque
from typing import Dict, Deque, Tuple, Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

# --- Local adapters ---
try:
    from app.llm_adapter import chat_with_style
except Exception:
    # Fallback to local import path when running as a single file
    from llm_adapter import chat_with_style

# --- Optional RAG (defensive import) ---
rag_available = False
rag_search_fn = None
try:
    # Expecting module app.rag_qdrant with async def search(text, k, max_chars) -> str
    from app import rag_qdrant
    rag_available = True
    rag_search_fn = rag_qdrant.search
except Exception:
    try:
        import rag_qdrant
        rag_available = True
        rag_search_fn = rag_qdrant.search
    except Exception:
        rag_available = False
        rag_search_fn = None

router = Router()

# --- Constants / Config ---
EMO_HERB = "🌿"

# Images (can be empty strings; bot will fallback to text)
ONB_IMAGES = {
    "cover": os.getenv("ONB_IMG_COVER", ""),
    "talk": os.getenv("ONB_IMG_TALK", ""),
    "work": os.getenv("ONB_IMG_WORK", ""),
    "meditations": os.getenv("ONB_IMG_MEDIT", ""),
}

VOICE_STYLES = {
    "default": "Стиль ответа: нейтральный и бережный. Пиши коротко, конкретно, без клише и без диагнозов.",
    "friend":  "Стиль ответа: тёплый друг. Проще слова, поддержка и мягкие вопросы. Без навязчивых советов.",
    "pro":     "Стиль ответа: клинический психолог. Аккуратно, по делу, термины по необходимости и с пояснением.",
    "dark":    "Стиль ответа: взрослая ирония 18+. Иронично, но бережно; никакой токсичности.",
}

SYSTEM_PROMPT = (
    "Ты — бережный русскоязычный психологический ассистент ReflectAI. "
    "Не даёшь диагнозов и не заменяешь врача. При рисках мягко советуешь обратиться к специалисту или горячим линиям. "
    "Отвечай кратко и по делу, но тепло."
)
REFLECTIVE_SUFFIX = (
    "\n\nРежим рефлексии: задавай короткие наводящие вопросы по одному, помогай структурировать мысли."
)

# --- Simple memory (per-chat short history) ---
DIALOG_HISTORY: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=12))
CHAT_MODE: Dict[int, str] = defaultdict(lambda: "talk")

# --- SQLite prefs ---
DB_PATH = os.getenv("BOT_DB_PATH", "bot.db")

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
            consent_save_all INTEGER DEFAULT 0,
            goals TEXT DEFAULT '',
            voice_style TEXT DEFAULT 'default',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # soft migration (ignore if exists)
        try:
            s.execute("ALTER TABLE user_prefs ADD COLUMN voice_style TEXT DEFAULT 'default';")
        except Exception:
            pass
        s.commit()

def _get_user_voice(tg_id: str) -> str:
    _ensure_tables()
    with db_session() as s:
        row = s.execute("SELECT voice_style FROM user_prefs WHERE tg_id=?", (tg_id,)).fetchone()
        return (row[0] if row and row[0] else "default")

def _set_user_voice(tg_id: str, style: str) -> None:
    _ensure_tables()
    with db_session() as s:
        s.execute("""
            INSERT INTO user_prefs (tg_id, voice_style) VALUES (?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET voice_style=excluded.voice_style, updated_at=CURRENT_TIMESTAMP
        """, (tg_id, style))
        s.commit()

# --- Keyboards ---

def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{EMO_HERB} Разобраться")],
            [KeyboardButton(text="💬 Поговорить"), KeyboardButton(text="🎧 Медитации")],
        ],
        resize_keyboard=True
    )

def kb_voice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎚️ По умолчанию", callback_data="voice:set:default")],
        [InlineKeyboardButton(text="🤝 Друг",         callback_data="voice:set:friend")],
        [InlineKeyboardButton(text="🧠 Про",          callback_data="voice:set:pro")],
        [InlineKeyboardButton(text="🕶️ Ирония 18+",   callback_data="voice:set:dark")],
    ])

def kb_goals() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😰 Тревога", callback_data="goal:anxiety"),
         InlineKeyboardButton(text="🌀 Стресс", callback_data="goal:stress")],
        [InlineKeyboardButton(text="💤 Сон", callback_data="goal:sleep"),
         InlineKeyboardButton(text="🧭 Ясность", callback_data="goal:clarity")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="goal_done")],
    ])

def kb_topics() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧩 Когнитивная переоценка", callback_data="topic:reframe")],
        [InlineKeyboardButton(text="📝 Рефлексия (короткая)", callback_data="reflect:start")],
    ])

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹️ Стоп", callback_data="reflect:stop")]
    ])

# --- Utils ---

async def silent_ack(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass

def _push(chat_id: int, role: str, content: str):
    DIALOG_HISTORY[chat_id].append({"role": role, "content": content})

def get_home_text() -> str:
    return (
        f"{EMO_HERB} Готово! Вот что дальше:\n\n"
        "• «💬 Поговорить» — просто расскажи, что на душе.\n"
        "• «🌿 Разобраться» — выбери тему и пройди шаги.\n"
        "• «🎧 Медитации» — расслабиться и выдохнуть.\n"
        "\nМожешь ввести /voice чтобы выбрать стиль ответа."
    )

# --- Handlers ---

@router.message(Command("start"))
async def on_start(m: Message):
    # Screen 1 — cover
    try:
        if ONB_IMAGES["cover"]:
            await m.answer_photo(ONB_IMAGES["cover"], caption="Привет! Я здесь, чтобы выслушать. Не стесняйся. Продолжим?")
        else:
            await m.answer("Привет! Я здесь, чтобы выслушать. Не стесняйся. Продолжим?")
    except Exception:
        await m.answer("Привет! Я здесь, чтобы выслушать. Не стесняйся. Продолжим?")
    # Screen 2 — quick goals
    await m.answer("Что сейчас важнее? Отметь и нажми «Готово».", reply_markup=kb_goals())

@router.callback_query(F.data.startswith("goal:"))
async def onb_goal_pick(cb: CallbackQuery):
    await silent_ack(cb)
    # Здесь можно сохранять выбранные цели в user_prefs.goals (опционально)
    await cb.message.edit_reply_markup(reply_markup=kb_goals())  # просто перерисуем для наглядности

@router.callback_query(F.data == "goal_done")
async def onb_goal_done(cb: CallbackQuery):
    await silent_ack(cb)
    # Screen 3 — What next?
    try:
        if ONB_IMAGES["talk"]:
            await cb.message.answer_photo(ONB_IMAGES["talk"], caption=get_home_text(), reply_markup=kb_main())
        else:
            await cb.message.answer(get_home_text(), reply_markup=kb_main())
    except Exception:
        await cb.message.answer(get_home_text(), reply_markup=kb_main())

@router.message(Command("voice"))
async def on_cmd_voice(m: Message):
    cur = _get_user_voice(str(m.from_user.id))
    txt = f"Выбери стиль голоса. Текущий: <b>{cur}</b>."
    await m.answer(txt, reply_markup=kb_voice())

@router.callback_query(F.data.startswith("voice:set:"))
async def on_voice_set(cb: CallbackQuery):
    await silent_ack(cb)
    style = cb.data.split(":", 2)[2]
    if style not in VOICE_STYLES:
        await cb.message.answer("Неизвестный стиль. Давай ещё раз: /voice")
        return
    _set_user_voice(str(cb.from_user.id), style)
    await cb.message.answer(f"Стиль обновлён: <b>{style}</b> ✅")

# Reflection mini-flow (very short 4 steps)

REFRAMING_STEPS = [
    ("situation", "Опиши ситуацию в двух-трёх предложениях."),
    ("thought", "Какая автоматическая мысль возникла?"),
    ("evidence", "Какие есть факты «за» и «против» этой мысли?"),
    ("alternate", "Как могла бы звучать более сбалансированная мысль?"),
]
_reframe_state: Dict[str, Dict[str, object]] = {}

@router.callback_query(F.data == "reflect:start")
async def reflect_start(cb: CallbackQuery):
    await silent_ack(cb)
    chat_id = cb.message.chat.id
    tg_id = str(cb.from_user.id)
    CHAT_MODE[chat_id] = "reflection"
    _reframe_state[tg_id] = {"step_idx": 0, "answers": {}}
    await cb.message.answer("Запускаю короткую рефлексию (4 шага, ~2 минуты).", reply_markup=stop_keyboard())
    await cb.message.answer(REFRAMING_STEPS[0][1], reply_markup=stop_keyboard())

@router.callback_query(F.data == "reflect:stop")
async def reflect_stop(cb: CallbackQuery):
    await silent_ack(cb)
    chat_id = cb.message.chat.id
    CHAT_MODE[chat_id] = "talk"
    await cb.message.answer("Окей, остановились. Можем вернуться позже. 💬")

# --- TALK: main text handler ---

@router.message(F.text)
async def on_text(m: Message):
    chat_id = m.chat.id
    tg_id = str(m.from_user.id)
    user_text = (m.text or "").strip()

    # Reflection steps handler
    if CHAT_MODE.get(chat_id) == "reflection":
        state = _reframe_state.setdefault(tg_id, {"step_idx": 0, "answers": {}})
        step_idx: int = int(state.get("step_idx", 0))
        answers: Dict[str, str] = state.get("answers", {})  # type: ignore

        key, _prompt = REFRAMING_STEPS[step_idx]
        answers[key] = user_text
        step_idx += 1

        if step_idx >= len(REFRAMING_STEPS):
            # Finish and summarize
            CHAT_MODE[chat_id] = "talk"
            _reframe_state.pop(tg_id, None)
            summary = (
                f"Ситуация: {answers.get('situation','')}\n"
                f"Мысль: {answers.get('thought','')}\n"
                f"Факты: {answers.get('evidence','')}\n"
                f"Альтернатива: {answers.get('alternate','')}\n\n"
                "Как это ощущается сейчас?"
            )
            await m.answer(summary, reply_markup=stop_keyboard())
            return
        else:
            state["step_idx"] = step_idx
            state["answers"] = answers
            await m.answer(REFRAMING_STEPS[step_idx][1], reply_markup=stop_keyboard())
            return

    # Soft RAG
    rag_ctx = ""
    if rag_available and rag_search_fn:
        try:
            rag_ctx = await rag_search_fn(user_text, k=3, max_chars=1200)
        except Exception:
            rag_ctx = ""

    # Style
    style_key = _get_user_voice(tg_id)
    style_hint = VOICE_STYLES.get(style_key, VOICE_STYLES["default"])

    # System with RAG
    sys_prompt = SYSTEM_PROMPT
    if rag_ctx:
        sys_prompt = (
            sys_prompt
            + "\n\n[Контекст — используй аккуратно, не раскрывай источники пользователю]\n"
            + rag_ctx
        ).strip()

    # Short history
    history = list(DIALOG_HISTORY[chat_id])
    messages = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": user_text}]

    # LLM
    try:
        answer = await chat_with_style(messages=messages, style_hint=style_hint, temperature=0.6)
    except Exception:
        answer = "Похоже, модель недоступна. Я рядом 🌿 Попробуешь ещё раз?"

    _push(chat_id, "user", user_text)
    _push(chat_id, "assistant", answer)
    await m.answer(answer)
    return
