from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand, FSInputFile
)

from app.llm_adapter import LLMAdapter
from app.safety import is_crisis, CRISIS_REPLY
from app.db import db_session, User
from app.rag_qdrant import search as rag_search
from app.prompts import POMNI_MASTER_PROMPT, ASSISTANT_PROMPT
from app.memory import (
    add_journal_entry, update_user_memory, get_user_memory,
    get_user_settings, set_user_tone, set_user_method,
    is_help_intent, log_event
)

router = Router()
LLM = LLMAdapter()

# Последнее предложенное (для RAG диверсификации)
LAST_SUGGESTED = {}   # chat_id -> tag
# Режим дневника
DIARY_MODE = {}       # chat_id -> bool

# Главное меню (ReplyKeyboard)
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗣 Поговорить"), KeyboardButton(text="🛠 Разобраться")],
        [KeyboardButton(text="🎧 Медитации")],
        [KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True
)

async def _set_bot_commands(bot):
    cmds = [
        BotCommand(command="start", description="начать"),
        BotCommand(command="tone", description="стиль общения"),
        BotCommand(command="method", description="подход (КПТ/ACT/...)"),
        BotCommand(command="privacy", description="приватность дневника"),  # UI добавим следующим шагом
    ]
    await bot.set_my_commands(cmds)


# ---------- Онбординг ----------
@router.message(CommandStart())
async def start_cmd(message: Message):
    user_id = message.from_user.id
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(user_id)).first()
        if not u:
            s.add(User(tg_id=str(user_id)))
            s.commit()

    await _set_bot_commands(message.bot)
    DIARY_MODE[message.chat.id] = False

    # Карточка приветствия
    try:
        await message.answer_photo(
            FSInputFile("assets/illustrations/hello.png"),
            caption=("Привет! Я **Помни** — друг-психолог-дневник. "
                     "Можно просто поговорить — я рядом. Если нужно, разложу по шагам и дам маленькие практики.")
        )
    except Exception:
        await message.answer("Привет! Я Помни — друг-психолог-дневник.")

    await message.answer(
        "Выбери стиль в /tone (мягкий 💛, практичный ��, короткий ✂️, честный 🖤). "
        "С чего начнём — 🗣 Поговорить или 🛠 Разобраться?",
        reply_markup=MAIN_KB
    )


# ---------- Переключатели стиля/подхода ----------
@router.message(Command("tone"))
async def tone_cmd(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мягкий 💛", callback_data="tone:soft"),
         InlineKeyboardButton(text="Практичный 🧰", callback_data="tone:practical")],
        [InlineKeyboardButton(text="Короткий ✂️", callback_data="tone:concise"),
         InlineKeyboardButton(text="Честный 🖤", callback_data="tone:honest")],
    ])
    await message.answer("Выбери стиль общения:", reply_markup=kb)

@router.callback_query(F.data.startswith("tone:"))
async def tone_pick(cb: CallbackQuery):
    tone = cb.data.split(":")[1]
    set_user_tone(cb.from_user.id, tone)
    await cb.message.answer("Готово. Подстрою ответы под этот стиль.")
    await cb.answer()

@router.message(Command("method"))
async def method_cmd(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="КПТ", callback_data="method:cbt"),
         InlineKeyboardButton(text="ACT", callback_data="method:act")],
        [InlineKeyboardButton(text="Гештальт", callback_data="method:gestalt"),
         InlineKeyboardButton(text="Поддерживающий", callback_data="method:supportive")],
    ])
    await message.answer("Выбери основной подход:", reply_markup=kb)

@router.callback_query(F.data.startswith("method:"))
async def method_pick(cb: CallbackQuery):
    method = cb.data.split(":")[1]
    set_user_method(cb.from_user.id, method)
    await cb.message.answer("Принято. Буду опираться на этот подход.")
    await cb.answer()


# ---------- Навигация ----------
@router.message(Command("diary"))
@router.message(F.text == "🗣 Поговорить")
async def enter_diary(message: Message):
    DIARY_MODE[message.chat.id] = True
    await message.answer(
        "Я рядом и слушаю 💛 Просто расскажи, что на душе. "
        "Если захочешь — разложу по шагам и подскажу, как действовать.",
        reply_markup=MAIN_KB
    )

@router.message(Command("tools"))
@router.message(F.text == "🛠 Разобраться")
async def tools_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Тревога/стресс", callback_data="triage:stress"),
         InlineKeyboardButton(text="Самооценка", callback_data="triage:self")],
        [InlineKeyboardButton(text="Отношения", callback_data="triage:rel"),
         InlineKeyboardButton(text="Сон", callback_data="triage:sleep")],
        [InlineKeyboardButton(text="Продуктивность", callback_data="triage:prod"),
         InlineKeyboardButton(text="Другое", callback_data="triage:other")],
    ])
    await message.answer("Выбери направление — разложу по шагам и предложу практику:", reply_markup=kb)

@router.callback_query(F.data.startswith("triage:"))
async def triage_pick(cb: CallbackQuery):
    topic = cb.data.split(":")[1]
    topic_q = {
        "stress": "Снижение тревоги и стресс-коупинг",
        "self": "Самокритика и самооценка",
        "rel": "Отношения и границы",
        "sleep": "Проблемы со сном",
        "prod": "Прокрастинация и продуктивность",
        "other": "Самопомощь и рефлексия"
    }.get(topic, "Самопомощь")
    chunks = rag_search(topic_q, last_suggested_tag=LAST_SUGGESTED.get(cb.message.chat.id), mode="assist")
    ctx = "\n\n".join([c.get("text","") for c in chunks])[:1400]
    reply = await LLM.complete_chat(
        system=ASSISTANT_PROMPT.format(
            tone_desc="тёплый и спокойный", method_desc="КПТ/ACT/гештальт — по месту"
        ),
        user=f"Тема: {topic_q}\nКонтекст:\n{ctx}"
    )
    await cb.message.answer(reply)
    await cb.answer()


# ---------- Главный обработчик (свободный дневник по умолчанию) ----------
@router.message()
async def diary_or_general(message: Message):
    text = message.text or ""
    if is_crisis(text):
        await message.answer(CRISIS_REPLY)
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    DIARY_MODE.setdefault(chat_id, True)  # по умолчанию — «Поговорить»

    if DIARY_MODE[chat_id]:
        # 1) Сохраняем запись и обновляем персональную память (пока без UI приватности)
        add_journal_entry(user_id=user_id, text=text)
        update_user_memory(user_id=user_id, new_text=text, adapter=LLM)
        summary = get_user_memory(user_id)

        # 2) Достаём настройки тона/подхода
        st = get_user_settings(user_id)
        tone_desc = {
            "soft":"очень тёплый и бережный",
            "practical":"спокойный и ориентированный на действия",
            "concise":"краткий и по делу",
            "honest":"прямой, без приукрас, но уважительный"
        }.get(st.tone, "тёплый")
        method_desc = {
            "cbt":"КПТ (мысли↔эмоции↔поведение)",
            "act":"ACT (ценности, принятие, дефузия)",
            "gestalt":"гештальт (осознавание, контакт)",
            "supportive":"поддерживающий"
        }.get(st.method, "КПТ")

        # 3) Контекст из RAG: в режиме свободного чата скрытно фильтруем «breathing»
        chunks = rag_search(text, last_suggested_tag=LAST_SUGGESTED.get(chat_id), mode="chat")
        ctx = "\n\n".join([c.get("text","") for c in chunks])[:1400]

        # 4) Friend-first или ассистентность по явному намерению
        system = POMNI_MASTER_PROMPT.format(tone_desc=tone_desc, method_desc=method_desc)
        if is_help_intent(text):
            system = ASSISTANT_PROMPT.format(tone_desc=tone_desc, method_desc=method_desc)

        reply = await LLM.complete_chat(system=system,
                                        user=f"Память:\n{summary}\n\nСообщение:\n{text}\n\nКонтекст:\n{ctx}")
        await message.answer(reply, reply_markup=MAIN_KB)
        log_event(user_id, "diary_message", "")
        return

    # Fallback (если внезапно не в дневнике)
    chunks = rag_search(text, last_suggested_tag=LAST_SUGGESTED.get(chat_id), mode="assist")
    ctx = "\n\n".join([c.get("text","") for c in chunks])[:1400]
    reply = await LLM.complete_chat(system=POMNI_MASTER_PROMPT.format(
                                        tone_desc="тёплый", method_desc="КПТ"),
                                    user=text + "\n\n" + ctx)
    await message.answer(reply, reply_markup=MAIN_KB)


# ====== Помни: приватность и дневник ======
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
try:
    from .memory import MemoryManager
    _mem = MemoryManager()
except Exception:
    _mem = None

@router.message(Command("privacy"))
async def cmd_privacy(message: Message, command: CommandObject):
    if _mem is None:
        await message.answer("Память временно недоступна.")
        return
    tg_id = str(message.from_user.id)
    arg = (command.args or "").strip().lower()
    if not arg:
        mode = _mem.get_privacy(tg_id)
        await message.answer(
            f"Приватность: *{mode}*.
Варианты: `ask` (спрашивать), `none` (не сохранять), `all` (сохранять всё).
Пример: `/privacy ask`",
            parse_mode="Markdown",
        )
        return
    if arg not in {"ask","none","all","insights"}:
        await message.answer("Выбери: ask | none | all")
        return
    _mem.set_privacy(tg_id, arg)
    await message.answer(f"Ок, режим приватности: *{arg}*", parse_mode="Markdown")
