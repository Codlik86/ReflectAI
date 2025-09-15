# app/bot.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from textwrap import dedent
from aiogram import F
import re as _re_for_cmd
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

# --- Emoji (safe Unicode escapes) ---
EMO_TALK = "\\U0001F4AC"       # 💬
EMO_PUZZLE = "\\U0001F9E9"     # 🧩
EMO_HEADPHONES = "\\U0001F3A7" # 🎧
EMO_GEAR = "\\u2699\\ufe0f"  # ⚙️

from aiogram.exceptions import TelegramBadRequest

async def safe_edit(message, *, text: str | None = None, reply_markup=None):
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

from collections import defaultdict, deque
from typing import Dict, Deque, List

from aiogram import Router, F

router = Router()















# app/bot.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from textwrap import dedent
from aiogram import F
import re as _re_for_cmd
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

# --- Emoji (safe Unicode escapes) ---
EMO_TALK = "\\U0001F4AC"       # 💬
EMO_PUZZLE = "\\U0001F9E9"     # 🧩
EMO_HEADPHONES = "\\U0001F3A7" # 🎧
EMO_GEAR = "\\u2699\\ufe0f"  # ⚙️

from aiogram.exceptions import TelegramBadRequest

async def safe_edit(message, *, text: str | None = None, reply_markup=None):
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

from collections import defaultdict, deque
from typing import Dict, Deque, List

from aiogram import Router, F

router = Router()
















# --- universal DONE/FINISH gate: sends user to home screen ---

@router.message(F.text.regexp(r'^/(talk|settings|meditations|about|help|pay|policy)(?:@\w+)?(?:\s|$)'))
async def _route_slash_commands(m: Message):
    cmd = (m.text or '').split()[0].split('@')[0].lower()
    mapping = {
        '/talk': cmd_talk,
        '/settings': cmd_settings,
        '/meditations': cmd_meditations,
        '/about': cmd_about,
        '/help': cmd_help,
        '/pay': cmd_pay,
        '/policy': cmd_policy,
    }
    handler = mapping.get(cmd)
    if handler:
        await handler(m)

@router.callback_query(F.data.func(lambda d: isinstance(d, str) and any(k in d.lower() for k in (
    "onb:done","onboard:done","onboarding:done","goals:done","goal_done",
    "start:done","start:finish","done","finish","complete","completed","готов"
))))
async def cb_done_gate(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass
    text = get_home_text()
    # Попробуем показать главное меню, если есть функция kb_main()
    kb = None
    try:
        kb = kb_main()  # type: ignore[name-defined]
    except Exception:
        kb = None
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)







def back_markup_for_topic(topic_id: str) -> InlineKeyboardMarkup:
    try:
        # если есть полноценная клавиатура со списком упражнений — используем её
        return kb_exercises(topic_id)  # type: ignore[name-defined]
    except Exception:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к темам", callback_data=f"work:topic:{topic_id}")]
        ])

# --- stepper builder with args (adapter) ---
def kb_stepper2(topic_id: str, ex_id: str, cur: int, total: int) -> InlineKeyboardMarkup:
    is_last = (cur >= total-1)
    next_text = "✔️ Завершить" if is_last else "▶️ Далее"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=next_text, callback_data=f"work:step:{topic_id}:{ex_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"work:topic:{topic_id}"),
         InlineKeyboardButton(text="⏹ Стоп", callback_data="work:stop")],
    ])

# minimal main menu (auto-added)

# Инлайн-CTA после онбординга

# --- CTA после онбординга: инлайн-кнопки (надёжно через Builder) ---
def kb_cta_home() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{EMO_TALK} Поговорить", callback_data="cta:talk")
    b.button(text=f"{EMO_PUZZLE} Разобраться", callback_data="cta:work")
    b.button(text=f"{EMO_HEADPHONES} Медитации", callback_data="cta:meditations")
    b.adjust(1)
    return b.as_markup()

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{EMO_TALK} Поговорить", callback_data="talk:hint")],
        [InlineKeyboardButton(text=f"{EMO_PUZZLE} Разобраться", callback_data="work:open"),
         InlineKeyboardButton(text=f"{EMO_HEADPHONES} Медитации", callback_data="meditations:open")],
        [InlineKeyboardButton(text=f"{EMO_GEAR} Настройки", callback_data="settings:open")],
    ])

# ------- helpers: exercise render -------
def render_step_text(topic_title: str, ex_title: str, step_text: str) -> str:
    header = '🧩 ' + topic_title + ' → ' + ex_title
    return header + '\n\n' + step_text

def render_text_exercise(topic_title: str, ex_title: str, text: str) -> str:
    header = '🧩 ' + topic_title + ' → ' + ex_title
    return header + '\n\n' + text

# --- ephemeral per-user state for exercises ---
_WS = {}
def _ws_get(uid: str):
    return _WS.get(uid)
def _ws_set(uid: str, **fields):
    prev = _WS.get(uid) or {}
    prev.update(fields)
    _WS[uid] = prev
def _ws_reset(uid: str):
    _WS.pop(uid, None)

from aiogram.filters import CommandStart, Command
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import text as sql_text

from app.llm_adapter import LLMAdapter
from app.prompts import SYSTEM_PROMPT
from app.safety import is_crisis, CRISIS_REPLY
from app.exercises import TOPICS
from app.db import db_session, User, Insight
from app.tools import (
    REFRAMING_STEPS,
    stop_user_task,
    debounce_ok,
)
from app.rag_qdrant import search as rag_search
from aiogram.utils.keyboard import InlineKeyboardBuilder

adapter: LLMAdapter | None = None

# -------------------- КОРОТКАЯ ПАМЯТЬ (RAM) --------------------
# последние 8 реплик в рамках текущего чата (для «держим тему»)
DIALOG_HISTORY: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=8))

def _push(chat_id: int, role: str, content: str) -> None:
    if content:
        DIALOG_HISTORY[chat_id].append({"role": role, "content": content})

# -------------------- «СУПЕРПАМЯТЬ» (SQLite) --------------------
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

# -------------------- ВНУТРЕННИЕ СОСТОЯНИЯ --------------------
# простой сценарий «Рефлексия»
_reframe_state: Dict[str, Dict] = {}  # user_id -> {"step_idx": int, "answers": dict}

# -------------------- КЛАВИАТУРЫ --------------------
def tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💡 Рефлексия", callback_data="tool_reframe"),
            InlineKeyboardButton(text="🧩 Микрошаг",  callback_data="tool_micro"),
        ],
    ])

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
    ])

def save_insight_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить инсайт", callback_data="save_insight")],
        [InlineKeyboardButton(text="⬅️ Вернуться к чату", callback_data="open_tools")],
    ])

# -------------------- ОНБОРДИНГ (как у «Дневничка») --------------------
ONB_IMAGES = {
    # поставь свои картинки при желании
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

# -------------------- КОМАНДЫ --------------------
@router.message(CommandStart())
async def start(m: Message):
    await set_bot_commands(m.bot)
    # регистрация
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if not u:
            s.add(User(tg_id=str(m.from_user.id), privacy_level="insights"))
            s.commit()

    # очистим короткую память чата
    DIALOG_HISTORY.pop(m.chat.id, None)

    caption = (
        "Привет! Я здесь, чтобы поддержать, выслушать и сохранить важное — не стесняйся.\n\n"
        "Перед тем как начать, подтвердим правила и активируем персонализацию.\n"
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
    tg_id = str(cb.from_user.id)
    _set_consent(tg_id, True)  # включаем расширенную персонализацию по умолчанию
    txt = (
        "Класс! Тогда пару быстрых настроек 🛠️\n\n"
        "Выбери, что сейчас важнее (можно несколько), а затем нажми «Готово»:"
    )
    await cb.message.answer(txt, reply_markup=onb_goals_kb())
    await cb.answer()

@router.callback_query(F.data.startswith("goal:"))
async def onb_goal_pick(cb: CallbackQuery):
    code = cb.data.split(":", 1)[1]
    _append_goal(str(cb.from_user.id), code)
    names = {
        "anxiety": "Снизить тревогу",
        "sleep": "Улучшить сон",
        "self": "Повысить самооценку",
        "motivation": "Найти ресурсы и мотивацию",
    }
    await cb.answer(f"Добавил: {names.get(code, code)}", show_alert=False)

@router.callback_query(F.data.startswith("work:topic:"))
async def cb_pick_topic(cb: CallbackQuery):
    topic_id = cb.data.split(":")[2]
    t = TOPICS.get(topic_id, {"title": "Тема"})
    title = t.get("title", "Тема")
    intro = t.get("intro")
    if intro:
        text = "Тема: " + title + "\n\n" + intro
    else:
        text = "Ок, остаёмся в теме «" + title + "». Выбери упражнение ниже."
    await safe_edit(cb.message, text=text, reply_markup=kb_exercises(topic_id))
    await cb.answer()

def kb_exercises(topic_id: str) -> InlineKeyboardMarkup:
    t = TOPICS[topic_id]
    rows = [[InlineKeyboardButton(text=ex["title"], callback_data=f"work:ex:{topic_id}:{ex['id']}")] for ex in t["exercises"]]
    rows.append([InlineKeyboardButton(text="⬅️ Назад к темам", callback_data="work:back_topics")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back_to_exercises(topic_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к упражнениям", callback_data=f"work:back_ex")]
    ])
def kb_stepper() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Далее", callback_data="work:next")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="work:back_ex"), InlineKeyboardButton(text="⏹ Стоп", callback_data="work:stop")],
    ])
_work_state: dict[str, dict] = {}  # user_id -> {"topic": str|None, "ex": (topic_id, ex_id)|None, "step": int}
def _ws_get(uid: str) -> dict: return _work_state.get(uid, {"topic": None, "ex": None, "step": 0})
def _ws_set(uid: str, **kw) -> dict: st = _ws_get(uid); st.update(kw); _work_state[uid] = st; return st

@router.message(Command("work"))
async def cmd_work(m: Message):
    _ws_set(str(m.from_user.id), topic=None, ex=None, step=0)
    await m.answer("Выбери тему, с которой хочешь поработать:", reply_markup=kb_topics())

@router.message(Command("meditations"))
async def cmd_meditations(m: Message):
    uid = str(m.from_user.id)
    _ws_set(uid, topic="meditations", ex=None, step=0)
    t = TOPICS["meditations"]
    await m.answer(f"Тема: {t['title']}\n{t['intro']}", reply_markup=kb_exercises("meditations"))

# Текстовые триггеры
@router.message(F.text.in_({"🧩 Разобраться","Разобраться"}))
async def open_work_text(m: Message):
    _ws_set(str(m.from_user.id), topic=None, ex=None, step=0)
    await m.answer("Выбери тему, с которой хочешь поработать:", reply_markup=kb_topics())

@router.message(F.text.in_({"🎧 Медитации","Медитации"}))
async def open_medit_text(m: Message):
    uid = str(m.from_user.id)
    _ws_set(uid, topic="meditations", ex=None, step=0)
    t = TOPICS["meditations"]
    await m.answer(f"Тема: {t['title']}\n{t['intro']}", reply_markup=kb_exercises("meditations"))

@router.message(F.text.in_({"⚙️ Настройки","Настройки"}))
async def open_settings_text(m: Message):
    await m.answer("Тут будут настройки (тон, подход, приватность). Пока — в разработке.")

@router.message(F.text.in_({"💬 Поговорить","Поговорить"}))
async def talk_text(m: Message):
    await m.answer("Я рядом. Можешь просто написать, что на душе.")

@router.callback_query(F.data.startswith("work:topic:"))
async def cb_pick_topic(cb: CallbackQuery):
    topic_id = cb.data.split(":")[2]
    t = TOPICS.get(topic_id, {"title": "Тема"})
    title = t.get("title", "Тема")
    intro = t.get("intro")
    if intro:
        text = "Тема: " + title + "\n\n" + intro
    else:
        text = "Ок, остаёмся в теме «" + title + "». Выбери упражнение ниже."
    await safe_edit(cb.message, text=text, reply_markup=kb_exercises(topic_id))
    await cb.answer()


@router.callback_query(F.data.startswith("work:ex:"))
async def cb_pick_exercise(cb: CallbackQuery):
    parts = cb.data.split(":")
    topic_id, ex_id = parts[2], parts[3]
    t = TOPICS.get(topic_id, {})
    ex = None
    for item in t.get("exercises", []):
        if item.get("id") == ex_id:
            ex = item
            break
    if ex is None:
        await cb.answer("Не нашёл упражнение", show_alert=True)
        return

    topic_title = t.get("title", "Тема")
    ex_title = ex.get("title", "Упражнение")

    # 2.1) если это текстовое упражнение — рендерим текст и даём "Назад"
    text_only = ex.get("text") or ex.get("body") or ex.get("content")
    if text_only and not ex.get("steps"):
        text = render_text_exercise(topic_title, ex_title, str(text_only))
        await safe_edit(cb.message, text=text, reply_markup=back_markup_for_topic(topic_id))
        await cb.answer()
        return

    # 2.2) обычные шаги (+ интро как шаг 0, если есть)
    steps = ex.get("steps", [])
    intro = ex.get("intro")
    steps_all = ([intro] + steps) if intro else steps

    if not steps_all:
        await cb.answer("Пустое упражнение", show_alert=True)
        return

    uid = str(cb.from_user.id)
    _ws_set(uid, topic=topic_id, ex=ex_id, step=0)

    text = render_step_text(topic_title, ex_title, steps_all[0])
    # используем адаптерную клавиатуру с аргументами
    await safe_edit(cb.message, text=text, reply_markup=kb_stepper2(topic_id, ex_id, 0, len(steps_all)))
    await cb.answer()
@router.callback_query(F.data == "work:next")
async def cb_next(cb: CallbackQuery):
    uid = str(cb.from_user.id); st = _ws_get(uid)
    if not st.get("ex"):
        return await cb.answer()
    topic_id, ex_id = st["ex"]
    ex = next(e for e in TOPICS[topic_id]["exercises"] if e["id"] == ex_id)
    step = st.get("step", 0) + 1
    steps = ex.get("steps") or []
    if step >= len(steps):
        _ws_set(uid, ex=None, step=0)
        await safe_edit(cb.message, text="✅ Готово. Хочешь выбрать другое упражнение или тему?", reply_markup=kb_exercises(topic_id))
        return await cb.answer()
    _ws_set(uid, step=step)
    await safe_edit(cb.message, text=f"🧩 {TOPICS[topic_id]['title']} → {ex['title']}\n\n{steps[step]}", reply_markup=kb_stepper2())
    await cb.answer()
    if not st.get("ex"):
        return await cb.answer()
    topic_id, ex_id = st["ex"]
    ex = next(e for e in TOPICS[topic_id]["exercises"] if e["id"] == ex_id)
    step = st.get("step", 0) + 1
    steps = ex.get("steps") or []
    if step >= len(steps):
        _ws_set(uid, ex=None, step=0)
        await safe_edit(cb.message, text="✅ Готово. Хочешь выбрать другое упражнение или тему?", reply_markup=kb_exercises(topic_id))
        return await cb.answer()
    _ws_set(uid, step=step)
    await safe_edit(cb.message, text=f"🧩 {TOPICS[topic_id]['title']} → {ex['title']}\n\n{steps[step]}", reply_markup=kb_stepper2())
    await cb.answer()
    if not st.get("ex"): return await cb.answer()
    topic_id, ex_id = st["ex"]
    ex = next(e for e in TOPICS[topic_id]["exercises"] if e["id"] == ex_id)
    step = st.get("step",0)+1
    if step >= len(ex["steps"]):
        _ws_set(uid, ex=None, step=0)
        await cb.message.edit_text("✅ Готово. Хочешь выбрать другое упражнение или тему?")
        await cb.message.edit_reply_markup(reply_markup=kb_exercises(topic_id))
        return await cb.answer()
    _ws_set(uid, step=step)
    await cb.message.edit_text(f"🧩 {TOPICS[topic_id]['title']} → {ex['title']}\n\n{ex['steps'][step]}")
    await cb.answer()

@router.callback_query(F.data == "work:back_ex")
async def cb_back_ex(cb: CallbackQuery):
    uid = str(cb.from_user.id); st = _ws_get(uid)
    topic_id = st.get("topic")
    if not topic_id: return await cb.answer()
    _ws_set(uid, ex=None, step=0)
    t = TOPICS[topic_id]
    await cb.message.edit_text(f"Тема: {t['title']}\n{t['intro']}")
    await cb.message.edit_reply_markup(reply_markup=kb_exercises(topic_id))
    await cb.answer()

@router.callback_query(F.data == "work:back_topics")
async def cb_back_topics(cb: CallbackQuery):
    uid = str(cb.from_user.id); _ws_set(uid, topic=None, ex=None, step=0)
    await cb.message.edit_text("Выбери тему, с которой хочешь поработать:")
    await cb.message.edit_reply_markup(reply_markup=kb_topics())
    await cb.answer()

@router.callback_query(F.data == "work:stop")
async def cb_stop(cb: CallbackQuery):
    # сбросить состояние, если есть хелпер
    try:
        _ = _ws_set  # проверим наличие
        _ws_set(str(cb.from_user.id), topic=None, ex=None, step=0)
    except NameError:
        pass
    await safe_edit(
        cb.message,
        text="Остановил упражнение. Можем просто поговорить или выбрать другую тему.",
        reply_markup=None,
    )
    await cb.answer()

@router.callback_query
async def __ignore_other_cb(cb: CallbackQuery):
    # фолбэк заглушка, чтобы случайные колбэки не роняли обработку
    return
@router.message(Command("tone"))
async def cmd_tone(m: Message):
    await m.answer("Тон общения (заглушка):\n• Нейтральный — по умолчанию\n• Тёплый и поддерживающий\n• Более структурный/короткий\n\nПозже здесь будет выбор с кнопками.")

@router.message(Command("method"))
async def cmd_method(m: Message):
    await m.answer("Подходы (заглушка):\n• КПТ\n• АСТ\n• Гештальт\n\nСкоро можно будет выбрать предпочтение.")

@router.message(Command("about"))
async def cmd_about(m: Message):
    await m.answer("Pomni — тёплый AI-друг/дневник: слушает, помогает осмыслить переживания, предлагает упражнения и микрошаги.")

@router.message(F.text)
async def on_text(m: Message):
    if (m.text or '').startswith('/'):
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
                "🧩 Итог рефлексии\n\n"
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

    # мягкий RAG
    try:
        rag_ctx = await rag_search(user_text, k=3, max_chars=1200)
    except Exception:
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
        # ВАЖНО: передаём user (раньше из-за этого падало)
        answer = await adapter.complete_chat(user=tg_id, messages=messages, temperature=0.6)
    except Exception as e:
        answer = f"Не получилось обратиться к модели: {e}"

    _push(chat_id, "user", user_text)
    _push(chat_id, "assistant", answer)
    _save_turn(tg_id, "user", user_text)
    _save_turn(tg_id, "assistant", answer)

    await m.answer(answer, reply_markup=None)

# -------------------- ПРАКТИКИ --------------------
@router.callback_query(F.data == "open_tools")
async def on_open_tools(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer(); return
    await cb.message.answer("Чем займёмся?", reply_markup=None)
    await cb.answer()

@router.callback_query(F.data == "tool_reframe")
async def on_tool_reframe(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer(); return
    stop_user_task(user_id)
    _reframe_state[user_id] = {"step_idx": 0, "answers": {}}

    last_user = next((m["content"] for m in reversed(DIALOG_HISTORY[cb.message.chat.id]) if m["role"] == "user"), "")
    if last_user:
        preview = last_user[:160] + ("…" if len(last_user) > 160 else "")
        await cb.message.answer(f"Останемся в теме: «{preview}».", reply_markup=stop_keyboard())

    _, prompt = REFRAMING_STEPS[0]
    await cb.message.answer("Запускаю короткую рефлексию (4 шага, ~2 минуты).", reply_markup=stop_keyboard())
    await cb.message.answer(prompt, reply_markup=stop_keyboard())
    await cb.answer()

@router.callback_query(F.data == "tool_micro")
async def on_tool_micro(cb: CallbackQuery):
    global adapter
    if adapter is None:
        adapter = LLMAdapter()

    chat_id = cb.message.chat.id
    tg_id = str(cb.from_user.id)

    last_user = next((m["content"] for m in reversed(DIALOG_HISTORY[chat_id]) if m["role"] == "user"), "")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if last_user:
        messages.append({"role": "user", "content": last_user})
    messages.append({"role": "user", "content": "Подскажи 1–2 очень маленьких шага на ближайшие 10–30 минут по этой теме."})

    try:
        # Тоже передаём user
        answer = await adapter.complete_chat(user=tg_id, messages=messages, temperature=0.4)
    except Exception as e:
        answer = f"Не получилось обратиться к модели: {e}"

    _push(chat_id, "assistant", answer)
    _save_turn(tg_id, "assistant", answer)

    await cb.message.answer(answer, reply_markup=None)
    await cb.answer()

@router.callback_query(F.data == "tool_stop")
async def on_tool_stop(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    stop_user_task(user_id)
    _reframe_state.pop(user_id, None)
    await cb.message.answer("Остановил. Чем могу помочь дальше?", reply_markup=None)
    await cb.answer()

# -------------------- ИНСАЙТЫ --------------------
@router.callback_query(F.data == "save_insight")
async def on_save_insight(cb: CallbackQuery):
    msg = cb.message
    text = (msg.text or msg.caption or "").strip() if msg else ""
    if not text:
        await cb.answer("Нечего сохранить", show_alert=True)
        return
    preview = text if len(text) <= 1000 else text[:1000]
    with db_session() as s:
        s.add(Insight(tg_id=str(cb.from_user.id), text=preview))
        s.commit()
    await cb.answer("Сохранено ✅", show_alert=False)
def kb_topics():
    rows = []
    for key in ["panic","anxiety","sadness","anger","sleep","meditations"]:
        title = TOPICS[key]["title"]
        rows.append([InlineKeyboardButton(text=title, callback_data=f"work:topic:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def kb_exercises(topic_id: str):
    t = TOPICS[topic_id]
    rows = [
        [InlineKeyboardButton(text=ex["title"], callback_data=f"work:ex:{topic_id}:{ex['id']}")]
        for ex in t["exercises"]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад к темам", callback_data="work:back_topics")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def kb_stepper():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Далее", callback_data="work:next")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="work:back_ex"), InlineKeyboardButton(text="⏹ Стоп", callback_data="work:stop")],
    ])

@router.callback_query(F.data.startswith("work:step:"))
async def cb_step(cb: CallbackQuery):
    parts = cb.data.split(":")
    topic_id, ex_id = parts[2], parts[3]
    t = TOPICS.get(topic_id, {})
    ex = None
    for item in t.get("exercises", []):
        if item.get("id") == ex_id:
            ex = item
            break
    if ex is None:
        await cb.answer("Не нашёл упражнение", show_alert=True)
        return
    steps = ex.get("steps", [])
    intro = ex.get("intro")
    steps_all = ([intro] + steps) if intro else steps
    total = len(steps_all)
    if not total:
        await cb.answer("Пустое упражнение", show_alert=True)
        return
    uid = str(cb.from_user.id)
    st = _ws_get(uid) or {}
    cur = 0
    if st.get("topic") == topic_id and st.get("ex") == ex_id:
        cur = int(st.get("step", 0)) + 1
    else:
        cur = 1
    if cur >= total:
        await safe_edit(cb.message, text="Готово. Хочешь выбрать другое упражнение?", reply_markup=kb_exercises(topic_id))
        _ws_set(uid, topic=topic_id, ex=None, step=0)
        await cb.answer()
        return
    _ws_set(uid, topic=topic_id, ex=ex_id, step=cur)
    topic_title = t.get("title", "Тема")
    ex_title = ex.get("title", "Упражнение")
    text = render_step_text(topic_title, ex_title, steps_all[cur])
    await safe_edit(cb.message, text=text, reply_markup=kb_stepper2(topic_id, ex_id, cur, total))
    await cb.answer()

@router.callback_query(F.data.in_({'onboarding:done','start:done','done'}))
async def cb_onboarding_done(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.edit_text('Готово! Чем займёмся дальше?', reply_markup=kb_main())
    except Exception:
        await cb.message.answer('Готово! Чем займёмся дальше?', reply_markup=kb_main())

@router.message(F.text.func(lambda t: (t or '').replace('✅','').strip().lower() in {'готово','готово!' }))
async def msg_onboarding_done(m: Message):
    try:
        await m.answer("Готово! Чем займёмся дальше?", reply_markup=kb_main())
    except Exception:
        # на всякий
        await m.answer("Готово! Чем займёмся дальше?")

# --- last-resort ack: never leave spinner hanging ---


@router.callback_query(F.data == "goal_done")
async def cb_goal_done(cb: CallbackQuery):
    """Финиш онбординга: показываем, что дальше, и основную клавиатуру."""
    try:
        await cb.answer()
    except Exception:
        pass

    text = (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Если хочется просто поговорить — нажми «Поговорить». Поделись, что у тебя на душе, а я поддержу и помогу разложить.\n"
        "2) Нужно быстро разобраться — зайди в «Разобраться». Там короткие упражнения: дыхание, КПТ-мини, заземление и др.\n"
        "3) Хочешь аудио-передышку — «Медитации». (Скоро добавим подборку коротких аудио.)\n\n"
        "Пиши, как удобно — я рядом ❤️"
    )
    try:
        await cb.message.answer(text, reply_markup=kb_main())
    except Exception:
        await cb.message.answer(text)


@router.callback_query()
async def cb_ack_any_callback(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.in_({"goal_done", "onb:done", "onboarding:done", "done"}))
async def onb_goal_done(cb: CallbackQuery):
    # Снимаем спиннер сразу (чтобы не висела анимация)
    try:
        await cb.answer()
    except Exception:
        pass
    # Показываем домашний экран
    text = get_home_text()
    try:
        await cb.message.edit_text(text)
    except Exception:
        await cb.message.answer(text, reply_markup=None)


def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{EMO_TALK} Поговорить"), KeyboardButton(text=f"{EMO_PUZZLE} Разобраться")],
            [KeyboardButton(text=f"{EMO_HEADPHONES} Медитации"), KeyboardButton(text=f"{EMO_GEAR} Настройки")],
        ],
        resize_keyboard=True
    )


async def kb_main() -> ReplyKeyboardMarkup:
    talk = "\U0001F5E3\ufe0f Поговорить"               # 🗣️
    work = "\U0001F9E9 Разобраться"                     # 🧩
    meds = "\U0001F9D8\u200d\u2640\ufe0f Медитации"  # 🧘‍♀️
    sett = "\u2699\ufe0f Настройки"                    # ⚙️
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=talk)],
            [KeyboardButton(text=work)],
            [KeyboardButton(text=meds)],
            [KeyboardButton(text=sett)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        selective=False,
    )

def kb_after_onboard_inline() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="\U0001F5E3\ufe0f Поговорить", callback_data="cta:talk")],
        [InlineKeyboardButton(text="\U0001F9E9 Разобраться", callback_data="cta:work")],
        [InlineKeyboardButton(text="\U0001F9D8\u200d\u2640\ufe0f Медитации", callback_data="cta:meds")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query((F.data == "onboard:done") | (F.data == "onboard:ready"))
async def cb_onboard_done(cb: CallbackQuery):
    text = (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Если хочется просто поговорить — нажми «Поговорить». Можно без структуры и практик.\n"
        "2) Нужно разобраться прямо сейчас — открой «Разобраться»: короткие упражнения на 2–5 минут.\n"
        "3) А ещё будут аудио-медитации — скоро добавим раздел «Медитации».\n\n"
        "Пиши, как удобно — я рядом ❤️"
    )
    await cb.message.answer(text, reply_markup=kb_cta_home())
    await cb.answer()

@router.callback_query(F.data == "cta:talk")
async def cb_cta_talk(cb: CallbackQuery):
    await cb.message.answer("Я здесь. Можешь просто написать, что на душе — начнём разговор.", reply_markup=(await kb_main() if callable(globals().get('kb_main')) else kb_main()))
    await cb.answer()

def _kb_topics_from_TOPICS() -> InlineKeyboardMarkup:
    rows = []
    for key, t in TOPICS.items():
        title = t.get("title", key)
        rows.append([InlineKeyboardButton(text=title, callback_data=f"work:topic:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data == "cta:work")
async def cb_cta_work(cb: CallbackQuery):
    await cb.message.answer("Выбери тему, с которой хочется разобраться:", reply_markup=_kb_topics_from_TOPICS())
    await cb.answer()

@router.callback_query(F.data == "cta:meds")
async def cb_cta_meds(cb: CallbackQuery):
    await cb.message.answer("Раздел «Медитации» в подготовке. В ближайшее время появятся короткие аудио.", reply_markup=(await kb_main() if callable(globals().get('kb_main')) else kb_main()))
    await cb.answer()

async def _kb_main_any():
    kb = globals().get("kb_main")
    if kb is None:
        return None
    try:
        return await kb()
    except TypeError:
        # kb_main может быть синхронной функцией
        return kb()


def kb_main() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="\U0001F4AC Поговорить")],
        [KeyboardButton(text="\U0001F9E9 Разобраться")],
        [KeyboardButton(text="\U0001F9D8\u200d\u2640\ufe0f Медитации")],
        [KeyboardButton(text="\u2699\ufe0f Настройки")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Меню"
    )


# alias для CTA после онбординга (источник — kb_cta_home)
def kb_onboard_cta():
    return kb_cta_home()


@router.callback_query(F.data == "cta:meditations")
async def cta_meditations(cb: CallbackQuery):
    await safe_edit(cb.message, text="Раздел «Медитации» в разработке. Скоро добавим подборку коротких аудио 🎧")
    await cb.answer()

# === AUTOCMDS START ===
# ВНИМАНИЕ: автодобавленные команды. Не редактируй внутри этого блока — патчи будут перезаписывать.
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

async def set_bot_commands(bot: Bot) -> None:
    """Регистрируем команды в левом (slash) меню Telegram."""
    commands = [
        BotCommand(command="start",       description="Начать / онбординг"),
        BotCommand(command="talk",        description="Поговорить — свободный чат"),
        BotCommand(command="work",        description="Разобраться — упражнения"),
        BotCommand(command="meditations", description="Медитации (аудио, скоро)"),
        BotCommand(command="settings",    description="Настройки"),
        BotCommand(command="about",       description="О боте"),
        BotCommand(command="help",        description="Помощь"),
        BotCommand(command="pay",         description="Оплата / поддержка проекта"),
        BotCommand(command="policy",      description="Правила и политика"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        print("[warn] set_my_commands failed:", e)

# --- Slash handlers -------------------------------------------------------

@router.message(Command("talk"))
async def cmd_talk(m: Message):
    # мягкий вход в свободный разговор
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        (
            "Я рядом. Можешь просто написать, что на душе — без рамок и формата. "
            "Постараюсь поддержать и помочь разложить мысли."
        ),
        reply_markup=kb,
    )

@router.message(Command("work"))
async def cmd_work(m: Message):
    # пробуем открыть список тем напрямую, иначе мягкий фолбэк
    try:
        kb = kb_topics()  # если функция есть — покажем темы
        await m.answer("Выбери тему, с которой хочешь поработать 👇", reply_markup=kb)
    except Exception:
        try:
            kb = kb_main()
        except Exception:
            kb = None
        await m.answer(
            "Открою раздел «Разобраться». Если список тем не появился, нажми кнопку внизу.",
            reply_markup=kb,
        )

@router.message(Command("meditations"))
async def cmd_meditations(m: Message):
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        (
            "Раздел аудио-медитаций скоро добавим. "
            "А пока можно выбрать дыхательные и телесные практики в разделе «Разобраться»."
        ),
        reply_markup=kb,
    )

@router.message(Command("settings"))
async def cmd_settings(m: Message):
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        "Тут будут настройки (тон, подход, приватность). Пока — в разработке.",
        reply_markup=kb,
    )

@router.message(Command("about"))
async def cmd_about(m: Message):
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        (
            "Я — бот-поддержка: можно поговорить, быстро снизить тревогу короткими упражнениями "
            "и сохранить важные мысли. Если что-то не работает — напиши, пожалуйста."
        ),
        reply_markup=kb,
    )

@router.message(Command("help"))
async def cmd_help(m: Message):
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        (
            "Как пользоваться:\\n"
            "• «Поговорить» — свободный диалог\\n"
            "• «Разобраться» — короткие практики\\n"
            "• «Медитации» — скоро аудио\\n"
            "• «Настройки» — тон и подход\\n"
            "• «/policy» — правила и политика"
        ),
        reply_markup=kb,
    )

@router.message(Command("pay"))
async def cmd_pay(m: Message):
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        (
            "Поддержка проекта / оплата — скоро. "
            "Если хочешь поддержать сейчас, напиши, пришлю реквизиты ❤️"
        ),
        reply_markup=kb,
    )

@router.message(Command("policy"))
async def cmd_policy(m: Message):
    try:
        kb = kb_main()
    except Exception:
        kb = None
    await m.answer(
        (
            "Правила и политика:\\n"
            "• Правила: https://tinyurl.com/5n98a7j8\\n"
            "• Политика: https://tinyurl.com/5n98a7j8"
        ),
        reply_markup=kb,
    )
# === AUTOCMDS END ===


def _fallback_cmd_router(m: Message) -> bool:
    t = getattr(m, "text", None)
    if not isinstance(t, str):
        return False
    return bool(_re_for_cmd.match(r'^/(talk|settings|meditations|about|help|pay|policy)(?:@\w+)?\b', t))

@router.message(_fallback_cmd_router)
async def _fallback_cmds(m: Message):
    cmd = m.text.split()[0].split('@')[0]  # '/talk' или '/talk@Bot'
    mapping = {
        '/talk': cmd_talk,
        '/settings': cmd_settings,
        '/meditations': cmd_meditations,
        '/about': cmd_about,
        '/help': cmd_help,
        '/pay': cmd_pay,
        '/policy': cmd_policy,
    }
    handler = mapping.get(cmd)
    if handler:
        await handler(m)

