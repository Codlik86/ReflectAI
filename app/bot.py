# app/bot.py
import os
import sqlite3
from contextlib import contextmanager
from collections import defaultdict, deque
from typing import Dict, Deque, Optional, Tuple, List, Any

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.exceptions import TelegramBadRequest

# ===== Imports from project modules (prompts/exercises/llm/rag) =====
try:
    from app.prompts import SYSTEM_PROMPT
except Exception:
    try:
        from prompts import SYSTEM_PROMPT
    except Exception:
        SYSTEM_PROMPT = (
            "Ты — бережный русскоязычный психологический ассистент ReflectAI. "
            "Не даёшь диагнозов и не заменяешь врача. При рисках мягко советуешь обратиться к специалисту."
        )

try:
    from app.exercises import TOPICS
except Exception:
    from exercises import TOPICS

try:
    from app.llm_adapter import chat_with_style
except Exception:
    from llm_adapter import chat_with_style

rag_search_fn = None
try:
    from app import rag_qdrant
    rag_search_fn = rag_qdrant.search
except Exception:
    try:
        import rag_qdrant
        rag_search_fn = rag_qdrant.search
    except Exception:
        rag_search_fn = None

# ===== Router =====
router = Router()

# ===== Constants / Config =====
EMO_HERB = "🌿"

ONB_IMAGES = {
    "cover1": os.getenv("ONB_IMG_COVER", ""),
    "cover2": os.getenv("ONB_IMG_COVER2", ""),
    "talk": os.getenv("ONB_IMG_TALK", ""),
    "work": os.getenv("ONB_IMG_WORK", ""),
    "meditations": os.getenv("ONB_IMG_MEDIT", ""),
}

POLICY_URL = os.getenv("POLICY_URL", "#")
TERMS_URL = os.getenv("TERMS_URL", "#")
LEGAL_CRAFT_LINK = os.getenv("LEGAL_CRAFT_LINK", "https://s.craft.me/APV7T8gRf3w2Ay")

VOICE_STYLES = {
    "default": "Стиль ответа: нейтральный, бережный. Коротко, по делу, без диагнозов и категоричных советов.",
    "friend":  "Стиль ответа: тёплый друг. Проще слова, много поддержки, мягкие вопросы. Никакой назидательности.",
    "pro":     "Стиль ответа: клинический психолог. Аккуратно, точные формулировки, термины с пояснениями.",
    "dark":    "Стиль ответа: взрослая ирония (18+). Умно и бережно, без токсичности и осуждения.",
}

REFLECTIVE_SUFFIX = (
    "\n\nРежим рефлексии: задавай короткие вопросы по одному, помогай структурировать мысли, поддерживай темп."
)

# ===== Simple Dialogue Memory (per-chat short history) =====
DIALOG_HISTORY: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=14))
CHAT_MODE: Dict[int, str] = defaultdict(lambda: "talk")   # 'talk' | 'reflection'

def _push(chat_id: int, role: str, content: str):
    DIALOG_HISTORY[chat_id].append({"role": role, "content": content})

# ===== Persistent user prefs (SQLite) =====
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
            voice_style TEXT DEFAULT 'default',
            consent_save_all INTEGER DEFAULT 0,
            goals TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        for ddl in [
            "ALTER TABLE user_prefs ADD COLUMN voice_style TEXT DEFAULT 'default';",
            "ALTER TABLE user_prefs ADD COLUMN consent_save_all INTEGER DEFAULT 0;",
            "ALTER TABLE user_prefs ADD COLUMN goals TEXT DEFAULT '';",
        ]:
            try:
                s.execute(ddl)
            except Exception:
                pass
        s.commit()

def _set_consent(tg_id: str, value: int = 1):
    _ensure_tables()
    with db_session() as s:
        s.execute("""
            INSERT INTO user_prefs (tg_id, consent_save_all) VALUES (?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET consent_save_all=excluded.consent_save_all, updated_at=CURRENT_TIMESTAMP
        """, (tg_id, value))
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

# ===== Keyboards =====

def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{EMO_HERB} Разобраться")],
            [KeyboardButton(text="💬 Поговорить"), KeyboardButton(text="🎧 Медитации")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )

def kb_onb_step1() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Старт ▶️", callback_data="onb:start")]
    ])

def _valid_url(u: str) -> bool:
    return bool(u) and (u.startswith("http://") or u.startswith("https://"))

def kb_onb_step2() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    # Если ссылки валидны — даём url-кнопки; иначе callback с текстовой заглушкой
    if _valid_url(POLICY_URL):
        row.append(InlineKeyboardButton(text="Политика", url=POLICY_URL))
    else:
        row.append(InlineKeyboardButton(text="Политика", callback_data="onb:policy"))
    if _valid_url(TERMS_URL):
        row.append(InlineKeyboardButton(text="Правила", url=TERMS_URL))
    else:
        row.append(InlineKeyboardButton(text="Правила", callback_data="onb:terms"))
    buttons.append(row)
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

def _topic_title(tid: str) -> str:
    t = TOPICS.get(tid, {})
    title = t.get("title", tid)
    emoji = t.get("emoji") or EMO_HERB
    return f"{emoji} {title}"

def kb_topics() -> InlineKeyboardMarkup:
    rows = []
    ordered_ids = list(TOPICS.keys())
    if "reflection" in ordered_ids:
        ordered_ids.remove("reflection")
        ordered_ids = ["reflection"] + ordered_ids
    for tid in ordered_ids:
        rows.append([InlineKeyboardButton(text=_topic_title(tid), callback_data=f"topic:{tid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_exercises(tid: str) -> InlineKeyboardMarkup:
    t = TOPICS.get(tid, {})
    exs = t.get("exercises", []) or []
    rows = []
    for ex in exs:
        eid = ex["id"]
        rows.append([InlineKeyboardButton(text=f"• {ex['title']}", callback_data=f"ex:{tid}:{eid}:start")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к темам", callback_data="topics:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def step_keyboard(tid: str, eid: str, idx: int, total: int) -> InlineKeyboardMarkup:
    if idx + 1 < total:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Далее", callback_data=f"ex:{tid}:{eid}:step:{idx+1}")],
            [InlineKeyboardButton(text="🏁 Завершить", callback_data=f"ex:{tid}:{eid}:finish")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏁 Завершить", callback_data=f"ex:{tid}:{eid}:finish")],
    ])

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏹️ Стоп", callback_data="reflect:stop")]])

# ===== Helpers =====

async def _safe_edit_text(msg: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await msg.answer(text, reply_markup=reply_markup)
        except Exception:
            raise

async def _safe_edit_caption(msg: Message, caption: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await msg.edit_caption(caption, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await msg.answer(caption, reply_markup=reply_markup)
        except Exception:
            raise

async def _silent_ack(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass

# ===== Onboarding texts =====

def onb_text_1() -> str:
    return (
        "Привет! Здесь ты можешь выговориться, посоветоваться или просто получить поддержку. "
        "Для меня не бывает «неважных тем» и «глупых вопросов». Забота о своём эмоциональном состоянии — важна. 💜\n\n"
        "Нажми на <b>Старт</b> и начни заботиться о себе."
    )

def onb_text_2() -> str:
    return (
        "Привет. Я — бот эмоциональной поддержки. Прежде чем мы познакомимся, подтверди правила.\n\n"
        "Продолжая, ты принимаешь правила и политику сервиса:"
    )

def onb_text_3() -> str:
    return (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Если хочешь просто поговорить — нажми «Поговорить». Поделись, что у тебя на душе, а я поддержу и помогу разобраться.\n"
        "2) Нужен оперативный разбор — заходи в «Разобраться». Там короткие упражнения на разные темы.\n"
        "3) Хочешь аудио-передышку — «Медитации». (Скоро добавим подборку коротких аудио.)\n\n"
        "Пиши, как удобно — я рядом 🖤"
    )

def get_home_text() -> str:
    return (
        f"{EMO_HERB} Готово! Вот что дальше:\n\n"
        "• «💬 Поговорить» — просто расскажи, что на душе.\n"
        "• «🌿 Разобраться» — выбери тему и пройди упражнения.\n"
        "• «🎧 Медитации» — расслабиться и выдохнуть.\n"
        "\nМожешь ввести /tone чтобы выбрать стиль ответа."
    )

# ===== Handlers =====

@router.message(Command("start"))
async def on_start(m: Message):
    img = ONB_IMAGES.get("cover1") or ""
    if img:
        try:
            await m.answer_photo(img, caption=onb_text_1(), reply_markup=kb_onb_step1())
            return
        except Exception:
            pass
    await m.answer(onb_text_1(), reply_markup=kb_onb_step1())

@router.callback_query(F.data == "onb:start")
async def on_onb_start(cb: CallbackQuery):
    await _silent_ack(cb)
    img = ONB_IMAGES.get("cover2") or ONB_IMAGES.get("cover1") or ""
    caption = onb_text_2()
    if img:
        try:
            if cb.message.photo:
                await _safe_edit_caption(cb.message, caption, kb_onb_step2())
            else:
                await cb.message.answer_photo(img, caption=caption, reply_markup=kb_onb_step2())
        except Exception:
            await cb.message.answer(caption, reply_markup=kb_onb_step2())
    else:
        await _safe_edit_text(cb.message, caption, kb_onb_step2())

@router.callback_query(F.data == "onb:policy")
async def on_onb_policy(cb: CallbackQuery):
    await _silent_ack(cb)
    txt = (
        "Политика конфиденциальности и условия использования доступны по ссылке:\n"
        f"{LEGAL_CRAFT_LINK}\n\n"
        "Нажимая «Привет, хорошо ✅», ты подтверждаешь ознакомление."
    )
    await cb.message.answer(txt)

@router.callback_query(F.data == "onb:terms")
async def on_onb_terms(cb: CallbackQuery):
    await _silent_ack(cb)
    txt = (
        "Правила сервиса и условия использования доступны по ссылке:\n"
        f"{LEGAL_CRAFT_LINK}\n\n"
        "Нажимая «Привет, хорошо ✅», ты подтверждаешь ознакомление."
    )
    await cb.message.answer(txt)

@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    await _silent_ack(cb)
    _set_consent(str(cb.from_user.id), 1)
    try:
        await cb.message.answer(onb_text_3(), reply_markup=kb_main())
    except Exception:
        await cb.message.answer(get_home_text(), reply_markup=kb_main())

@router.message(F.text.in_({"Меню", "меню"}))
async def on_menu_text(m: Message):
    await m.answer(get_home_text(), reply_markup=kb_main())

# ===== Раздел «Разобраться» =====

@router.message(F.text == f"{EMO_HERB} Разобраться")
async def on_work_section(m: Message):
    img = ONB_IMAGES.get("work") or ""
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


async def _start_reflection_chat(message: Message):
    chat_id = message.chat.id
    CHAT_MODE[chat_id] = "reflection"
    txt = (
        "Окей, давай в свободном формате поразбираемся. "
        "Я буду отвечать в рефлексивном ключе: помогать замечать мысли, чувства и потребности, "
        "и мягко наводить на шаги поддержки. Чтобы выйти из режима рефлексии — напиши «Стоп» или нажми Меню."
    )
    await message.answer(txt)

@router.callback_query(F.data.startswith("topic:"))
async def on_topic_pick(cb: CallbackQuery):
    await _silent_ack(cb)
    tid = cb.data.split(":", 1)[1]
    t = TOPICS.get(tid)
    if not t:
        await cb.message.answer("Не нашёл тему. Вернёмся к списку:", reply_markup=kb_topics())
        return

    if t.get("type") == "chat" or tid == "reflection":
        await _start_reflection_chat(cb.message)
        return

    intro = (t.get("intro") or "").strip()
    text = f"<b>{_topic_title(tid)}</b>\n\n{intro}" if intro else f"<b>{_topic_title(tid)}</b>"
    if cb.message.photo:
        await _safe_edit_caption(cb.message, text, kb_exercises(tid))
    else:
        try:
            await _safe_edit_text(cb.message, text, kb_exercises(tid))
        except Exception:
            await cb.message.answer(text, reply_markup=kb_exercises(tid))

# ===== Упражнения =====
EX_STATE: Dict[int, Dict[str, Any]] = defaultdict(dict)

def _find_exercise(tid: str, eid: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    t = TOPICS.get(tid, {})
    for ex in (t.get("exercises") or []):
        if ex.get("id") == eid:
            steps = ex.get("steps") or []
            # "без шагов": если steps пусто, но есть 'text'/'content' — делаем одиночный шаг
            if not steps:
                text = ex.get("text") or ex.get("content") or ""
                if text:
                    steps = [text]
            return ex, steps
    return None, []

@router.callback_query(F.data.startswith("ex:"))
async def on_ex_click(cb: CallbackQuery):
    await _silent_ack(cb)
    parts = cb.data.split(":")
    # ex:<tid>:<eid>:start | ex:<tid>:<eid>:step:<idx> | ex:<tid>:<eid>:finish
    if len(parts) < 4:
        await cb.message.answer("Не понял команду упражнения.")
        return
    _, tid, eid, action, *rest = parts

    ex, steps = _find_exercise(tid, eid)
    if not ex:
        await cb.message.answer("Не нашёл упражнение. Вернёмся к теме.", reply_markup=kb_exercises(tid))
        return

    if action == "start":
        EX_STATE[cb.message.chat.id] = {"tid": tid, "eid": eid, "idx": 0}
        intro = ex.get("intro") or ""
        head = f"<b>{_topic_title(tid)}</b>\n— {ex.get('title', '')}\n\n{intro}".strip()
        if head:
            await cb.message.answer(head)
        step_text = steps[0] if steps else "Шагов нет."
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
        EX_STATE[cb.message.chat.id] = {"tid": tid, "eid": eid, "idx": idx}
        step_text = steps[idx]
        await cb.message.answer(step_text, reply_markup=step_keyboard(tid, eid, idx, len(steps)))
        return

    if action == "finish":
        EX_STATE.pop(cb.message.chat.id, None)
        await cb.message.answer("Готово. Вернёмся к теме?", reply_markup=kb_exercises(tid))
        return
# ===== Медитации =====

@router.message(F.text == "⚙️ Настройки")
async def on_settings(m: Message):
    txt = (
        "Настройки:\n"
        "• Выбрать тон ответа — кнопка ниже.\n"
        "• Политика/правила — открою ссылку.\n"
        "Позже добавим больше параметров."
    )
    await m.answer(txt, reply_markup=kb_settings())

@router.callback_query(F.data == "settings:tone")
async def on_settings_tone(cb: CallbackQuery):
    await _silent_ack(cb)
    cur = _get_user_voice(str(cb.from_user.id))
    await cb.message.answer(f"Текущий тон: <b>{cur}</b>. Выбери ниже:", reply_markup=kb_tone())

@router.callback_query(F.data == "settings:privacy")
async def on_settings_privacy(cb: CallbackQuery):
    await _silent_ack(cb)
    # Используем ранее добавленный LEGAL_CRAFT_LINK, если есть; иначе просто напомним про /tone
    link = globals().get("LEGAL_CRAFT_LINK") or "https://s.craft.me/APV7T8gRf3w2Ay"
    await cb.message.answer(f"Политика и правила: {link}")
@router.message(F.text == "🎧 Медитации")
async def on_meditations(m: Message):
    img = ONB_IMAGES.get("meditations") or ""
    caption = "Скоро добавим подборку коротких аудио-практик. Пока можешь зайти в «Разобраться» или «Поговорить»."
    if img:
        try:
            await m.answer_photo(img, caption=caption)
            return
        except Exception:
            pass
    await m.answer(caption)

# ===== Поговорить (вход) =====
@router.message(F.text == "💬 Поговорить")
async def on_talk_enter(m: Message):
    img = ONB_IMAGES.get("talk") or ""
    caption = "Я рядом и слушаю. О чём хочется поговорить?"
    if img:
        try:
            await m.answer_photo(img, caption=caption)
            return
        except Exception:
            pass
    await m.answer(caption)

# ===== Поговорить (LLM) =====
@router.message(F.text)
async def on_text(m: Message):
    chat_id = m.chat.id
    tg_id = str(m.from_user.id)
    user_text = (m.text or "").strip()
# Soft RAG (пытаемся с lang, если сигнатура без него — повторяем без lang)
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

    # Style
    style_key = _get_user_voice(tg_id)
    style_hint = VOICE_STYLES.get(style_key, VOICE_STYLES["default"])

    # System prompt + (optional) reflective suffix + RAG-context
    sys_prompt = SYSTEM_PROMPT
    if CHAT_MODE.get(chat_id) == "reflection":
        sys_prompt += REFLECTIVE_SUFFIX
    if rag_ctx:
        sys_prompt = (
            sys_prompt
            + "\n\n[Контекст из проверенных источников — используй аккуратно, не раскрывай ссылки пользователю]\n"
            + rag_ctx
        ).strip()

    # History
    history = list(DIALOG_HISTORY[chat_id])
    messages = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": user_text}]

    # LLM
    try:
        answer = await chat_with_style(messages=messages, style_hint=style_hint, temperature=0.6)
    except Exception:
        answer = "Похоже, модель сейчас недоступна. Я рядом 🌿 Попробуешь ещё раз?"

    _push(chat_id, "user", user_text)
    _push(chat_id, "assistant", answer)
    await m.answer(answer)

# ===== Service commands =====

@router.message(F.text.regexp(r'(?i)^(стоп|stop)$'))
async def on_stop_word(m: Message):
    chat_id = m.chat.id
    if CHAT_MODE.get(chat_id) == "reflection":
        CHAT_MODE[chat_id] = "talk"
        await m.answer("Окей, выходим из режима рефлексии. Можем продолжить обычный разговор 💬")
@router.message(Command("ping"))
async def on_ping(m: Message):
    await m.answer("pong ✅")
