# -*- coding: utf-8 -*-
from __future__ import annotations

from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ======================= УНИВЕРСАЛЬНОЕ РЕДАКТИРОВАНИЕ =======================

async def smart_edit(message, text: str, **kwargs):
    """
    Безопасное редактирование (и текста, и подписи к медиа).
    Если редактировать нельзя — отправляет новое сообщение.
    Игнорирует "message is not modified".
    """
    try:
        if getattr(message, "text", None) is not None:
            if message.text != text or kwargs.get("reply_markup") is not None:
                await message.edit_text(text, **kwargs)
            return
        if getattr(message, "caption", None) is not None:
            if message.caption != text or kwargs.get("reply_markup") is not None:
                await message.edit_caption(text, **kwargs)
            return
        await message.answer(text, **kwargs)
    except Exception as e:
        try:
            # иногда Telegram ругается на edit; отправляем новое
            await message.answer(text, **kwargs)
        except Exception:
            pass

# ================================ ИМПОРТЫ ===================================

import asyncio
import os
from typing import Dict, List, Optional, Tuple

from aiogram import Router, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)

# Внутренние модули приложения
from app.exercises import TOPICS  # словарь тем/упражнений
from app.safety import is_crisis, CRISIS_REPLY
from app.llm_adapter import LLMAdapter

# RAG — может быть отключён; подхватываем мягко
try:
    from app.rag_qdrant import search as rag_search, search_with_meta as rag_meta
except Exception:
    rag_search = None
    rag_meta = None

# =========================== ЭМОДЗИ И НАСТРОЙКИ =============================

EMO_TALK = "💬"
EMO_HERB = "🌿"
EMO_HEADPHONES = "🎧"
EMO_GEAR = "⚙️"

# Иконки тем (уникальные, без дубликатов)
DEFAULT_TOPIC_ICON = "🌿"
TOPIC_ICONS = {
    "reflection": "🪞",
    "anxiety": "🌬️",
    "anger": "🔥",
    "pain_melancholy": "🌧️",
    "sleep": "🌙",
    "breath_body": "🧘",
    "procrastination": "⏳",
    "burnout": "🪫",
    "decisions": "🧭",
    "social_anxiety": "🗣️",
}
def topic_icon(tid: str, t: dict) -> str:
    return TOPIC_ICONS.get(tid, t.get("icon", DEFAULT_TOPIC_ICON))

# Картинки в разделах (обложки)
ONB_IMAGES = {
    "cover": "https://file.garden/aML3M6Sqrg21TaIT/kind-creature-min.jpg",
    "talk": "https://file.garden/aML3M6Sqrg21TaIT/warm-conversation-min.jpg",
    "work": "https://file.garden/aML3M6Sqrg21TaIT/_practices-min.jpg",
    "meditations": "https://file.garden/aML3M6Sqrg21TaIT/meditation-min.jpg",
}

# ============================== РОУТЕР БОТА =================================

router = Router()

# ============================== КЛАВИАТУРЫ ==================================

def kb_main() -> ReplyKeyboardMarkup:
    # Правое меню
    rows = [
        [KeyboardButton(text=f"{EMO_TALK} Поговорить")],
        [KeyboardButton(text=f"{EMO_HERB} Разобраться")],
        [KeyboardButton(text=f"{EMO_HEADPHONES} Медитации")],
        [KeyboardButton(text=f"{EMO_GEAR} Настройки")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def kb_topics() -> InlineKeyboardMarkup:
    # Список тем из exercises.TOPICS
    rows: List[List[InlineKeyboardButton]] = []
    for tid, t in TOPICS.items():
        title = t.get("title", tid)
        rows.append([
            InlineKeyboardButton(
                text=f"{topic_icon(tid, t)} {title}",
                callback_data=f"work:topic:{tid}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_exercises(topic_id: str) -> InlineKeyboardMarkup:
    # Список упражнений в теме
    t = TOPICS.get(topic_id, {})
    items = t.get("exercises") or []
    rows: List[List[InlineKeyboardButton]] = []
    for ex in items:
        ex_id = ex.get("id")
        ex_title = ex.get("title", ex_id or "Упражнение")
        rows.append([
            InlineKeyboardButton(
                text=f"🧩 {ex_title}",
                callback_data=f"work:ex:{topic_id}:{ex_id}",
            )
        ])
    # Навигация
    rows.append([
        InlineKeyboardButton(text="◀️ К темам", callback_data="work:back_topics"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_back_to_topics(topic_id: Optional[str] = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🌿 Другие темы", callback_data="work:back_topics")]]
    if topic_id:
        rows.insert(0, [InlineKeyboardButton(text="◀️ Назад к упражнениям", callback_data=f"work:topic:{topic_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_stepper(topic_id: str, ex_id: str, cur: int, total: int) -> InlineKeyboardMarkup:
    is_last = (cur >= total - 1)
    next_text = "✔️ Завершить" if is_last else "▶️ Далее"
    rows = [
        [InlineKeyboardButton(text=next_text, callback_data=f"work:step:{topic_id}:{ex_id}")],
        [
            InlineKeyboardButton(text="◀️ К упражнениям", callback_data=f"work:topic:{topic_id}"),
            InlineKeyboardButton(text="⏹ Стоп", callback_data="work:stop"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ====================== ВСПОМОГАТЕЛЬНЫЕ РЕНДЕР-ФУНКЦИИ ======================

def render_step_text(topic_title: str, ex_title: str, step_text: str) -> str:
    return (
        f"Тема: {topic_title}\n"
        f"Упражнение: {ex_title}\n\n"
        f"{step_text}"
    )

# ================================ /START ====================================

@router.message(Command("start"))
async def cmd_start(m: Message):
    return await show_onboarding(m)

# ============================== КНОПКИ МЕНЮ ================================

@router.message(F.text == f"{EMO_TALK} Поговорить")
@router.message(Command("talk"))
async def on_btn_talk(m: Message):
    # Картинка + мягкое приглашение
    try:
        await m.answer_photo(
            ONB_IMAGES["talk"],
            caption="Я рядом. Расскажи, что на душе — начнём с этого.",
        )
    except Exception:
        await m.answer("Я рядом. Расскажи, что на душе — начнём с этого.")

@router.message(F.text == f"{EMO_HERB} Разобраться")
@router.message(Command("work"))
async def on_btn_work(m: Message):
    try:
        await m.answer_photo(
            ONB_IMAGES["work"],
            caption="Выбери тему, с которой хочешь поработать:",
            reply_markup=kb_topics(),
        )
    except Exception:
        await m.answer("Выбери тему, с которой хочешь поработать:", reply_markup=kb_topics())

@router.message(F.text == f"{EMO_HEADPHONES} Медитации")
@router.message(Command("meditations"))
async def on_btn_meditations(m: Message):
    try:
        await m.answer_photo(
            ONB_IMAGES["meditations"],
            caption="Раздел с аудио-медитациями скоро добавим. Пока можно выбрать упражнения в «Разобраться».",
        )
    except Exception:
        await m.answer("Раздел с аудио-медитациями скоро добавим. Пока можно выбрать упражнения в «Разобраться».")

@router.message(F.text == f"{EMO_GEAR} Настройки")
@router.message(Command("settings"))
async def on_btn_settings(m: Message):
    await m.answer("Настройки: тон, подход и приватность — скоро появятся.", reply_markup=kb_main())

@router.message(Command("about"))
async def cmd_about(m: Message):
    await m.answer(
        "«Помни» — тёплый AI-друг/дневник. Помогает мягко разобраться в переживаниях, "
        "предлагает короткие упражнения и микрошаги. Не заменяет терапию, не ставит диагнозов."
    )

@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer("Напиши, что происходит — я поддержу, предложу 1–2 идеи и простое упражнение. 🌿")

@router.message(Command("pay"))
async def cmd_pay(m: Message):
    await m.answer("Поддержать проект: скоро добавим способы. Спасибо за желание помочь! ❤️")

@router.message(Command("policy"))
async def cmd_policy(m: Message):
    await m.answer("Приватность и правила: скоро оформим страницу. Главное — уважение, бережность и безопасность.")

@router.message(Command("ping"))
async def cmd_ping(m: Message):
    await m.answer("pong ✅")

# =========================== «РАЗОБРАТЬСЯ» (CALLBACK) ======================

# Память шага упражнения (in-memory; для MVP хватает)
_WS: Dict[str, Dict[str, object]] = {}  # by user_id: {topic_id, ex_id, step, steps}

def _ws_get(uid: str) -> Dict[str, object]:
    return _WS.get(uid, {})

def _ws_set(uid: str, **fields):
    st = _WS.get(uid) or {}
    st.update(fields)
    _WS[uid] = st

def _ws_reset(uid: str):
    _WS.pop(uid, None)

async def _silent_ack(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass

@router.callback_query(F.data == "work:back_topics")
async def cb_back_topics(cb: CallbackQuery):
    await _silent_ack(cb)
    await smart_edit(cb.message, text="Выбери тему, с которой хочешь поработать:", reply_markup=kb_topics())

@router.callback_query(F.data.startswith("work:topic:"))
async def cb_pick_topic(cb: CallbackQuery):
    await _silent_ack(cb)

    topic_id = cb.data.split(":")[2]
    t = TOPICS.get(topic_id, {})
    title = t.get("title", "Тема")
    intro = t.get("intro")

    # Тема типа chat → сразу свободный тёплый чат
    if t.get("type") == "chat":
        intro_long = t.get("intro_long") or intro or (
            "Давай немного поразмышляем. Напиши пару строк — что волнует, что хочется понять. Я рядом. 🌿"
        )
        await smart_edit(cb.message, text=f"Тема: {topic_icon(topic_id, t)} {title}\n\n{intro_long}")
        return

    # Обычная тема: интро + упражнения
    text = f"Тема: {topic_icon(topic_id, t)} {title}\n\n{intro}" if intro else \
           f"Ок, остаёмся в теме {topic_icon(topic_id, t)} «{title}». Выбери упражнение ниже."
    await smart_edit(cb.message, text=text, reply_markup=kb_exercises(topic_id))

@router.callback_query(F.data.startswith("work:ex:"))
async def cb_pick_exercise(cb: CallbackQuery):
    await _silent_ack(cb)

    _, _, topic_id, ex_id = cb.data.split(":", 3)
    t = TOPICS.get(topic_id, {})
    ex = next((e for e in (t.get("exercises") or []) if e.get("id") == ex_id), {})
    steps_all: List[str] = ex.get("steps") or []

    if not steps_all:
        await smart_edit(cb.message, text="Похоже, в этом упражнении пока нет шагов.", reply_markup=kb_exercises(topic_id))
        return

    _ws_set(str(cb.from_user.id), topic_id=topic_id, ex_id=ex_id, step=0, steps=steps_all)

    topic_title = t.get("title", "Тема")
    ex_title = ex.get("title") or "Упражнение"
    text = render_step_text(topic_title, ex_title, steps_all[0])
    await smart_edit(cb.message, text=text, reply_markup=kb_stepper(topic_id, ex_id, 0, len(steps_all)))

@router.callback_query(F.data.startswith("work:step:"))
async def cb_next_step(cb: CallbackQuery):
    await _silent_ack(cb)

    _, _, topic_id, ex_id = cb.data.split(":", 3)
    uid = str(cb.from_user.id)
    st = _ws_get(uid)
    steps_all: List[str] = st.get("steps") or []  # type: ignore

    if not steps_all:
        await smart_edit(cb.message, text="Кажется, шаги уже сброшены. Выбери упражнение ещё раз.", reply_markup=kb_exercises(topic_id))
        return

    cur = int(st.get("step", 0)) + 1  # type: ignore
    if cur >= len(steps_all):
        _ws_reset(uid)
        await smart_edit(cb.message, text="✅ Готово. Хочешь выбрать другое упражнение или тему?", reply_markup=kb_exercises(topic_id))
        return

    _ws_set(uid, step=cur)
    t = TOPICS.get(topic_id, {})
    ex = next((e for e in (t.get("exercises") or []) if e.get("id") == ex_id), {})
    topic_title = t.get("title", "Тема")
    ex_title = ex.get("title") or "Упражнение"
    text = render_step_text(topic_title, ex_title, steps_all[cur])
    await smart_edit(cb.message, text=text, reply_markup=kb_stepper(topic_id, ex_id, cur, len(steps_all)))

@router.callback_query(F.data == "work:stop")
async def cb_stop(cb: CallbackQuery):
    await _silent_ack(cb)
    _ws_reset(str(cb.from_user.id))
    await smart_edit(cb.message, text="Остановил упражнение. Можем просто поговорить или выбрать другую тему.", reply_markup=kb_topics())

# ============================== СВОБОДНЫЙ ДИАЛОГ ============================

_adapter: Optional[LLMAdapter] = None

@router.message(F.text)
async def on_text(m: Message):
    # игнорируем команды — они обрабатываются отдельно
    txt = (m.text or "").strip()
    if not txt or txt.startswith("/"):
        return

    global _adapter
    if _adapter is None:
        _adapter = LLMAdapter()

    # Лёгкая безопасностная проверка
    if is_crisis(txt):
        await m.answer(CRISIS_REPLY)
        return

    # Мягкий RAG (тихо, без «источников»)
    rag_ctx = ""
    if rag_search is not None:
        try:
            rag_ctx = await rag_search(txt, k=6, max_chars=int(os.getenv("RAG_MAX_CHARS", "1200")))
        except Exception:
            rag_ctx = ""

    # Сбор сообщений для LLM
    sys_hint = (
        "Ты — «Помни», тёплый бережный друг. Поддерживай, проясняй, предлагай мягкие шаги (10–30 минут). "
        "Без диагнозов и категоричности. Коротко и по-человечески."
    )
    messages = [
        {"role": "system", "content": sys_hint},
        {"role": "user", "content": txt},
    ]
    if rag_ctx:
        messages.insert(1, {"role": "system", "content": f"Полезные заметки (не цитируй источники вслух, говори от себя):\n{rag_ctx}"})

    # Ответ
    try:
        reply = await _adapter.complete_chat(user=str(m.from_user.id), messages=messages, temperature=0.7)
    except Exception:
        # последний шанс, чтобы бот не молчал
        reply = "Понимаю, как это может выматывать… Давай понемногу: что больше всего тревожит сейчас? 🌿"

    await m.answer(reply)

# ============================ ФОЛБЭК (НЕ МОЛЧАТЬ) ===========================

@router.message()
async def __last_resort(m: Message):
    try:
        txt = (m.text or "").strip()
        if txt:
            await m.answer("я здесь 🌿 " + (txt[:80] + ("…" if len(txt) > 80 else "")))
        else:
            await m.answer("я здесь 🌿")
    except Exception:
        pass


# ==== Онбординг: быстрые цели и старт ===============================

# Временное хранилище выборов (на сессию процесса)
from collections import defaultdict
_ONB_PREFS: dict[int, set[str]] = defaultdict(set)

def kb_onb_prefs():
    kb = InlineKeyboardBuilder()
    kb.button(text="🧘‍♂️ Снизить тревогу", callback_data="onb:p:anxiety")
    kb.button(text="🌙 Улучшить сон", callback_data="onb:p:sleep")
    kb.button(text="✨ Повысить самооценку", callback_data="onb:p:selfesteem")
    kb.button(text="🎯 Найти ресурсы и мотивацию", callback_data="onb:p:motivation")
    kb.button(text="✅ Готово", callback_data="onb:done")
    # по одному в ряд — как на скрине
    kb.adjust(1)
    return kb.as_markup()

async def show_onboarding(m: Message):
    text = (
        "👋 Привет, друг!\n\n"
        "Класс! Тогда пару быстрых настроек 🛠️\n\n"
        "Выбери, что сейчас важнее (можно несколько), а затем нажми «Готово»:"
    )
    # если есть ONB_IMAGES["cover"] — показываем красивую обложку;
    # иначе обычный текст
    try:
        img = ONB_IMAGES.get("cover")  # type: ignore[name-defined]
    except Exception:
        img = None
    if img:
        try:
            await m.answer_photo(img, caption=text, reply_markup=kb_onb_prefs())
            return
        except Exception:
            pass
    await m.answer(text, reply_markup=kb_onb_prefs())

@router.callback_query(F.data.startswith("onb:p:"))
async def onb_pick_pref(cb: CallbackQuery):
    uid = cb.from_user.id if cb.from_user else 0
    code = cb.data.split(":", 2)[-1]
    bucket = _ONB_PREFS[uid]
    # переключатель
    if code in bucket:
        bucket.remove(code)
        await cb.answer("Убрали из списка")
    else:
        bucket.add(code)
        await cb.answer("Добавлено ✔️")
    # тихо, без перерисовки клавиатуры (так надёжнее с медиа-сообщением)

@router.callback_query(F.data == "onb:done")
async def onb_done(cb: CallbackQuery):
    uid = cb.from_user.id if cb.from_user else 0
    chosen = _ONB_PREFS.pop(uid, set())

    # Текст как на скриншоте
    follow = (
        "Что дальше? Несколько вариантов:\n\n"
        f"1) Хочешь просто поговорить — нажми «Поговорить». Без рамок и практик: поделись тем, что происходит, я поддержу и помогу разложить.\n"
        f"2) Нужно быстро разобраться — открой «Разобраться». Там короткие упражнения на 5–10 минут: от дыхания и анти-катастрофизации до плана при панике и S-T-O-P.\n"
        f"3) Хочешь разгрузить голову — в «Медитациях» будут короткие аудио для тревоги, сна и концентрации — добавим совсем скоро.\n\n"
        "Пиши, как тебе удобно. Я рядом ❤️"
    )
    try:
        # если исходное сообщение было с фото — меняем подпись,
        # иначе просто отправим новое
        try:
            await cb.message.edit_caption(follow)
        except Exception:
            await cb.message.edit_text(follow)
    except Exception:
        await cb.message.answer(follow)

    # Покажем основное меню, если у тебя есть kb_main()
    try:
        await cb.message.answer("Выбирай, с чего начнём:", reply_markup=kb_main())  # type: ignore[name-defined]
    except Exception:
        pass
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton


# ===== Онбординг: клавиатуры и тексты ========================================
def kb_onb_consent() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👋 Привет, друг!")]],
        resize_keyboard=True
    )

def kb_onb_quick_setup() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🧘 Снизить тревогу")],
        [KeyboardButton(text="😴 Улучшить сон")],
        [KeyboardButton(text="✨ Повысить самооценку")],
        [KeyboardButton(text="🎯 Найти ресурсы и мотивацию")],
        [KeyboardButton(text="✅ Готово")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

ONB_COVER_CAPTION = (
    "Привет! Я здесь, чтобы поддержать, выслушать и сохранить важное — не стесняйся.\n\n"
    "Перед началом подтвердим правила и включим персонализацию.\n"
    "Продолжая, ты принимаешь наши правила и политику:\n"
    "• https://tinyurl.com/5n98a7j8 • https://tinyurl.com/5n98a7j8\n\n"
    "Скорее нажимай — и я всё расскажу 👇"
)

ONB_SETTINGS_TEXT = (
    "Класс! Тогда пару быстрых настроек 🛠️\n\n"
    "Выбери, что сейчас важнее (можно несколько), а затем нажми «Готово»:"
)

ONB_WHATS_NEXT = (
    "Что дальше? Несколько вариантов:\n\n"
    "1) Хочешь просто поговорить — нажми «Поговорить». Без рамок и практик: поделись тем, что происходит, я поддержу и помогу разложить.\n"
    "2) Нужно быстро разобраться — открой «Разобраться». Там короткие упражнения на 5–10 минут: от дыхания и анти-катастрофизации до плана при панике и S-T-O-P.\n"
    "3) Хочешь разгрузить голову — в «Медитациях» будут короткие аудио для тревоги, сна и концентрации — добавим совсем скоро.\n\n"
    "Пиши, как тебе удобно. Я рядом ❤️"
)

# Фолбэк-меню на случай, если kb_main() не определён в проекте:
def _kb_main_fallback():
    try:
        return kb_main()
    except NameError:
        talk = globals().get("EMO_TALK","💬")+" Поговорить"
        work = globals().get("EMO_HERB","🌿")+" Разобраться"
        med  = globals().get("EMO_HEADPHONES","🎧")+" Медитации"
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=talk)],[KeyboardButton(text=work)],[KeyboardButton(text=med)]],
            resize_keyboard=True
        )
# ==============================================================================


@router.message(CommandStart())
async def cmd_start_onboarding(m: Message):
    try:
        await m.answer_photo(ONB_IMAGES["cover"], caption=ONB_COVER_CAPTION, reply_markup=kb_onb_consent())
    except Exception:
        await m.answer(ONB_COVER_CAPTION, reply_markup=kb_onb_consent())

@router.message(F.text == "👋 Привет, друг!")
async def on_onb_hello(m: Message):
    await m.answer(ONB_SETTINGS_TEXT, reply_markup=kb_onb_quick_setup())

@router.message(F.text == "✅ Готово")
async def on_onb_ready(m: Message):
    await m.answer(ONB_WHATS_NEXT, reply_markup=_kb_main_fallback())


# === Onboarding/V2 constants ===
EMO_WAVE = globals().get("EMO_WAVE", "👋")
EMO_TOOLS = globals().get("EMO_TOOLS", "🛠️")
POLICY_URL = "https://tinyurl.com/5n98a7j8"
RULES_URL = "https://tinyurl.com/5n98a7j8"


# === Onboarding/V2 helpers ===
# хранилка временных предпочтений онбординга и голоса
_ONB_PREFS: dict[int, set[str]] = {}
_VOICE_PREFS: dict[int, str] = {}

VOICE_CHOICES = {
    "default": "Универсальный 🌿",
    "friend":  "Друг/подруга 🤝",
    "pro":     "Психолог 🎓",
    "dark":    "Тёмная версия 🖤 (18+)",
}

def get_user_voice(uid: int) -> str:
    return _VOICE_PREFS.get(uid, "default")

def set_user_voice(uid: int, v: str) -> None:
    _VOICE_PREFS[uid] = v

def kb_onb_consent() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Политика", url=POLICY_URL)
    kb.button(text="Правила",  url=RULES_URL)
    kb.button(text="Привет, хорошо✅", callback_data="onb:hello")
    return kb.as_markup()

def kb_onb_prefs() -> ReplyKeyboardMarkup:
    rb = ReplyKeyboardBuilder()
    for txt in ["🧘 Снизить тревогу", "😴 Улучшить сон", "💫 Повысить самооценку", "🎯 Найти ресурсы и мотивацию"]:
        rb.button(text=txt)
    rb.button(text="✅ Готово")
    return rb.as_markup(resize_keyboard=True)

def kb_voice() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=VOICE_CHOICES["default"], callback_data="voice:set:default")
    kb.button(text=VOICE_CHOICES["friend"] , callback_data="voice:set:friend")
    kb.button(text=VOICE_CHOICES["pro"]    , callback_data="voice:set:pro")
    kb.button(text=VOICE_CHOICES["dark"]   , callback_data="voice:set:dark")
    return kb.as_markup()

def get_home_text() -> str:
    # если в исходнике уже есть такая функция — оставляем твою.
    return "Выбирай режим ниже или просто напиши, как ты сейчас. " + EMO_HERB



# === Onboarding V2 (экраны по ТЗ) ===

@router.message(CommandStart())
async def on_cmd_start_v2(m: Message):
    # Экран 1: картинка + согласие
    caption = (
        "Привет! Я здесь, чтобы поддержать, выслушать и сохранить важное — не стесняйся.\n\n"
        "Перед началом подтвердим правила и включим персонализацию.\n"
        "Продолжая, ты принимаешь наши правила и политику:\n"
        f"{POLICY_URL} • {RULES_URL}\n\n"
        "Скорее нажимай — и я всё расскажу 👇"
    )
    try:
        await m.answer_photo(ONB_IMAGES.get("cover"), caption=caption, reply_markup=kb_onb_consent())
    except Exception:
        await m.answer(caption, reply_markup=kb_onb_consent())

@router.callback_query(F.data == "onb:hello")
async def cb_onb_hello(cb: CallbackQuery):
    uid = cb.from_user.id
    _ONB_PREFS[uid] = set()
    # Экран 2: «Привет, друг!» + быстрые настройки
    try:
        await cb.message.answer(f"{EMO_WAVE} Привет, друг!")
    except Exception:
        pass
    text = (
        "Класс! Тогда пару быстрых настроек " + EMO_TOOLS + "\n\n"
        "Выбери, что сейчас важнее (можно несколько), а затем нажми «Готово»:"
    )
    await cb.message.answer(text, reply_markup=kb_onb_prefs())
    await cb.answer()

# собираем простые предпочтения
@router.message(F.text.in_({"🧘 Снизить тревогу","😴 Улучшить сон","💫 Повысить самооценку","🎯 Найти ресурсы и мотивацию"}))
async def on_onb_pick(m: Message):
    uid = m.from_user.id
    _ONB_PREFS.setdefault(uid, set()).add(m.text)

@router.message(F.text == "✅ Готово")
async def on_onb_done(m: Message):
    # Экран 3: финальный
    text = (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Хочешь просто поговорить — нажми «Поговорить». Без рамок и практик: поделись тем, что происходит, я поддержу и помогу разложить.\n"
        "2) Нужно оперативно поработать — зайди в «Разобраться». Там короткие упражнения на разные темы.\n"
        "3) Хочешь аудио-передышку — в «Медитациях» будут короткие аудио для тревоги, сна и концентрации — добавим совсем скоро.\n\n"
        "Пиши, как удобно — я рядом ❤️"
    )
    try:
        # если есть твой kb_main() — покажем его, иначе просто текст
        kb = globals().get("kb_main")
        if callable(kb):
            await m.answer(text, reply_markup=kb())
        else:
            await m.answer(text)
    except Exception:
        await m.answer(text)

# === /voice — выбор тона общения ===

@router.message(Command("voice"))
async def cmd_voice(m: Message):
    v = get_user_voice(m.from_user.id)
    await m.answer(
        "Выбери стиль общения для режима «Поговорить»:\n"
        f"Текущий: {VOICE_CHOICES.get(v, 'Универсальный 🌿')}",
        reply_markup=kb_voice()
    )

@router.callback_query(F.data.startswith("voice:set:"))
async def cb_voice_set(cb: CallbackQuery):
    uid = cb.from_user.id
    v = cb.data.split(":", 2)[-1]
    if v not in VOICE_CHOICES:
        await cb.answer("Неизвестный стиль", show_alert=True); return
    set_user_voice(uid, v)
    await cb.message.answer(f"Стиль обновлён: {VOICE_CHOICES[v]}")
    await cb.answer("Готово")


