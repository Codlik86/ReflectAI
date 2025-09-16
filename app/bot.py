# -*- coding: utf-8 -*-
from __future__ import annotations

# --- Per-topic emojis for /work ---
DEFAULT_TOPIC_ICON = "🌿"  # общий эмодзи по умолчанию
TOPIC_ICONS = {
    "reflection": "🪞",            # Рефлексия
    "anxiety": "🌬️",               # Тревога
    "anger": "🔥",                  # Злость
    "pain_melancholy": "🌧️",       # Боль и тоска
    "sleep": "🌙",                  # Сон
    "breath_body": "🧘",            # Дыхание и тело
    "procrastination": "⏳",        # Прокрастинация
    "burnout": "🪫",                # Выгорание
    "decisions": "🧭",              # Решения и неопределённость
    "social_anxiety": "🗣️",        # Социальная тревога
}
def topic_icon(tid: str, t: dict) -> str:
    return TOPIC_ICONS.get(tid, t.get("icon", DEFAULT_TOPIC_ICON))


# ==== Импорты ===============================================================
from textwrap import dedent
from collections import defaultdict, deque
from typing import Dict, Deque, List, Optional

import asyncio

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    BotCommand,
)
from aiogram.exceptions import TelegramBadRequest

# В aiogram v3 билдер можно не использовать для простых клавиатур
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import text as sql_text

# ==== Внутренние импорты проекта ============================================
# Важно: оставляю импорт как был у тебя в проекте
from app.llm_adapter import LLMAdapter
from app.prompts import SYSTEM_PROMPT

# Доп. тон для режима рефлексии (мягче, меньше буквального зеркаления)
REFLECTIVE_SUFFIX = (
    "\n\n[режим рефлексии]\n"
    "— отвечай тёпло и бережно;\n"
    "— не повторяй дословно каждое сообщение пользователя, только уместное перефразирование;\n"
    "— добавляй чуть инициативы и контекста, без моралей;\n"
    "— допускаются небольшие «человеческие» реакции: да, понимаю… иногда троеточия, эмодзи в меру.\n"
)

from app.safety import is_crisis, CRISIS_REPLY
from app.exercises import TOPICS
from app.db import db_session, User, Insight  # Insight может использоваться в будущем
from app.tools import (
    REFRAMING_STEPS,
    stop_user_task,
    debounce_ok,
)
try:
    # RAG может быть необязательным — оборачиваю в try
    from app.rag_qdrant import search as rag_search
except Exception:
    rag_search = None  # type: ignore

# ==== Константы/эмодзи ======================================================
EMO_TALK = "\U0001F4AC"        # 💬
EMO_PUZZLE = "\U0001F9E9"      # 🧩
EMO_HEADPHONES = "\U0001F3A7"  # 🎧
EMO_GEAR = "\u2699\ufe0f"      # ⚙️

# ==== Router ================================================================
router = Router()

# ==== Хелперы UI ============================================================
async def safe_edit(message: Message, *, text: Optional[str] = None, reply_markup=None) -> None:
    """
    Редактирует текст/markup и молча игнорит 'message is not modified'.
    """
    try:
        if text is not None and reply_markup is not None:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        if text is not None:
            await message.edit_text(text)
        if reply_markup is not None:
            await message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise

async def silent_ack(cb: CallbackQuery) -> None:
    """Быстрый ack для снятия спиннера и во избежание 'query is too old'."""
    try:
        await cb.answer()
    except Exception:
        pass

# ==== Главный текст после онбординга =======================================
def get_home_text() -> str:
    return (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Хочешь просто поговорить — нажми «Поговорить». Без рамок и практик: поделись тем, что происходит, я поддержу и помогу разложить.\n"
        "2) Нужно быстро разобраться — открой «Разобраться». Там короткие упражнения на 5–10 минут: от дыхания и анти-катастрофизации до плана при панике и S-T-O-P.\n"
        "3) Хочешь разгрузить голову — в «Медитациях» будут короткие аудио для тревоги, сна и концентрации — добавим совсем скоро.\n\n"
        "Пиши, как тебе удобно. Я рядом ❤️"
    )

# ==== Клавиатуры ============================================================
def kb_main() -> ReplyKeyboardMarkup:
    """
    Нижняя (persistent) клавиатура.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{EMO_TALK} Поговорить")],
            [KeyboardButton(text=f"{EMO_PUZZLE} Разобраться"), KeyboardButton(text=f"{EMO_HEADPHONES} Медитации")],
            [KeyboardButton(text=f"{EMO_GEAR} Настройки")],
        ],
        resize_keyboard=True, one_time_keyboard=False, selective=False
    )

# --- Список тем (уникальные по title) ---
def kb_topics() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    seen = set()
    for key, t in TOPICS.items():
        title = t.get("title", "Тема")
        if not title or title in seen:
            continue
        seen.add(title)
        b.button(text=f"🌿 {title}", callback_data=f"work:topic:{key}")
    b.adjust(1)
    return b.as_markup()

def kb_exercises(topic_id: str) -> InlineKeyboardMarkup:
    """
    Список упражнений по теме.
    """
    t = TOPICS.get(topic_id, {})
    rows = []
    for ex in t.get("exercises", []):
        title = ex.get("title", ex.get("id", "Упражнение"))
        rows.append([InlineKeyboardButton(text=title, callback_data=f"work:ex:{topic_id}:{ex.get('id')}")])
    # Нижние кнопки «назад»
    rows.append([
        InlineKeyboardButton(text="◀️ К темам", callback_data="work:back_topics")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_markup_for_topic(topic_id: str) -> InlineKeyboardMarkup:
    """
    Возврат к списку упражнений в теме.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к упражнениям", callback_data=f"work:topic:{topic_id}")],
        [InlineKeyboardButton(text="🌿 Другие темы", callback_data="work:back_topics")],
    ])

def kb_stepper2(topic_id: str, ex_id: str, cur: int, total: int) -> InlineKeyboardMarkup:
    """
    Шаги упражнения (вперёд / стоп / назад в тему).
    """
    is_last = (cur >= total - 1)
    next_text = "✔️ Завершить" if is_last else "▶️ Далее"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=next_text, callback_data=f"work:step:{topic_id}:{ex_id}")],
        [
            InlineKeyboardButton(text="◀️ К упражнениям", callback_data=f"work:topic:{topic_id}"),
            InlineKeyboardButton(text="⏹ Стоп", callback_data="work:stop"),
        ],
    ])

# ==== Вспомогательные рендер-функции =======================================
def render_step_text(topic_title: str, ex_title: str, step_text: str) -> str:
    header = "🌿 " + topic_title + " → " + ex_title
    return header + "\n\n" + str(step_text)

def render_text_exercise(topic_title: str, ex_title: str, text: str) -> str:
    header = "🌿 " + topic_title + " → " + ex_title
    return header + "\n\n" + str(text)

# ==== Эфемерное состояние упражнения (на пользователя) =====================
_WS: Dict[str, Dict] = {}

def _ws_get(uid: str) -> Dict:
    return _WS.get(uid, {})

def _ws_set(uid: str, **fields) -> None:
    prev = _WS.get(uid, {})
    prev.update(fields)
    _WS[uid] = prev

def _ws_reset(uid: str) -> None:
    _WS.pop(uid, None)

# ==== Короткая память диалога (RAM) ========================================
DIALOG_HISTORY: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=8))
# Режим чата (обычный / reflection)
CHAT_MODE: Dict[int, str] = {}

def _push(chat_id: int, role: str, content: str) -> None:
    if content:
        DIALOG_HISTORY[chat_id].append({"role": role, "content": content})

# ==== «Суперпамять» (SQLite) ===============================================
def _ensure_tables():
    with db_session() as s:
        s.execute(sql_text("""
        CREATE TABLE IF NOT EXISTS user_prefs (
          tg_id TEXT PRIMARY KEY,
          consent_save_all INTEGER DEFAULT 0,
          goals TEXT DEFAULT '',
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """))
        s.execute(sql_text("""
        CREATE TABLE IF NOT EXISTS dialog_turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tg_id TEXT NOT NULL,
          role TEXT NOT NULL,
          text TEXT NOT NULL,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """))
        s.commit()

def _can_save_full(tg_id: str) -> bool:
    _ensure_tables()
    with db_session() as s:
        row = s.execute(
            sql_text("SELECT consent_save_all FROM user_prefs WHERE tg_id=:tg"),
            {"tg": tg_id}
        ).fetchone()
        return bool(row and row[0])

def _save_turn(tg_id: str, role: str, text: str) -> None:
    if not _can_save_full(tg_id):
        return
    _ensure_tables()
    with db_session() as s:
        s.execute(
            sql_text("INSERT INTO dialog_turns (tg_id, role, text) VALUES (:tg, :r, :t)"),
            {"tg": tg_id, "r": role, "t": (text or "")[:4000]},
        )
        s.commit()

def _load_recent_turns(tg_id: str, days: int = 7, limit: int = 24) -> List[Dict[str, str]]:
    _ensure_tables()
    with db_session() as s:
        q = f"""
          SELECT role, text FROM dialog_turns
          WHERE tg_id = :tg AND created_at >= datetime('now','-{int(days)} days')
          ORDER BY id DESC LIMIT {int(limit)}
        """
        rows = s.execute(sql_text(q), {"tg": tg_id}).fetchall() or []
    return [{"role": r, "content": t} for (r, t) in reversed(rows)]

def _set_consent(tg_id: str, yes: bool) -> None:
    _ensure_tables()
    with db_session() as s:
        s.execute(sql_text("""
            INSERT INTO user_prefs (tg_id, consent_save_all)
            VALUES (:tg, :c)
            ON CONFLICT(tg_id) DO UPDATE SET consent_save_all=:c, updated_at=CURRENT_TIMESTAMP
        """), {"tg": tg_id, "c": 1 if yes else 0})
        s.commit()

def _append_goal(tg_id: str, goal_code: str) -> None:
    _ensure_tables()
    with db_session() as s:
        row = s.execute(sql_text("SELECT goals FROM user_prefs WHERE tg_id=:tg"), {"tg": tg_id}).fetchone()
        goals = set(((row[0] or "") if row else "").split(","))
        if goal_code not in goals:
            goals.add(goal_code)
        s.execute(sql_text("""
            INSERT INTO user_prefs (tg_id, goals)
            VALUES (:tg, :g)
            ON CONFLICT(tg_id) DO UPDATE SET goals=:g, updated_at=CURRENT_TIMESTAMP
        """), {"tg": tg_id, "g": ",".join([g for g in goals if g])})
        s.commit()

# ==== Онбординг =============================================================
ONB_IMAGES = {
    "cover": "https://file.garden/aML3M6Sqrg21TaIT/kind-creature%20(3).png",
}

def onb_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👋 Привет, друг!", callback_data="onb_hi")]
    ])

def onb_goals_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧘 Снизить тревогу", callback_data="goal:anxiety")],
        [InlineKeyboardButton(text="😴 Улучшить сон", callback_data="goal:sleep")],
        [InlineKeyboardButton(text="🌟 Повысить самооценку", callback_data="goal:self")],
        [InlineKeyboardButton(text="🎯 Найти ресурсы и мотивацию", callback_data="goal:motivation")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="goal_done")],
    ])

@router.message(F.text == f"{EMO_TALK} Поговорить")
async def on_btn_talk(m: Message):
    await m.answer("Я рядом. Расскажи, что на душе — начнём с этого.", reply_markup=None)

@router.message(F.text == f"{EMO_PUZZLE} Разобраться")
async def on_btn_work(m: Message):
    await m.answer("Выбери тему, с которой хочешь поработать:", reply_markup=kb_topics())

@router.message(F.text == f"{EMO_HEADPHONES} Медитации")
async def on_btn_meditations(m: Message):
    await m.answer("Раздел с аудио-медитациями скоро добавим. А пока можно выбрать упражнения в «Разобраться».", reply_markup=None)

@router.message(F.text == f"{EMO_GEAR} Настройки")
async def on_btn_settings(m: Message):
    await m.answer("Настройки: позже здесь появятся выбор тона, методы и приватность. Пока — заглушка.", reply_markup=None)

# ==== Slash-команды (левое меню) ===========================================
async def set_bot_commands(bot) -> None:
    try:
        await bot.set_my_commands([
            BotCommand(command="talk", description="Поговорить"),
            BotCommand(command="work", description="Разобраться (упражнения)"),
            BotCommand(command="meditations", description="Медитации (аудио, скоро)"),
            BotCommand(command="settings", description="Настройки"),
            BotCommand(command="about", description="О проекте"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="pay", description="Поддержать проект"),
            BotCommand(command="policy", description="Приватность"),
        ])
    except Exception:
        pass

@router.message(F.text.regexp(r'^/(talk|settings|meditations|about|help|pay|policy|work)(?:@\w+)?(?:\s|$)'))
async def _route_slash_commands(m: Message):
    cmd = (m.text or "").split()[0].split("@")[0].lower()
    if cmd == "/talk":
        return await on_btn_talk(m)
    if cmd == "/work":
        return await on_btn_work(m)
    if cmd == "/meditations":
        return await on_btn_meditations(m)
    if cmd == "/settings":
        return await on_btn_settings(m)
    if cmd == "/about":
        return await m.answer("Pomni — тёплый AI-друг/дневник: слушает, помогает осмыслить переживания, предлагает упражнения и микрошаги.")
    if cmd == "/help":
        return await m.answer("Помощь: просто напиши, что происходит. Я поддержу, подскажу упражнения, сохраню важные мысли.")
    if cmd == "/pay":
        return await m.answer("Поддержка проекта: скоро добавим способы. Спасибо, что хочешь помочь! ❤️")
    if cmd == "/policy":
        return await m.answer("Политика и правила: https://tinyurl.com/5n98a7j8 • https://tinyurl.com/5n98a7j8")
    # fallback
    await m.answer("Команда принята.")

@router.message(F.text.regexp(r'^/start(?:@\w+)?(?:\s|$)'))
async def start(m: Message):
    await set_bot_commands(m.bot)

    # регистрация пользователя
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if not u:
            s.add(User(tg_id=str(m.from_user.id), privacy_level="insights"))
            s.commit()

    # очищаем RAM-диалог
    DIALOG_HISTORY.pop(m.chat.id, None)

    caption = (
        "Привет! Я здесь, чтобы поддержать, выслушать и сохранить важное — не стесняйся.\n\n"
        "Перед началом подтвердим правила и включим персонализацию.\n"
        "Продолжая, ты принимаешь наши правила и политику:\n"
        "https://tinyurl.com/5n98a7j8 • https://tinyurl.com/5n98a7j8\n\n"
        "Скорее нажимай — и я всё расскажу 👇"
    )
    try:
        await m.answer_photo(ONB_IMAGES["cover"], caption=caption, reply_markup=onb_start_kb())
    except Exception:
        await m.answer(caption, reply_markup=onb_start_kb())

@router.callback_query(F.data == "onb_hi")
async def onb_hi(cb: CallbackQuery):
    await silent_ack(cb)
    _set_consent(str(cb.from_user.id), True)
    txt = (
        "Класс! Тогда пару быстрых настроек 🛠️\n\n"
        "Выбери, что сейчас важнее (можно несколько), а затем нажми «Готово»:"
    )
    await cb.message.answer(txt, reply_markup=onb_goals_kb())

@router.callback_query(F.data.startswith("goal:"))
async def onb_goal_pick(cb: CallbackQuery):
    await silent_ack(cb)
    code = cb.data.split(":", 1)[1]
    _append_goal(str(cb.from_user.id), code)
    names = {
        "anxiety": "Снизить тревогу",
        "sleep": "Улучшить сон",
        "self": "Повысить самооценку",
        "motivation": "Найти ресурсы и мотивацию",
    }
    try:
        await cb.answer(f"Добавил: {names.get(code, code)}", show_alert=False)
    except Exception:
        pass

@router.callback_query(F.data.in_(("goal_done", "onboard:done", "onb:done", "start:done")))
async def cb_done_gate(cb: CallbackQuery):
    # ранний ack
    await silent_ack(cb)
    # показываем текст и нижнюю клавиатуру
    try:
        await cb.message.answer(get_home_text(), reply_markup=kb_main())
    except Exception:
        await cb.message.answer(get_home_text())

# ==== Работа с темами/упражнениями =========================================
@router.message(F.text == f"{EMO_PUZZLE} Разобраться")
async def _open_work_from_keyboard(m: Message):
    await on_btn_work(m)

@router.callback_query(F.data == "work:back_topics")
async def cb_back_topics(cb: CallbackQuery):
    await silent_ack(cb)
    await safe_edit(cb.message, text="Выбери тему, с которой хочешь поработать:", reply_markup=kb_topics())

@router.callback_query(F.data.startswith("work:topic:"))
async def cb_pick_topic(cb: CallbackQuery):
    # важно ответить быстро, чтобы не получить "query is too old"
    try:
        await cb.answer()
    except Exception:
        pass

    topic_id = cb.data.split(":")[2]
    t = TOPICS.get(topic_id, {})
    title = t.get("title", "Тема")
    intro = t.get("intro")

    # Темы-«рефлексия»: без списка упражнений — сразу тёплое интро и свободный чат
    if t.get("type") == "chat":
        intro_long = t.get("intro_long") or intro or (
            "Давай немного поразмышляем об этом. Напиши пару строк — что волнует, что хочется понять… Я рядом."
        )
        text = f"Тема: {title}\n\n{intro_long}"
        await safe_edit(cb.message, text=text, reply_markup=None)
        return

    # Обычная тема: показываем интро и список упражнений
    if intro:
        text = f"Тема: {title}\n\n{intro}"
    else:
        text = f"Ок, остаёмся в теме «{title}». Выбери упражнение ниже."
    await safe_edit(cb.message, text=text, reply_markup=kb_exercises(topic_id))
@router.callback_query(F.data.startswith("work:ex:"))
async def cb_pick_exercise(cb: CallbackQuery):
    # быстро отвечаем на callback
    try:
        await cb.answer()
    except Exception:
        pass

    parts = cb.data.split(":")
    topic_id, ex_id = parts[2], parts[3]
    t = TOPICS.get(topic_id, {})
    ex = next((e for e in t.get("exercises", []) if e.get("id") == ex_id), None)
    if ex is None:
        await cb.message.answer("Не нашёл упражнение")
        return

    topic_title = t.get("title", "Тема")
    ex_title = ex.get("title", "Упражнение")

    # Упражнение-«рефлексия»: без степпера — сразу тёплое интро, дальше пользователь пишет и общается свободно
    if ex.get("type") == "chat":
        intro_long = ex.get("intro_long") or ex.get("intro") or (
            "Предлагаю спокойно поразмышлять. Напиши, что чувствуешь и что сейчас важно… Я здесь и поддержу."
        )
        text = f"🌿 {topic_title} → {ex_title}\n\n{intro_long}"
        await safe_edit(cb.message, text=text, reply_markup=None)
        return

    # Текстовое упражнение без шагов
    text_only = ex.get("text") or ex.get("body") or ex.get("content")
    if text_only and not ex.get("steps"):
        text = render_text_exercise(topic_title, ex_title, str(text_only))
        await safe_edit(cb.message, text=text, reply_markup=back_markup_for_topic(topic_id))
        return

    # Обычный степпер
    steps = ex.get("steps", [])
    intro = ex.get("intro")
    steps_all = ([intro] + steps) if intro else steps
    if not steps_all:
        await cb.message.answer("Пустое упражнение")
        return

    uid = str(cb.from_user.id)
    _ws_set(uid, topic=topic_id, ex=ex_id, step=0)
    text = render_step_text(topic_title, ex_title, steps_all[0])
    await safe_edit(cb.message, text=text, reply_markup=kb_stepper2(topic_id, ex_id, 0, len(steps_all)))
@router.callback_query(F.data.startswith("work:step:"))
async def cb_step_next(cb: CallbackQuery):
    await silent_ack(cb)
    parts = cb.data.split(":")
    # формат: work:step:{topic}:{ex}
    if len(parts) < 4:
        return
    topic_id, ex_id = parts[2], parts[3]

    uid = str(cb.from_user.id)
    st = _ws_get(uid)
    if not st or st.get("ex") != ex_id or st.get("topic") != topic_id:
        # если потеряли состояние — начнём сначала
        try:
            await cb.answer("Сценарий сброшен, открою упражнение заново…")
        except Exception:
            pass
        return await cb_pick_exercise(cb)

    t = TOPICS.get(topic_id, {})
    ex_list = t.get("exercises", [])
    ex = next((e for e in ex_list if e.get("id") == ex_id), None)
    if not ex:
        return

    steps: List[str] = list(ex.get("steps", []))
    intro = ex.get("intro")
    steps_all = ([str(intro)] + [str(s) for s in steps]) if intro else [str(s) for s in steps]

    cur = int(st.get("step", 0)) + 1
    if cur >= len(steps_all):
        # завершение
        _ws_reset(uid)
        done_text = "✅ Готово. Хочешь выбрать другое упражнение или тему?"
        await safe_edit(cb.message, text=done_text, reply_markup=kb_exercises(topic_id))
        return

    _ws_set(uid, step=cur)
    topic_title = t.get("title", "Тема")
    ex_title = (ex.get("title") or "Упражнение")
    text = render_step_text(topic_title, ex_title, steps_all[cur])
    await safe_edit(cb.message, text=text, reply_markup=kb_stepper2(topic_id, ex_id, cur, len(steps_all)))

@router.callback_query(F.data == "work:stop")
async def cb_stop(cb: CallbackQuery):
    await silent_ack(cb)
    try:
        _ws_reset(str(cb.from_user.id))
    except Exception:
        pass
    await safe_edit(
        cb.message,
        text="Остановил упражнение. Можем просто поговорить или выбрать другую тему.",
        reply_markup=kb_topics(),
    )

# ==== Инструменты (рефлексия/микрошаг) =====================================
_reframe_state: Dict[str, Dict] = {}  # user_id -> {"step_idx": int, "answers": dict}

def tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💡 Рефлексия", callback_data="tool_reframe"),
            InlineKeyboardButton(text="🌿 Микрошаг",  callback_data="tool_micro"),
        ],
    ])

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Стоп", callback_data="tool_stop")]
    ])

def save_insight_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить инсайт", callback_data="save_insight")],
        [InlineKeyboardButton(text="⬅️ Вернуться к чату", callback_data="open_tools")],
    ])

@router.callback_query(F.data == "open_tools")
async def on_open_tools(cb: CallbackQuery):
    await silent_ack(cb)
    await cb.message.answer("Чем займёмся?", reply_markup=tools_keyboard())

@router.callback_query(F.data == "tool_reframe")
async def on_tool_reframe(cb: CallbackQuery):
    await silent_ack(cb)
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        return
    stop_user_task(user_id)
    _reframe_state[user_id] = {"step_idx": 0, "answers": {}}

    last_user = next((m["content"] for m in reversed(DIALOG_HISTORY[cb.message.chat.id]) if m["role"] == "user"), "")
    if last_user:
        preview = last_user[:160] + ("…" if len(last_user) > 160 else "")
        await cb.message.answer(f"Останемся в теме: «{preview}».", reply_markup=stop_keyboard())

    _, prompt = REFRAMING_STEPS[0]
    await cb.message.answer("Запускаю короткую рефлексию (4 шага, ~2 минуты).", reply_markup=stop_keyboard())
    await cb.message.answer(prompt, reply_markup=stop_keyboard())

@router.callback_query(F.data == "tool_micro")
async def on_tool_micro(cb: CallbackQuery):
    await silent_ack(cb)
    chat_id = cb.message.chat.id
    tg_id = str(cb.from_user.id)

    global adapter
    if 'adapter' not in globals() or adapter is None:
        adapter = LLMAdapter()  # type: ignore

    last_user = next((m["content"] for m in reversed(DIALOG_HISTORY[chat_id]) if m["role"] == "user"), "")
    sys_prompt = SYSTEM_PROMPT
    if CHAT_MODE.get(chat_id) == "reflection":
        sys_prompt = SYSTEM_PROMPT + REFLECTIVE_SUFFIX
    messages = [{"role": "system", "content": sys_prompt}]
    if last_user:
        messages.append({"role": "user", "content": last_user})
    messages.append({"role": "user", "content": "Подскажи 1–2 очень маленьких шага на ближайшие 10–30 минут по этой теме."})

    try:
        answer = await adapter.complete_chat(user=tg_id, messages=messages, temperature=0.4)
    except Exception as e:
        answer = f"Не получилось обратиться к модели: {e}"

    _push(chat_id, "assistant", answer)
    _save_turn(tg_id, "assistant", answer)

    await cb.message.answer(answer, reply_markup=None)

@router.callback_query(F.data == "tool_stop")
async def on_tool_stop(cb: CallbackQuery):
    await silent_ack(cb)
    user_id = str(cb.from_user.id)
    stop_user_task(user_id)
    _reframe_state.pop(user_id, None)
    await cb.message.answer("Остановил. Чем могу помочь дальше?", reply_markup=None)

# ==== Текстовый диалог («Поговорить») ======================================
adapter: Optional[LLMAdapter] = None

@router.message(F.text)
async def on_text(m: Message):
    # игнорируем слэш-команды — их ловит другой хэндлер
    if (m.text or "").startswith("/"):
        return

    global adapter
    if adapter is None:
        adapter = LLMAdapter()

    chat_id = m.chat.id
    tg_id = str(m.from_user.id)
    user_text = (m.text or "").strip()

    # если идёт сценарий «Рефлексия» — ведём по шагам
    if tg_id in _reframe_state:
        st = _reframe_state[tg_id]
        step_idx = st["step_idx"]
        key, _prompt = REFRAMING_STEPS[step_idx]
        st["answers"][key] = user_text

        if step_idx + 1 < len(REFRAMING_STEPS):
            st["step_idx"] += 1
            _, next_prompt = REFRAMING_STEPS[st["step_idx"]]
            await m.answer(next_prompt, reply_markup=stop_keyboard())
            return
        else:
            a = st["answers"]
            summary = (
                "🌿 Итог рефлексии\n\n"
                f"• Мысль: {a.get('thought','—')}\n"
                f"• Эмоция (1–10): {a.get('emotion','—')}\n"
                f"• Действие: {a.get('behavior','—')}\n"
                f"• Альтернативная мысль: {a.get('alternative','—')}\n\n"
                "Как это меняет твой взгляд? Что маленькое и конкретное сделаем дальше?"
            )
            _reframe_state.pop(tg_id, None)
            await m.answer(summary, reply_markup=save_insight_keyboard())
            return

    # safety
    if is_crisis(user_text):
        await m.answer(CRISIS_REPLY)
        return

    # мягкий RAG (если доступен)
    if rag_search is not None:
        try:
            rag_ctx = await rag_search(user_text, k=3, max_chars=1200)
        except Exception:
            rag_ctx = ""
    else:
        rag_ctx = ""

    # длинная память (последние дни)
    long_tail = _load_recent_turns(tg_id, days=7, limit=24)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if rag_ctx:
        messages.append({"role": "system", "content": "Короткий контекст:\n" + rag_ctx})
    messages.extend(long_tail[-10:])          # из БД
    messages.extend(DIALOG_HISTORY[chat_id])  # из RAM
    messages.append({"role": "user", "content": user_text})

    try:
        # обязательно передаём user
        answer = await adapter.complete_chat(user=tg_id, messages=messages, temperature=0.6)
    except Exception as e:
        answer = f"Не получилось обратиться к модели: {e}"

    _push(chat_id, "user", user_text)
    _push(chat_id, "assistant", answer)
    _save_turn(tg_id, "user", user_text)
    _save_turn(tg_id, "assistant", answer)

    await m.answer(answer, reply_markup=None)


@router.callback_query(F.data == "reflect:stop")
async def reflect_stop(cb: CallbackQuery):
    CHAT_MODE.pop(cb.message.chat.id, None)
    await cb.message.answer("Остановил рефлексию. Можем вернуться к упражнениям или просто поговорить.")
    try:
        await cb.answer()
    except Exception:
        pass
