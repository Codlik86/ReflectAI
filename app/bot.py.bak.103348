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
CURRENT_FOCUS: Dict[int, str] = {}              # chat_id -> последняя мысль/тема (свободный текст)

# Текущая "подтема" (категория), и быстрый сдвиг в любой момент
CURRENT_TOPIC: Dict[int, str] = {}              # chat_id -> slug ("work", "rel", ...)
FLOW_MODE: Dict[int, Optional[str]] = {}        # chat_id -> "reflect"|"microstep"|"pause"|None

TOPIC_TITLES = {
    "work": "Работа",
    "rel": "Отношения",
    "self": "Самооценка",
    "sleep": "Сон",
    "prod": "Продуктивность",
    "health": "Здоровье",
    "money": "Деньги",
    "study": "Учёба",
    "family": "Семья",
    "friends": "Друзья",
    "mood": "Настроение",
    "other": "Другое",
}

TOPIC_KEYWORDS = {
    "work": r"\b(работа|офис|начальник|коллег|собеседовани|карьер|выгор|апати[яи])\b",
    "rel": r"\b(отношен|партн|парн|девушк|жен|муж|развод|ссора|конфликт|границ)\b",
    "self": r"\b(самооцен|самокрит|неуверен|стыд|вина|я ничт|я плох)\b",
    "sleep": r"\b(сон|бессонниц|заснуть|плохой сон|просн|поздно лег)\b",
    "prod": r"\b(прокрастин|продуктивн|дела|задач|срок|дедлайн)\b",
    "health": r"\b(здоровь|болит|панич|симптом|тревог[аи]|стресс)\b",
    "money": r"\b(деньг|зарплат|кредит|ипотек|расход|бюджет)\b",
    "study": r"\b(учеб|школ|универ|экзамен|сессия|курс|дз|домашк)\b",
    "family": r"\b(семь|родител|мам|пап|ребен|сын|дочь|дет)\b",
    "friends": r"\b(друз|компан|тусов|одиночеств|нет друзей)\b",
    "mood": r"\b(настроени|грусть|печаль|апати|радост|злость|раздраж)\b",
}

def _topic_title(slug: Optional[str]) -> str:
    return TOPIC_TITLES.get(slug or "", "Другое")

# --------- Утилиты ----------
def _main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗣 Поговорить"), KeyboardButton(text="�� Разобраться")],
            [KeyboardButton(text="🎧 Медитации")],
            [KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )

def _flow_kb(show_change_focus: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🤔 Рефлексия", callback_data="flow:reflect"),
         InlineKeyboardButton(text="🪜 Микрошаг", callback_data="flow:microstep")],
        [InlineKeyboardButton(text="⏸️ Пауза", callback_data="flow:pause")],
    ]
    if show_change_focus:
        rows.append([InlineKeyboardButton(text="🔁 Сменить фокус", callback_data="focus:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _focus_menu_kb(selected: Optional[str] = None) -> InlineKeyboardMarkup:
    # Делаем компактную сетку тем
    order = ["work","rel","self","sleep","prod","health","money","study","family","friends","mood","other"]
    buttons = []
    row = []
    for slug in order:
        title = _topic_title(slug)
        prefix = "• " if slug == selected else ""
        row.append(InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"setfocus:{slug}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Очистить фокус", callback_data="setfocus:")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def _set_bot_commands(bot):
    cmds = [
        BotCommand(command="start", description="начать"),
        BotCommand(command="tone", description="стиль общения"),
        BotCommand(command="method", description="подход (КПТ/ACT/...)"),
        BotCommand(command="privacy", description="приватность дневника"),
        BotCommand(command="focus", description="показать/сменить тему разговора"),
    ]
    await bot.set_my_commands(cmds)

async def _call_llm(system: str, user: str) -> str:
    if hasattr(LLM, "complete_chat"):
        return await LLM.complete_chat(system=system, user=user)
    return await LLM.chat(system=system, user=user)

def _detect_text_choice(text: str) -> Optional[str]:
    t = (text or "").lower()
    if re.search(r"\b(рефлекс|порефлекс|разбер[её]м|разлож(им|ить))\b", t):
        return "reflect"
    if re.search(r"\b(микрошаг|маленьк(ий|ие) шаг|что сделать|с чего начать)\b", t):
        return "microstep"
    if re.search(r"\b(пауза|перерыв|отвлечься|передышка)\b", t):
        return "pause"
    return None

def _guess_topic(text: str) -> Optional[str]:
    tt = (text or "").lower()
    for slug, pat in TOPIC_KEYWORDS.items():
        if re.search(pat, tt):
            return slug
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
        "cbt":"КПТ (мысли<->эмоции<->поведение)",
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
    chat_id = message.chat.id
    DIARY_MODE[chat_id] = True
    FLOW_MODE[chat_id] = None
    CURRENT_FOCUS.pop(chat_id, None)
    CURRENT_TOPIC.pop(chat_id, None)

    try:
        await message.answer_photo(
            FSInputFile("assets/illustrations/hello.png"),
            caption=("Привет! Я **Помни** — друг-психолог-дневник. "
                     "Можно просто поговорить — я рядом. Если нужно, разложу по шагам и дам маленькие практики.")
        )
    except Exception:
        await message.answer("Привет! Я Помни — друг-психолог-дневник.")

    await message.answer(
        "Выбери стиль в /tone (мягкий 💛, практичный 🧰, короткий ✂️, честный 🖤).\n"
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


# ---------- Фокус/тема (подтемы) ----------
@router.message(Command("focus"))
async def focus_cmd(message: Message):
    chat_id = message.chat.id
    cur = CURRENT_TOPIC.get(chat_id)
    title = _topic_title(cur) if cur else "не выбран"
    await message.answer(
        f"Текущая тема: *{title}*\nМожешь переключить ниже.",
        reply_markup=_focus_menu_kb(selected=cur),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "focus:menu")
async def focus_menu(cb: CallbackQuery):
    cur = CURRENT_TOPIC.get(cb.message.chat.id)
    await cb.message.answer("Выбери тему разговора:", reply_markup=_focus_menu_kb(selected=cur))
    await cb.answer()

@router.callback_query(F.data.startswith("setfocus:"))
async def focus_set(cb: CallbackQuery):
    slug = cb.data.split(":", 1)[1]  # может быть "" (очистить)
    chat_id = cb.message.chat.id
    user_id = cb.from_user.id
    if slug:
        CURRENT_TOPIC[chat_id] = slug
        await cb.message.answer(f"Ок, держим фокус на теме: *{_topic_title(slug)}*.", parse_mode="Markdown")
        try:
            log_event(str(user_id), "focus_set", slug)
        except Exception as e:
            print("[bot] log_event error:", e)
    else:
        CURRENT_TOPIC.pop(chat_id, None)
        await cb.message.answer("Фокус очищен. Пойму тему из контекста.")
        try:
            log_event(str(user_id), "focus_clear", "")
        except Exception as e:
            print("[bot] log_event error:", e)
    await cb.answer()


# ---------- Навигация ----------
@router.message(Command("diary"))
@router.message(F.text == "🗣 Поговорить")
async def enter_diary(message: Message):
    chat_id = message.chat.id
    DIARY_MODE[chat_id] = True
    FLOW_MODE[chat_id] = None
    # Фокус и тема сохраняем, они полезны между сессиями
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
    topic = CURRENT_TOPIC.get(chat_id)

    st = get_user_settings(cb.from_user.id)
    tone = _tone_desc(st.tone)
    method = _method_desc(st.method)

    # Контекст RAG по теме
    rag_query = " ".join(filter(None, [focus, _topic_title(topic)]))
    chunks = await rag_search(rag_query or "эмоции и самопомощь", last_suggested_tag=LAST_SUGGESTED.get(chat_id))
    ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1200]

    if mode == "reflect":
        prompt = (
            f"Рефлексия по теме: «{focus or _topic_title(topic)}». "
            "Сделай короткий ABC: 1) событие (1 предложение), 2) мысль, 3) чувство (0-10). "
            "Потом 2 факта за/2 против, альтернативная мысль и 1 маленький шаг."
        )
    elif mode == "microstep":
        prompt = (
            f"Микрошаги по теме: «{focus or _topic_title(topic)}». "
            "Предложи 2-3 конкретных шага на 5-10 минут, затем спроси, какой выбрать."
        )
    else:  # pause
        prompt = (
            f"Короткая пауза по теме: «{focus or _topic_title(topic)}». "
            "Дай инструкцию на ~2 минуты (дыхание, скан тела), затем мягкий вопрос к намерению на сегодня."
        )

    system = POMNI_MASTER_PROMPT.format(tone_desc=tone, method_desc=method)
    reply = await _call_llm(system=system, user=f"Текущая тема: {_topic_title(topic)}\n{prompt}\n\nКонтекст:\n{ctx}")
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
    DIARY_MODE.setdefault(chat_id, True)

    # Обновляем "фокус" и пробуем угадать подтему
    if text.strip():
        CURRENT_FOCUS[chat_id] = text.strip()
        guessed = _guess_topic(text)
        if guessed and not CURRENT_TOPIC.get(chat_id):
            # Предложим один раз за сессию взять тему
            CURRENT_TOPIC[chat_id] = guessed  # авто-фиксируем мягко
            try:
                log_event(str(user_id), "focus_auto", guessed)
            except Exception as e:
                print("[bot] log_event error:", e)
            await message.answer(
                f"Похоже, говорим про «{_topic_title(guessed)}». "
                f"Если захочешь — нажми «🔁 Сменить фокус» и выбери другую.",
                reply_markup=_flow_kb(show_change_focus=True)
            )

    if DIARY_MODE[chat_id]:
        # 1) Сохраняем запись и обновляем персональную память
        add_journal_entry(user_id=user_id, text=text)
        update_user_memory(user_id=user_id, new_text=text, adapter=LLM)
        summary = get_user_memory(user_id)

        # 2) Настройки тона/подхода
        st = get_user_settings(user_id)
        tone_desc = _tone_desc(st.tone)
        method_desc = _method_desc(st.method)

        # 3) Контекст из RAG: учитываем и текст, и тему
        topic = CURRENT_TOPIC.get(chat_id)
        rag_query = " ".join(filter(None, [text, _topic_title(topic)]))
        chunks = await rag_search(rag_query, last_suggested_tag=LAST_SUGGESTED.get(chat_id))
        ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1400]

        # 4) Выбор потока (по кнопке/по тексту)
        choice = FLOW_MODE.get(chat_id) or _detect_text_choice(text)
        system = POMNI_MASTER_PROMPT.format(tone_desc=tone_desc, method_desc=method_desc)

        if choice == "reflect":
            focus = CURRENT_FOCUS.get(chat_id, text).strip()
            user = (
                f"Текущая подтема: {_topic_title(topic)}.\n"
                f"Рефлексия по: «{focus or _topic_title(topic)}».\n"
                "Сделай ABC (коротко), дальше 2 за/2 против, альтернативная мысль, 1 маленький шаг."
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
                f"Текущая подтема: {_topic_title(topic)}.\n"
                f"Микрошаги по: «{focus or _topic_title(topic)}».\n"
                "Дай 2-3 очень маленьких шага (5-10 минут), затем спроси, какой выбрать."
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
                f"Текущая подтема: {_topic_title(topic)}.\n"
                f"Пауза по: «{focus or _topic_title(topic)}».\n"
                "Дай 2-минутную инструкцию (дыхание+скан тела), затем мягкий вопрос к намерению на сегодня."
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

        # 5) Базовый friend-first: держим тему и не сбрасываем контекст
        ask_or_assist = ASSISTANT_PROMPT if is_help_intent(text) else POMNI_MASTER_PROMPT
        system = ask_or_assist.format(tone_desc=tone_desc, method_desc=method_desc)
        user = (
            "Продолжай диалог в тёплом стиле, 1 вопрос за раз. "
            "Если уместно — предложи Рефлексию / Микрошаг / Пауза (без давления).\n\n"
            f"Текущая подтема: {_topic_title(topic)}\n"
            f"Память:\n{summary}\n\nСообщение:\n{text}\n\nКонтекст:\n{ctx}"
        )
        reply = await _call_llm(system=system, user=user)
        await message.answer(reply, reply_markup=_flow_kb(show_change_focus=True))
        try:
            log_event(str(user_id), "diary_message", "")
        except Exception as e:
            print("[bot] log_event error:", e)
        return

    # ----- Если не в режиме дневника: ассистентный ответ по RAG -----
    topic = CURRENT_TOPIC.get(chat_id)
    rag_query = " ".join(filter(None, [text, _topic_title(topic)]))
    chunks = await rag_search(rag_query, last_suggested_tag=LAST_SUGGESTED.get(chat_id))
    ctx = "\n\n".join([c.get("text","") for c in (chunks or [])])[:1400]
    system = ASSISTANT_PROMPT.format(tone_desc="тёплый", method_desc="КПТ")
    reply = await _call_llm(system=system, user=f"Текущая подтема: {_topic_title(topic)}\n" + text + "\n\n" + ctx)
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
