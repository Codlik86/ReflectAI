# app/bot.py
from __future__ import annotations

import os
import hashlib
from typing import Dict, List, Optional, Tuple

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

# ===== Внутренние модули =====
from .exercises import EXERCISES  # ожидается структура как в твоём exercises.py
from .prompts import SYSTEM_PROMPT as BASE_PROMPT
from .prompts import TALK_SYSTEM_PROMPT as TALK_PROMPT  # базовый для /talk
try:
    from .prompts import REFLECTIVE_SUFFIX  # необязательно; используется для рефлексии
except Exception:
    REFLECTIVE_SUFFIX = "\n\n(Режим рефлексии: мягко замедляй темп, задавай вопросы, помогающие осмыслению.)"

# LLM-обёртка (подхватываем твою реализацию)
try:
    from .llm_adapter import chat_with_style  # должен уметь принимать system/messages/style/rag_ctx
except Exception:
    chat_with_style = None  # на всякий, чтобы не падать при импорте в процессе наладки

# RAG (не обязателен, но если есть — подмешаем)
try:
    from .rag_qdrant import retrieve_relevant_context
except Exception:
    retrieve_relevant_context = None

router = Router()

# ===== Онбординг: изображения и ссылки =====
POLICY_URL = os.getenv("POLICY_URL", "https://s.craft.me/APV7T8gRf3w2Ay")
TERMS_URL = os.getenv("TERMS_URL", "https://s.craft.me/APV7T8gRf3w2Ay")

DEFAULT_ONB_IMAGES = {
    "cover": os.getenv("ONB_IMG_COVER", ""),
    "talk": os.getenv("ONB_IMG_TALK", ""),
    "work": os.getenv("ONB_IMG_WORK", ""),
    "meditations": os.getenv("ONB_IMG_MEDIT", ""),
}

def get_onb_image(key: str) -> str:
    return DEFAULT_ONB_IMAGES.get(key, "") or ""

# ===== Глобальные состояния чата (память в пределах процесса) =====
CHAT_MODE: Dict[int, str] = {}        # chat_id -> "talk" | "work" | "reflection"
USER_TONE: Dict[int, str] = {}        # chat_id -> "default" | "friend" | "therapist" | "18plus"
PRIVACY_FLAGS: Dict[int, Dict[str, bool]] = {}  # chat_id -> {"save_history": True}

# ===== Универсальные хелперы =====
async def _safe_edit(msg: Message, text: Optional[str] = None, reply_markup: Optional[InlineKeyboardMarkup] = None):
    """
    Редактирует текст/клавиатуру, если сообщение можно редактировать,
    иначе отвечает новым сообщением.
    """
    try:
        if text is not None and reply_markup is not None:
            await msg.edit_text(text, reply_markup=reply_markup)
        elif text is not None:
            await msg.edit_text(text)
        elif reply_markup is not None:
            await msg.edit_reply_markup(reply_markup=reply_markup)
        else:
            return
    except Exception:
        # если не удалось отредактировать — отправляем новое сообщение
        if text is not None:
            await msg.answer(text, reply_markup=reply_markup)
        elif reply_markup is not None:
            await msg.answer(".", reply_markup=reply_markup)

def _emoji_by_topic(tid: str, title: str) -> str:
    """
    Аккуратно назначаем эмодзи теме: берём из EXERCISES[tid]['emoji'], иначе
    устойчивый фолбэк по хешу из пула.
    """
    t = EXERCISES.get(tid, {})
    e = str(t.get("emoji") or "").strip()
    if e:
        return e
    pool = ["🌱", "🌿", "🌸", "🌙", "☀️", "🔥", "🧭", "🧠", "🛠️", "💡", "🧩", "🎯", "🌊", "🫶", "✨"]
    idx = int(hashlib.md5((tid or title).encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]

def _topic_title_with_emoji(tid: str) -> str:
    d = EXERCISES.get(tid, {})
    title = d.get("title", tid)
    return f"{_emoji_by_topic(tid, title)} {title}"

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

def kb_settings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎚 Тон общения", callback_data="settings:tone")],
            [InlineKeyboardButton(text="🔒 Приватность", callback_data="settings:privacy")],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:main")],
        ]
    )

def kb_tone_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Универсальный (по умолчанию)", callback_data="tone:default")],
            [InlineKeyboardButton(text="🤝 Друг/подруга", callback_data="tone:friend")],
            [InlineKeyboardButton(text="🧠 Психологичный", callback_data="tone:therapist")],
            [InlineKeyboardButton(text="🌶️ 18+", callback_data="tone:18plus")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings")],
        ]
    )

def kb_privacy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить историю", callback_data="privacy:clear")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings")],
        ]
    )

def kb_topics() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for tid, meta in EXERCISES.items():
        if tid == "reflection":  # "Рефлексия" внутри тем как отдельный элемент
            title = meta.get("title", "Рефлексия")
        else:
            title = meta.get("title", tid)
        rows.append([InlineKeyboardButton(text=_topic_title_with_emoji(tid), callback_data=f"work:{tid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def step_keyboard(tid: str, eid: str, idx: int, total: int) -> InlineKeyboardMarkup:
    prev_idx = max(0, idx - 1)
    next_idx = min(total - 1, idx + 1)
    buttons: List[List[InlineKeyboardButton]] = []
    nav: List[InlineKeyboardButton] = []
    nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex:{tid}:{eid}:{prev_idx}"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"ex:{tid}:{eid}:{next_idx}"))
    else:
        nav.append(InlineKeyboardButton(text="✅ Завершить", callback_data=f"ex:{tid}:{eid}:finish"))
    buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ===== Онбординг: тексты и клавиатуры =====
ONB_1_TEXT = (
    "Привет! Здесь ты можешь выговориться, разобрать ситуацию и найти опору.\n"
    "Я рядом и помогу — бережно и без оценок."
)

def kb_onb_step1() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вперёд ➜", callback_data="onb:step2")]
        ]
    )

ONB_2_TEXT = (
    "Прежде чем мы познакомимся, подтвердим правила и политику. "
    "Это нужно, чтобы нам обоим было спокойно и безопасно."
)

def kb_onb_step2() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📄 Правила", url=TERMS_URL),
            InlineKeyboardButton(text="🔐 Политика", url=POLICY_URL),
        ],
        [InlineKeyboardButton(text="Принимаю ✅", callback_data="onb:agree")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

WHAT_NEXT_TEXT = (
    "Что дальше? Несколько вариантов:\n\n"
    "1) Если хочешь просто поговорить — нажми «Поговорить». Поделись, что у тебя на душе, я поддержу и помогу разобраться.\n"
    "2) Нужен оперативный разбор — заходи в «Разобраться». Там короткие упражнения на разные темы.\n"
    "3) Хочешь аудио-передышку — «Медитации». (Скоро добавим подборку коротких аудио.)\n\n"
    "Пиши, как удобно — я рядом 🖤"
)

def kb_onb_step3() -> ReplyKeyboardMarkup:
    # Открываем сразу правое меню (reply-клавиатуру)
    return kb_main_menu()

# ===== Маршруты: меню и онбординг =====
@router.message(CommandStart())
async def on_start(m: Message):
    CHAT_MODE[m.chat.id] = "talk"
    # шаг 1: карточка с «Вперёд»
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
    img = get_onb_image("talk")
    if img:
        try:
            await cb.message.answer_photo(img, caption=ONB_2_TEXT, reply_markup=kb_onb_step2())
            await cb.answer()
            return
        except Exception:
            pass
    await cb.message.answer(ONB_2_TEXT, reply_markup=kb_onb_step2())
    await cb.answer()

@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    # шаг 3: «Что дальше?» + сразу показываем правое меню как reply-клавиатуру
    kb = kb_main_menu()
    img = get_onb_image("work")
    if img:
        try:
            await cb.message.answer_photo(img, caption=WHAT_NEXT_TEXT, reply_markup=kb)
            await cb.answer()
            return
        except Exception:
            pass
    # если фото не отправилось — отправим просто текст с клавиатурой
    await cb.message.answer(WHAT_NEXT_TEXT, reply_markup=kb)
    await cb.answer()

# ===== Кнопки меню (reply-клавиатура) =====
@router.message(F.text.in_(["🌿 Разобраться", "/work"]))
async def on_work_menu(m: Message):
    CHAT_MODE[m.chat.id] = "work"
    await m.answer("Выбирай тему:", reply_markup=ReplyKeyboardRemove())
    await m.answer(".", reply_markup=kb_topics())

@router.message(F.text.in_(["💬 Поговорить", "/talk"]))
async def on_talk(m: Message):
    CHAT_MODE[m.chat.id] = "talk"
    await m.answer("Я рядом и слушаю. О чём хочется поговорить?", reply_markup=kb_main_menu())

@router.message(F.text.in_(["🎧 Медитации", "/meditations", "/meditation"]))
async def on_meditations(m: Message):
    txt = (
        "🎧 Медитации скоро будут здесь. Мы готовим короткие аудио для тревоги, сна и восстановления. "
        "Пока можешь попробовать дыхание «квадрат 4-4-4-4» в «Разобраться»."
    )
    await m.answer(txt, reply_markup=kb_main_menu())

@router.message(F.text.in_(["⚙️ Настройки", "/settings", "/setting"]))
async def on_settings(m: Message):
    await m.answer("Настройки:", reply_markup=kb_main_menu())
    await m.answer(
        "Выбери, что настроить:",
        reply_markup=kb_settings()
    )

@router.callback_query(F.data == "menu:main")
async def on_menu_main(cb: CallbackQuery):
    await cb.message.answer("Меню:", reply_markup=kb_main_menu())
    await cb.answer()

@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(cb: CallbackQuery):
    await _safe_edit(cb.message, "Настройки:", reply_markup=kb_settings())
    await cb.answer()

# ===== Тон общения (/tone) =====
@router.message(Command("tone"))
async def on_tone_cmd(m: Message):
    await m.answer("Выбери тон общения. Он накладывается поверх базового промпта:", reply_markup=kb_tone_picker())

@router.callback_query(F.data.startswith("tone:"))
async def on_tone_pick(cb: CallbackQuery):
    style = cb.data.split(":", 1)[1]
    USER_TONE[cb.message.chat.id] = style
    await cb.answer("Стиль обновлён ✅", show_alert=False)
    await _safe_edit(cb.message, f"Тон общения установлен: <b>{style}</b> ✅", reply_markup=kb_settings())

# ===== Приватность (/privacy) =====
@router.message(Command("privacy"))
async def on_privacy_cmd(m: Message):
    flags = PRIVACY_FLAGS.setdefault(m.chat.id, {"save_history": True})
    state = "включено" if flags.get("save_history", True) else "выключено"
    await m.answer(f"Хранение истории сейчас: <b>{state}</b>.", reply_markup=kb_privacy())

@router.callback_query(F.data == "privacy:clear")
async def on_privacy_clear(cb: CallbackQuery):
    # Заглушка: здесь можно реально почистить свою БД/хранилище
    await cb.answer("История удалена ✅", show_alert=True)
    await _safe_edit(cb.message, "Готово. Что дальше?", reply_markup=kb_settings())

# ===== Список тем/упражнений =====
@router.callback_query(F.data.startswith("work:"))
async def on_topic_pick(cb: CallbackQuery):
    tid = cb.data.split(":", 1)[1]
    tdata = EXERCISES.get(tid)
    if not tdata:
        await cb.answer("Тема не найдена", show_alert=True)
        return
    title = tdata.get("title", tid)
    buttons: List[List[InlineKeyboardButton]] = []
    # «Рефлексия» как свободный чат:
    if tid == "reflection":
        buttons.append([InlineKeyboardButton(text="Начать рефлексию", callback_data="reflect:start")])
    else:
        for ex_id, ex in tdata.get("items", {}).items():
            ex_title = ex.get("title", ex_id)
            buttons.append([InlineKeyboardButton(text=ex_title, callback_data=f"ex:{tid}:{ex_id}:start")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:work")])
    await _safe_edit(
        cb.message,
        f"<b>{_topic_title_with_emoji(tid)}</b>",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await cb.answer()

@router.callback_query(F.data == "menu:work")
async def on_menu_work(cb: CallbackQuery):
    await _safe_edit(cb.message, "Выбирай тему:", reply_markup=kb_topics())
    await cb.answer()

# ===== Упражнения по шагам — в одном сообщении =====
@router.callback_query(F.data.startswith("ex:"))
async def on_ex_click(cb: CallbackQuery):
    """
    Формат callback_data: ex:<tid>:<eid>:<idx|start|finish>
    """
    try:
        _, tid, eid, action = cb.data.split(":")
    except Exception:
        await cb.answer()
        return

    topic = EXERCISES.get(tid, {})
    ex = topic.get("items", {}).get(eid, {})
    if not ex:
        await cb.answer("Упражнение не найдено", show_alert=True)
        return

    steps: List[str] = ex.get("steps") or []
    intro: str = ex.get("intro") or ""

    if action == "finish":
        await _safe_edit(cb.message, "Готово. Вернёмся к теме?", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"work:{tid}")]]
        ))
        await cb.answer()
        return

    if action == "start":
        if intro:
            await _safe_edit(cb.message, intro, reply_markup=step_keyboard(tid, eid, 0, max(1, len(steps))))
        else:
            txt = steps[0] if steps else "Шагов нет."
            await _safe_edit(cb.message, txt, reply_markup=step_keyboard(tid, eid, 0, max(1, len(steps))))
        await cb.answer()
        return

    # action — это индекс шага
    try:
        idx = int(action)
    except Exception:
        idx = 0

    total = max(1, len(steps))
    idx = max(0, min(idx, total - 1))
    step_text = steps[idx] if steps else "Шагов нет."

    await _safe_edit(cb.message, step_text, reply_markup=step_keyboard(tid, eid, idx, total))
    await cb.answer()

# ===== Рефлексия — свободный чат =====
@router.callback_query(F.data == "reflect:start")
async def on_reflect_start(cb: CallbackQuery):
    CHAT_MODE[cb.message.chat.id] = "reflection"
    await _safe_edit(cb.message, "Давай немного притормозим и прислушаемся к себе. "
                                  "Можешь начать с того, что больше всего откликается сейчас.")
    await cb.answer()

# ===== Простые команды / о проекте / помощь / оплата =====
@router.message(Command("about"))
async def on_about(m: Message):
    await m.answer("«Помни» — тёплый помощник, который помогает выговориться и прояснить мысли. "
                   "Здесь бережно, безоценочно, с опорой на научный подход.")

@router.message(Command("pay"))
async def on_pay(m: Message):
    await m.answer("Подписка скоро появится. Мы готовим удобные тарифы.")

@router.message(Command("help"))
async def on_help(m: Message):
    await m.answer("Если нужна помощь по сервису, напиши на support@remember.example — мы ответим.")

# ===== Общий чат: подмешиваем промпт + RAG + тон =====
def _style_overlay(style_key: str | None) -> str:
    if not style_key or style_key == "default":
        return ""
    if style_key == "friend":
        return "Стиль: тёплый, дружеский, на «ты». Поддерживай и говори простыми словами."
    if style_key == "therapist":
        return "Стиль: бережный, психологичный, задавай мягкие проясняющие вопросы, избегай диагнозов."
    if style_key == "18plus":
        return "Стиль: допускается разговорно, чуть смелее формулировки, но без грубости и токсичности."
    return ""

async def _answer_with_llm(m: Message, user_text: str):
    chat_id = m.chat.id
    mode = CHAT_MODE.get(chat_id, "talk")
    style_key = USER_TONE.get(chat_id, "default")

    # Базовый системный промпт
    sys_prompt = TALK_PROMPT if mode in ("talk", "reflection") else BASE_PROMPT
    overlay = _style_overlay(style_key)
    if overlay:
        sys_prompt = sys_prompt + "\n\n" + overlay
    if mode == "reflection" and REFLECTIVE_SUFFIX:
        sys_prompt = sys_prompt + "\n\n" + REFLECTIVE_SUFFIX

    rag_ctx = ""
    if retrieve_relevant_context:
        try:
            rag_ctx = retrieve_relevant_context(user_text) or ""
            if rag_ctx:
                sys_prompt = (
                    sys_prompt
                    + "\n\n[Контекст из проверенных источников — используй аккуратно, не раскрывай ссылки пользователю]\n"
                    + rag_ctx
                )
        except Exception:
            pass

    if chat_with_style is None:
        # на случай, если адаптер ещё не подключен
        await m.answer("Я тебя слышу. Сейчас подключаюсь… (LLM-адаптер не сконфигурирован)")
        return

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_text},
    ]

    try:
        reply = await chat_with_style(messages=messages, style_key=style_key)
    except TypeError:
        # совместимость с разными сигнатурами
        reply = await chat_with_style(messages, style_key=style_key)

    if not reply:
        reply = "Я рядом. Давай попробуем ещё раз сформулировать мысль?"
    await m.answer(reply, reply_markup=kb_main_menu())

# ===== Обработка произвольного текста: talk/reflection =====
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    # в любом режиме, где ожидается чат
    if CHAT_MODE.get(m.chat.id, "talk") in ("talk", "reflection"):
        await _answer_with_llm(m, m.text)
        return
    # если человек в «Разобраться», а пишет текст — мягко направим
    if CHAT_MODE.get(m.chat.id) == "work":
        await m.answer("Если хочешь обсудить — нажми «Поговорить». "
                       "Если упражнение — выбери тему в «Разобраться».", reply_markup=kb_main_menu())
        return
    # дефолт
    await m.answer("Я рядом и на связи. Нажми «Поговорить» или «Разобраться».", reply_markup=kb_main_menu())

# ===== Доп. команды-синонимы =====
@router.message(Command("menu"))
async def on_menu(m: Message):
    await m.answer("Меню:", reply_markup=kb_main_menu())

# Служебная: открыть список тем (удобно после онбординга)
@router.message(Command("work"))
async def on_work_cmd(m: Message):
    await on_work_menu(m)
