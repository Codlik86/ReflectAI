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
from app.meditations import get_categories, get_items, get_item

# ===== Внутренние модули =====
from .exercises import TOPICS, EXERCISES  # ожидается структура как в твоём exercises.py
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

# === Приватность/память (работаем через memory.py) ===
try:
    from .memory import get_privacy as db_get_privacy, set_privacy as db_set_privacy, purge_user_data
except Exception:
    # безопасные заглушки
    def db_get_privacy(tg_id: str) -> str: return "ask"
    def db_set_privacy(tg_id: str, value: str) -> None: ...
    purge_user_data = None  # type: ignore

router = Router()

# ===== Онбординг: изображения и ссылки =====
POLICY_URL = os.getenv("POLICY_URL", "https://s.craft.me/APV7T8gRf3w2Ay")
TERMS_URL = os.getenv("TERMS_URL", "https://s.craft.me/APV7T8gRf3w2Ay")

DEFAULT_ONB_IMAGES = {
    "cover": os.getenv("ONB_IMG_COVER", "https://file.garden/aML3M6Sqrg21TaIT/kind-creature-min.jpg"),
    "talk": os.getenv("ONB_IMG_TALK", "https://file.garden/aML3M6Sqrg21TaIT/warm-conversation-min.jpg"),
    "work": os.getenv("ONB_IMG_WORK", "https://file.garden/aML3M6Sqrg21TaIT/_practices-min.jpg"),
    "meditations": os.getenv("ONB_IMG_MEDIT", "https://file.garden/aML3M6Sqrg21TaIT/meditation%20(1)-min.jpg"),
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
    Режимы из БД: ask | none | all.
    Трактуем:
      - all  -> хранение ВКЛ
      - none -> хранение ВЫКЛ
      - ask  -> считаем как ВКЛ до явного выключения
    """
    try:
        mode = (db_get_privacy(str(chat_id)) or "ask").lower()
    except Exception:
        mode = "ask"

    save_on = (mode != "none")
    toggle_text = "🔔 Вкл. хранение" if not save_on else "🔕 Выкл. хранение"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text,          callback_data="privacy:toggle")],
            [InlineKeyboardButton(text="🗑 Очистить историю", callback_data="privacy:clear")],
        ]
    )

def kb_privacy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить историю", callback_data="privacy:clear")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings")],
        ]
    )

EMO_DEFAULTS = {
    "sleep": "😴", "body": "💡", "procrastination": "🌿",
    "burnout": "☀️", "decisions": "🎯", "social_anxiety": "🫥",
    "reflection": "✨",
}

def topic_button_title(tid: str) -> str:
    t = TOPICS.get(tid, {})
    title = (t.get("title") or tid).strip()
    emoji = (t.get("emoji") or EMO_DEFAULTS.get(tid, "🌱")).strip()
    return f"{emoji} {title}"

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

    for eid, ex in EXERCISES.get(tid, {}).items():
        title = ex.get("title", eid)
        buttons.append([
            InlineKeyboardButton(
                text=title,
                callback_data=f"ex:{tid}:{eid}:start"
            )
        ])

    # Назад к списку тем
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="work:topics")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(F.data == "work:topics")
async def on_back_to_topics(cb: CallbackQuery):
    await _safe_edit(cb.message, "Выбирай тему:", reply_markup=kb_topics())
    await cb.answer()

def step_keyboard(tid: str, eid: str, idx: int, total: int) -> InlineKeyboardMarkup:
    prev_idx = max(0, idx - 1)
    next_idx = min(total - 1, idx + 1)
    buttons: List[List[InlineKeyboardButton]] = []
    nav: List[InlineKeyboardButton] = []

    # На первом шаге «Назад» ведёт в список упражнений темы,
    # чтобы не уходить в «минус-шаги» и не спамить дублями
    if idx == 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"exlist:{tid}"))
    else:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex:{tid}:{eid}:{prev_idx}"))

    if idx < total - 1:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"ex:{tid}:{eid}:{next_idx}"))
    else:
        nav.append(InlineKeyboardButton(text="✅ Завершить", callback_data=f"ex:{tid}:{eid}:finish"))

    buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def step_keyboard_intro(tid: str, eid: str, total: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для экрана интро:
    - «Назад» → список упражнений выбранной темы
    - «Далее» → на ПЕРВЫЙ шаг (индекс 0)
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"exlist:{tid}"),
            InlineKeyboardButton(text="➡️ Далее", callback_data=f"ex:{tid}:{eid}:0"),
        ]]
    )

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
    # текст-только, без фото
    await cb.message.answer(ONB_2_TEXT, reply_markup=kb_onb_step2())
    await cb.answer()

@router.callback_query(F.data == "onb:agree")
async def on_onb_agree(cb: CallbackQuery):
    # логируем согласие → bot_events
    try:
        from sqlalchemy import text
        from app.db import db_session

        with db_session() as s:
            # получаем users.id по tg_id (у тебя tg_id — строка)
            uid = s.execute(
                text("SELECT id FROM users WHERE tg_id = :tg"),
                {"tg": str(cb.from_user.id)}
            ).scalar()

            # на всякий случай — если записи о пользователе ещё нет, создадим
            if uid is None:
                s.execute(
                    text("INSERT INTO users (tg_id, privacy_level) VALUES (:tg, 'insights')"),
                    {"tg": str(cb.from_user.id)}
                )
                uid = s.execute(
                    text("SELECT id FROM users WHERE tg_id = :tg"),
                    {"tg": str(cb.from_user.id)}
                ).scalar()

            # сам лог согласия
            s.execute(
                text("""
                    INSERT INTO bot_events (user_id, event_type, payload, created_at)
                    VALUES (:uid, :event, :payload, CURRENT_TIMESTAMP)
                """),
                {"uid": uid, "event": "policy_accept", "payload": '{"via":"onboarding_step2"}'}
            )
            s.commit()
    except Exception:
        # не мешаем онбордингу, если что-то с БД
        pass

    # твой текущий UX остаётся: показываем правое меню и шаг «Что дальше?»
    try:
        await cb.answer("Спасибо! Принял ✅", show_alert=False)
    except Exception:
        pass

    kb = None
    try:
        kb = kb_main_menu()   # как у тебя в текущем коде
    except Exception:
        kb = None

    await cb.message.answer(WHAT_NEXT_TEXT, reply_markup=kb)

# ===== Кнопки меню (reply-клавиатура) =====
@router.message(F.text.in_(["🌿 Разобраться", "/work"]))
async def on_work_menu(m: Message):
    CHAT_MODE[m.chat.id] = "work"

    img = get_onb_image("work")  # можно сделать отдельную картинку, если захочешь: "work_topics"
    if img:
        try:
            await m.answer_photo(img, caption="Выбирай тему:", reply_markup=kb_topics())
            return
        except Exception:
            pass

    await m.answer("Выбирай тему:", reply_markup=kb_topics())

@router.callback_query(F.data.startswith("exlist:"))
async def on_exlist(cb: CallbackQuery):
    tid = cb.data.split(":", 1)[1]
    await _safe_edit(cb.message, topic_button_title(tid), reply_markup=kb_exercises(tid))
    await cb.answer()

@router.message(F.text.in_(["💬 Поговорить", "/talk"]))
async def on_talk(m: Message):
    CHAT_MODE[m.chat.id] = "talk"
    await m.answer("Я рядом и слушаю. О чём хочется поговорить?", reply_markup=kb_main_menu())

# === ReflectAI: Медитации (категории → список треков → play) ===

def _as_track(item: object) -> dict:
    # dict-форма
    if isinstance(item, dict):
        return {
            "id": item.get("id") or item.get("key") or item.get("uid") or "",
            "title": item.get("title", "Медитация"),
            "duration": item.get("duration", ""),
            "url": item.get("url"),
        }
    # tuple/list-формы
    if isinstance(item, (tuple, list)):
        # (id, {title, duration, url})
        if len(item) == 2 and isinstance(item[1], dict):
            meta = item[1]
            return {
                "id": meta.get("id") or item[0],
                "title": meta.get("title", "Медитация"),
                "duration": meta.get("duration", ""),
                "url": meta.get("url"),
            }
        # (id, title, url[, duration])
        if len(item) >= 3:
            return {
                "id": item[0],
                "title": item[1] or "Медитация",
                "url": item[2],
                "duration": item[3] if len(item) > 3 else "",
            }
        # fallback
        return {"id": str(item[0]), "title": str(item[-1]), "duration": "", "url": None}
    # совсем непонятный случай
    return {"id": "", "title": str(item), "duration": "", "url": None}

# Клавиатура: список категорий (без кнопки "назад")
def kb_meditations_categories() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cid, label in get_categories():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"med:cat:{cid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# Клавиатура: список аудио внутри категории (устойчиво к форматам)
def kb_meditations_list(cid: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for raw in get_items(cid):
        tr = _as_track(raw)
        label = f"{tr['title']} · {tr.get('duration','')}".strip(" ·")
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"med:play:{cid}:{tr['id']}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Категории", callback_data="med:cats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

MEDITATIONS_TEXT = (
    "🎧 Медитации.\n"
    "Выбери тему — пришлю короткую практику.\n"
    "Начинай с того, что откликается."
)

# /meditations (и опечатки) — вход в раздел
@router.message(Command(commands=["meditations", "meditions", "meditation"]))
async def cmd_meditations(m: Message):
    img = get_onb_image("meditations")  # если есть обложка, отправим как фото
    if img:
        try:
            await m.answer_photo(img, caption=MEDITATIONS_TEXT,
                                 reply_markup=kb_meditations_categories())
            return
        except Exception:
            pass
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

# Кнопка правого меню «🎧 Медитации»
@router.message(F.text == "🎧 Медитации")
async def on_meditations_btn(m: Message):
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

# Коллбэки навигации
@router.callback_query(F.data == "med:cats")
async def on_med_cats(cb: CallbackQuery):
    await _safe_edit(cb.message, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())
    await cb.answer()

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
        await cb.answer("Не нашёл аудио", show_alert=True)
        return

    caption = f"🎧 {tr.get('title','Медитация')} · {tr.get('duration','')}".strip(" ·")
    url = tr.get("url")

    if url:
        try:
            # прямой .mp3/.m4a — Telegram сам проиграет
            await cb.message.answer_audio(url, caption=caption)
        except Exception:
            # если это не прямое аудио (YouTube/страница) — даём ссылку
            await cb.message.answer(f"{caption}\n{url}")
    else:
        await cb.message.answer(caption)

    await cb.answer("Запускай, я рядом 💛")

@router.message(F.text.in_(["⚙️ Настройки", "/settings", "/setting"]))
async def on_settings(m: Message):
    await m.answer("Настройки:", reply_markup=kb_settings())

@router.callback_query(F.data == "menu:main")
async def on_menu_main(cb: CallbackQuery):
    await cb.message.answer("Меню:", reply_markup=kb_main_menu())
    await cb.answer()

@router.callback_query(F.data == "menu:settings")
async def on_menu_settings(cb: CallbackQuery):
    await _safe_edit(cb.message, "Настройки:", reply_markup=kb_settings())
    await cb.answer()

@router.callback_query(F.data == "privacy:toggle")
async def on_privacy_toggle(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    mode = (db_get_privacy(str(chat_id)) or "ask").lower()
    # flip: all/ask -> none, none -> all
    new_mode = "none" if mode != "none" else "all"
    db_set_privacy(str(chat_id), new_mode)

    state_txt = "выключено" if new_mode == "none" else "включено"
    await _safe_edit(
        cb.message,
        f"Хранение истории сейчас: <b>{state_txt}</b>.",
        reply_markup=kb_privacy_for(chat_id),
    )
    await cb.answer("Настройка применена")

# === Settings menu actions ===
@router.callback_query(F.data == "settings:tone")
async def on_settings_tone(cb: CallbackQuery):
    await _safe_edit(
        cb.message,
        "Выбери тон общения:",
        reply_markup=kb_tone_picker(),
    )
    await cb.answer()

@router.callback_query(F.data == "settings:privacy")
async def on_settings_privacy(cb: CallbackQuery):
    await _safe_edit(
        cb.message,
        "Приватность:",
        reply_markup=kb_privacy_for(cb.message.chat.id),
    )
    await cb.answer()

# ===== Тон общения (/tone) =====
@router.message(Command("tone"))
async def on_tone_cmd(m: Message):
    await m.answer("Выбери тон общения:", reply_markup=kb_tone_picker())

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
        # делаем разбор безопасным: если нет 4-й части — считаем, что "start"
        parts = cb.data.split(":", 3)
        _, tid, eid = parts[0], parts[1], parts[2]
        action = parts[3] if len(parts) > 3 else "start"
    except Exception:
        await cb.answer()
        return

    # спец-режим: свободный чат "Рефлексия"
    if eid == "reflection":
        await cb.answer()
        await _safe_edit(
            cb.message,
            "Я рядом и слушаю. О чём хочется поговорить?",
            reply_markup=None,
        )
        return

    # !!! главная правка: никаких .get("items") — структура плоская
    ex = (EXERCISES.get(tid) or {}).get(eid)
    if not ex:
        await cb.answer("Упражнение не найдено", show_alert=True)
        return

    steps = ex.get("steps") or []
    intro = ex.get("intro") or ""
    total = max(1, len(steps))

    if action == "finish":
        # по завершению возвращаем список упражнений выбранной темы
        try:
            kb = kb_exercises(tid)
        except NameError:
            # если kb_exercises нет — уходим к списку тем
            kb = kb_topics()
        await _safe_edit(cb.message, "Готово. Вернёмся к теме?", reply_markup=kb)
        await cb.answer()
        return

    if action == "start":
        text = intro or (steps[0] if steps else "Шагов нет.")
        await _safe_edit(cb.message, text, reply_markup=step_keyboard_intro(tid, eid, total))
        await cb.answer()
        return

    # action — это индекс шага
    try:
        idx = int(action)
    except Exception:
        idx = 0
    idx = max(0, min(idx, total - 1))

    text = steps[idx] if steps else "Шагов нет."
    await _safe_edit(cb.message, text, reply_markup=step_keyboard(tid, eid, idx, total))
    await cb.answer()
    
# ===== Рефлексия — свободный чат =====
@router.callback_query(F.data == "reflect:start")
async def on_reflect_start(cb: CallbackQuery):
    CHAT_MODE[cb.message.chat.id] = "reflection"
    await _safe_edit(cb.message, "Давай немного притормозим и прислушаемся к себе. "
                                  "Можешь начать с того, что больше всего откликается сейчас.")
    await cb.answer()

@router.callback_query(F.data.startswith("t:"))
async def on_topic_click(cb: CallbackQuery):
    """
    Пользователь нажал на тему в «Разобраться».
    Делаем редактирование текущего сообщения:
    - в тексте — заголовок темы (с эмодзи),
    - в клавиатуре — список упражнений темы.
    """
    tid = cb.data.split(":", 1)[1]
    await _safe_edit(cb.message, topic_button_title(tid), reply_markup=kb_exercises(tid))
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
        return "Стиль: бережный, психологичный, задавай мягкие проясняющие вопросы, добавляй научные факты и избегай диагнозов."
    if style_key == "18plus":
        return "Стиль: допускается разговорный, допускается не тяжелые ругательства, любые темы для обсуждения, смелее формулировки, без грубости, но иногда можно добавить немного ироничной токсичности."
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

@router.message(Command("work"))
async def cmd_work(m: Message):
    await _safe_edit(m, "Выбирай тему:", reply_markup=kb_topics())

@router.callback_query(F.data == "privacy:clear")
async def on_privacy_clear(cb: CallbackQuery):
    # реальная очистка
    from app.memory import purge_user_history
    try:
        count = purge_user_history(cb.from_user.id)
    except Exception:
        await cb.answer("Не получилось очистить историю", show_alert=True)
        return

    # всплывашка + возврат в подходящее меню
    await cb.answer("История удалена ✅", show_alert=True)

    # подбираем подходящую клавиатуру: сначала приватность, если есть; иначе общие настройки
    kb = None
    try:
        kb = kb_privacy_for(cb.message.chat.id)  # если у тебя есть эта функция
    except Exception:
        try:
            kb = kb_settings()  # fallback: корневые настройки
        except Exception:
            kb = None

    text = f"Готово. Что дальше?\n\nУдалено записей: {count}."
    try:
        # если у тебя есть вспомогалка _safe_edit — используем её
        await _safe_edit(cb.message, text, reply_markup=kb)  # type: ignore[name-defined]
    except Exception:
        # иначе просто новое сообщение
        await cb.message.answer(text, reply_markup=kb)

# === ReflectAI: Медитации UI ===
# Клавиатура: список категорий
def kb_meditations_categories() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cid, label in get_categories():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"med:cat:{cid}")])
    # назад к главному меню (правое reply-меню остаётся, но даём явную кнопку)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings")])  # можно поменять на другой экран, если хочешь
    return InlineKeyboardMarkup(inline_keyboard=rows)

# Клавиатура: список аудио в категории
def kb_meditations_list(cid: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in get_items(cid):
        rows.append([InlineKeyboardButton(
            text=f"{item['title']} · {item.get('duration','')}".strip(),
            callback_data=f"med:play:{cid}:{item['id']}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ К категориям", callback_data="med:cats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

MEDITATIONS_TEXT = (
    "🎧 Медитации.\n"
    "Выбери тему — пришлю короткую практику.\n"
    "Начинай с того, что откликается."
)

# /meditations и кнопка «🎧 Медитации»
@router.message(Command("meditations"))
async def cmd_meditations(m: Message):
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

from aiogram import F
@router.message(F.text == "🎧 Медитации")
async def on_meditations_btn(m: Message):
    await _safe_edit(m, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())

# Коллбэки
@router.callback_query(F.data == "med:cats")
async def on_med_cats(cb: CallbackQuery):
    await _safe_edit(cb.message, MEDITATIONS_TEXT, reply_markup=kb_meditations_categories())
    await cb.answer()

@router.callback_query(F.data.startswith("med:cat:"))
async def on_med_cat(cb: CallbackQuery):
    cid = cb.data.split(":", 2)[2]
    # заголовок категории для текста
    cats = dict(get_categories())
    title = cats.get(cid, "Медитации")
    await _safe_edit(cb.message, f"🎧 {title}", reply_markup=kb_meditations_list(cid))
    await cb.answer()

@router.callback_query(F.data.startswith("med:play:"))
async def on_med_play(cb: CallbackQuery):
    _, _, cid, mid = cb.data.split(":", 3)
    item = get_item(cid, mid)
    if not item:
        await cb.answer("Не нашёл аудио", show_alert=True)
        return
    url = item.get("url")
    caption = f"🎧 {item.get('title','Медитация')} · {item.get('duration','')}".strip()
    try:
        # если это прямой URL на аудио — Telegram сам его подтянет
        await cb.message.answer_audio(url, caption=caption)
    except Exception:
        # fallback: просто ссылка (если это, например, YouTube)
        await cb.message.answer(f"{caption}\n{url}")
    # остаёмся на списке текущей категории
    await cb.answer("Запускай, я рядом 💛")