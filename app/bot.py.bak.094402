from __future__ import annotations

import re
from typing import Dict, Optional

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.filters.command import CommandObject
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

# --------- Простое хранение состояния чата (in-memory) ----------
LAST_SUGGESTED: Dict[int, Optional[str]] = {}   # chat_id -> last rag tag
DIARY_MODE: Dict[int, bool] = {}                # chat_id -> bool (true = "Поговорить")
CURRENT_FOCUS: Dict[int, str] = {}              # chat_id -> последняя тема/мысль пользователя
FLOW_MODE: Dict[int, Optional[str]] = {}        # chat_id -> "reflect"|"microstep"|"pause"|None


# --------- Утилиты ----------
def _main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗣 Поговорить"), KeyboardButton(text="🛠 Разобраться")],
            [KeyboardButton(text="🎧 Медитации")],
            [KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )

def _flow_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤔 Рефлексия", callback_data="flow:reflect"),
         InlineKeyboardButton(text="🪜 Микрошаг", callback_data="flow:microstep")],
        [InlineKeyboardButton(text="⏸️ Пауза", callback_data="flow:pause")],
    ])

async def _set_bot_commands(bot):
    cmds = [
        BotCommand(command="start", description="начать"),
        BotCommand(command="tone", description="стиль общения"),
        BotCommand(command="method", description="подход (КПТ/ACT/...)"),
        BotCommand(command="privacy", description="приватность дневника"),
    ]
    await bot.set_my_commands(cmds)

async def _call_llm(system: str, user: str) -> str:
    # Совместимость с разными версиями адаптера
    if hasattr(LLM, "complete_chat"):
        return await LLM.complete_chat(system=system, user=user)
    return await LLM.chat(system=system, user=user)

def _detect_text_choice(text: str) -> Optional[str]:
    t = (text or "").lower()
    # Очень простая эвристика на русском
    if re.search(r"\b(рефлекс|порефлекс|разбер[её]м|разлож(им|ить))\b", t):
        return "reflect"
    if re.search(r"\b(микрошаг|маленьк(ий|ие) шаг|что сделать|с чего начать)\b", t):
        return "microstep"
    if re.search(r"\b(пауза|перерыв|отвлечься|передышка)\b", t):
        return "pause"
    return None

def _tone_desc(code: str) -> str:
    return {
        "soft":"очень тёплый и бережный",
        "practical":"спокойный и ориентированный на действия",
        "concise":"краткий и по делу",
        "honest":"прямой, без приукрас, но уважительный"
    }.get(code, "тёплый")

def _method_desc(code: str) -> str:
    return {
        "cbt":"КПТ (мысли↔эмоции↔поведение)",
        "act":"ACT (ценности, принятие, дефузия)",
        "gestalt":"гештальт (осознавание, контакт)",
        "supportive":"поддерживающий"
    }.get(code, "КПТ")


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
    DIARY_MODE[message.chat.id] = True
    FLOW_MODE[message.chat.id] = None
    CURRENT_FOCUS.pop(message.chat.id, None)

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
        "Выбери стиль в /tone (мягкий 💛, практичный ��, короткий ✂️, честный 🖤).\n"
        "С чего начнём — 🗣 Поговорить или 🛠 Разобраться?",
        reply_markup=_main_kb()
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
    FLOW_MODE[message.chat.id] = None
    CURRENT_FOCUS.pop(message.chat.id, None)
    await message.answer(
        "Я рядом и слушаю 💛 Просто расскажи, что на душе. "
        "Если захочешь — разложу по шагам и подскажу, как действовать.",
        reply_markup=_main_kb()
    )

@router.message(Command("tools"))
@router.message(F.text == "🛠 Разобраться")
async def tools_menu(message: Message):
    DIARY_MODE[message.chat.id] = False
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

    chunks = await rag_search(topic_q, last_suggested_tag=LAST_SUGGESTED.get(cb.message.chat.id))
    ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1400]

    st = get_user_settings(cb.from_user.id)
    system = ASSISTANT_PROMPT.format(tone_desc=_tone_desc(st.tone), method_desc=_method_desc(st.method))
    reply = await _call_llm(system=system, user=f"Тема: {topic_q}\nКонтекст:\n{ctx}")
    await cb.message.answer(reply, reply_markup=_main_kb())
    await cb.answer()


# ---------- Выбор «Рефлексия/Микрошаг/Пауза» (кнопки) ----------
@router.callback_query(F.data.startswith("flow:"))
async def flow_pick(cb: CallbackQuery):
    mode = cb.data.split(":")[1]  # reflect | microstep | pause
    chat_id = cb.message.chat.id
    FLOW_MODE[chat_id] = mode
    focus = CURRENT_FOCUS.get(chat_id, "").strip()

    st = get_user_settings(cb.from_user.id)
    tone = _tone_desc(st.tone)
    method = _method_desc(st.method)

    # Подготавливаем контекст RAG по теме
    chunks = await rag_search(focus or "эмоции и работа", last_suggested_tag=LAST_SUGGESTED.get(chat_id))
    ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1200]

    if mode == "reflect":
        # Мгновенно продолжаем по текущей теме, не спрашивая "о чём?"
        prompt = (
            f"Фокус рефлексии: «{focus or 'текущая тема пользователя'}».\n"
            "Начни коротко: 1) событие/контекст в 1 предложении (помоги сформулировать), "
            "2) мысль, 3) чувство (0–10). Затем предложи мини-проверку мысли (2 факта «за» / 2 «против») "
            "и одну альтернативную формулировку. В конце — 1 маленький шаг на сегодня."
        )
    elif mode == "microstep":
        prompt = (
            f"Фокус микрошагов: «{focus or 'текущая тема пользователя'}».\n"
            "Предложи 2–3 очень маленьких шага (на 5–10 минут) на выбор, "
            "сформулируй максимально конкретно. Затем спроси, какой выбрать."
        )
    else:  # pause
        prompt = (
            f"Фокус паузы: «{focus or 'текущая тема пользователя'}».\n"
            "Дай простую 2-минутную паузу: 1) внимание к дыханию (4 цикла), "
            "2) мягкий скан тела (30–40 сек), 3) верни фокус к ценности/намерению на сегодня в 1 предложении."
        )

    system = POMNI_MASTER_PROMPT.format(tone_desc=tone, method_desc=method)
    reply = await _call_llm(system=system, user=f"{prompt}\n\nКонтекст:\n{ctx}")
    await cb.message.answer(reply, reply_markup=_main_kb())
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

    # Сохраняем «фокус» темы (последнее содержательное сообщение)
    if text.strip():
        CURRENT_FOCUS[chat_id] = text.strip()

    if DIARY_MODE[chat_id]:
        # 1) Сохраняем запись и обновляем персональную память
        add_journal_entry(user_id=user_id, text=text)
        update_user_memory(user_id=user_id, new_text=text, adapter=LLM)
        summary = get_user_memory(user_id)

        # 2) Настройки тона/подхода
        st = get_user_settings(user_id)
        tone_desc = _tone_desc(st.tone)
        method_desc = _method_desc(st.method)

        # 3) Контекст из RAG — без «дыхания» в чате (фильтрация реализуется в rag слой)
        chunks = await rag_search(text, last_suggested_tag=LAST_SUGGESTED.get(chat_id))
        ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1400]

        # 4) Выбор потока: по тексту или по явным кнопкам
        choice = FLOW_MODE.get(chat_id) or _detect_text_choice(text)
        system = POMNI_MASTER_PROMPT.format(tone_desc=tone_desc, method_desc=method_desc)

        if choice == "reflect":
            focus = CURRENT_FOCUS.get(chat_id, text).strip()
            user = (
                f"Пользователь уже выбрал рефлексию по теме: «{focus}».\n"
                "Помоги пройти сверхкороткий ABC: 1) событие в 1 предложении, 2) мысль, 3) чувство (0–10), "
                "4) 2 факта за/2 против, 5) альтернативная мысль, 6) один маленький шаг на сегодня."
                f"\n\nПамять:\n{summary}\n\nКонтекст:\n{ctx}"
            )
            reply = await _call_llm(system=system, user=user)
            await message.answer(reply, reply_markup=_main_kb())
            FLOW_MODE[chat_id] = None
            try:
                log_event(str(user_id), "diary_message", "")
            except Exception as e:
                print("[bot] log_event error:", e)
            return

        if choice == "microstep":
            focus = CURRENT_FOCUS.get(chat_id, text).strip()
            user = (
                f"Запрос на микрошаг по теме: «{focus}».\n"
                "Предложи 2–3 очень маленьких, конкретных шага (5–10 минут) на выбор. Спроси, какой выбрать."
                f"\n\nПамять:\n{summary}\n\nКонтекст:\n{ctx}"
            )
            reply = await _call_llm(system=system, user=user)
            await message.answer(reply, reply_markup=_main_kb())
            FLOW_MODE[chat_id] = None
            try:
                log_event(str(user_id), "diary_message", "")
            except Exception as e:
                print("[bot] log_event error:", e)
            return

        if choice == "pause":
            focus = CURRENT_FOCUS.get(chat_id, text).strip()
            user = (
                f"Попросил короткую паузу по теме: «{focus}».\n"
                "Дай 2-мин инструкцию (дыхание/скан тела), затем один мягкий вопрос для возвращения к ценности/намерению на сегодня."
                f"\n\nПамять:\n{summary}\n\nКонтекст:\n{ctx}"
            )
            reply = await _call_llm(system=system, user=user)
            await message.answer(reply, reply_markup=_main_kb())
            FLOW_MODE[chat_id] = None
            try:
                log_event(str(user_id), "diary_message", "")
            except Exception as e:
                print("[bot] log_event error:", e)
            return

        # 5) Обычный «friend-first»: эмпатия + 1 уточнение + предложение кнопок (без «сохранить заметку»)
        ask_or_assist = ASSISTANT_PROMPT if is_help_intent(text) else POMNI_MASTER_PROMPT
        system = ask_or_assist.format(tone_desc=tone_desc, method_desc=method_desc)
        user = (
            "Продолжай диалог в тёплом стиле, 1 вопрос за раз. "
            "Если уместно — предложи выбрать: Рефлексия / Микрошаг / Пауза (но не навязывай).\n\n"
            f"Память:\n{summary}\n\nСообщение:\n{text}\n\nКонтекст:\n{ctx}"
        )
        reply = await _call_llm(system=system, user=user)
        await message.answer(reply, reply_markup=_flow_kb())
        try:
            log_event(str(user_id), "diary_message", "")
        except Exception as e:
            print("[bot] log_event error:", e)
        return

    # ----- Если не в режиме дневника: ассистентный ответ по RAG -----
    chunks = await rag_search(text, last_suggested_tag=LAST_SUGGESTED.get(chat_id))
    ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1400]
    system = ASSISTANT_PROMPT.format(tone_desc="тёплый", method_desc="КПТ")
    reply = await _call_llm(system=system, user=text + "\n\n" + ctx)
    await message.answer(reply, reply_markup=_main_kb())


# ====== Приватность (/privacy) ======
try:
    from .memory import MemoryManager  # type: ignore
    _mem = MemoryManager()
except Exception:
    _mem = None

@router.message(Command("privacy"))
async def cmd_privacy(message: Message, command: CommandObject):
    global _mem
    try:
        from .memory import MemoryManager  # type: ignore
        _mem = MemoryManager()
    except Exception:
        _mem = None

    if _mem is None:
        await message.answer("Память временно недоступна.")
        return

    tg_id = str(message.from_user.id)
    arg = (command.args or "").strip().lower()

    if not arg:
        mode = _mem.get_privacy(tg_id)
        text = (
            f"Приватность: *{mode}*\n"
            "Варианты: `ask` (спрашивать), `none` (не сохранять), `all` (сохранять всё).\n"
            "Пример: `/privacy ask`"
        )
        await message.answer(text, parse_mode="Markdown")
        return

    if arg not in {"ask","none","all","insights"}:
        await message.answer("Выбери: ask | none | all")
        return

    _mem.set_privacy(tg_id, arg)
    await message.answer(f"Ок, режим приватности: *{arg}*", parse_mode="Markdown")
