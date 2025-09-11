# app/bot.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Deque, List

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from sqlalchemy import text as sql_text

from app.llm_adapter import LLMAdapter
from app.prompts import SYSTEM_PROMPT
from app.safety import is_crisis, CRISIS_REPLY
from app.db import db_session, User, Insight
from app.tools import (
    REFRAMING_STEPS,
    start_breathing_task,  # можно не показывать в UI, но оставить импорты
    stop_user_task,
    debounce_ok,
)
from app.rag_qdrant import search as rag_search

router = Router()
adapter: LLMAdapter | None = None

# ---------- Диалоговая память ----------
# Короткая память в ОЗУ (последние 8 реплик на чат)
DIALOG_HISTORY: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=8))

def _push(chat_id: int, role: str, content: str) -> None:
    if content:
        DIALOG_HISTORY[chat_id].append({"role": role, "content": content})

# «Суперпамять» в SQLite (по дням)
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
    with db_session() as s:
        _ensure_tables()
        row = s.execute(sql_text("SELECT consent_save_all FROM user_prefs WHERE tg_id=:tg"),
                        {"tg": tg_id}).fetchone()
        return bool(row and row[0])

def _save_turn(tg_id: str, role: str, text: str) -> None:
    if not _can_save_full(tg_id):
        return
    with db_session() as s:
        _ensure_tables()
        s.execute(sql_text(
            "INSERT INTO dialog_turns (tg_id, role, text) VALUES (:tg, :r, :t)"
        ), {"tg": tg_id, "r": role, "t": text[:4000]})
        s.commit()

def _load_recent_turns(tg_id: str, days: int = 7, limit: int = 24) -> List[Dict[str, str]]:
    with db_session() as s:
        _ensure_tables()
        # SQLite: datetime('now','-7 days')
        q = f"""
          SELECT role, text FROM dialog_turns
          WHERE tg_id = :tg AND created_at >= datetime('now','-{int(days)} days')
          ORDER BY id DESC LIMIT {int(limit)}
        """
        rows = s.execute(sql_text(q), {"tg": tg_id}).fetchall()
    # возвращаем в хронологическом порядке
    return [{"role": r, "content": t} for (r, t) in reversed(rows or [])]

# ---------- Простое состояние рефлексии ----------
# в памяти процесса, хватает для MVP
_reframe_state: Dict[str, Dict] = {}  # user_id -> {"step_idx": int, "answers": dict}

# ---------- Клавиатуры ----------
def tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💡 Рефлексия", callback_data="tool_reframe"),
         InlineKeyboardButton(text="🧩 Микрошаг",  callback_data="tool_micro")],
    ])

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹️ Стоп", callback_data="tool_stop")]
    ])

def save_insight_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить инсайт", callback_data="save_insight")],
        [InlineKeyboardButton(text="⬅️ Вернуться к чату", callback_data="open_tools")],
    ])

# ---------- Онбординг «как у Дневничка» ----------
ONB_IMAGES = {
    "cover": "https://i.imgur.com/5o7V7pN.jpeg",  # замени на свои
}

def onb_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👋 Привет, Помни!", callback_data="onb_hi")]
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
    with db_session() as s:
        _ensure_tables()
        s.execute(sql_text("""
            INSERT INTO user_prefs (tg_id, consent_save_all)
            VALUES (:tg, :c)
            ON CONFLICT(tg_id) DO UPDATE SET consent_save_all=:c, updated_at=CURRENT_TIMESTAMP
        """), {"tg": tg_id, "c": 1 if yes else 0})
        s.commit()

def _append_goal(tg_id: str, goal_code: str) -> None:
    with db_session() as s:
        _ensure_tables()
        row = s.execute(sql_text("SELECT goals FROM user_prefs WHERE tg_id=:tg"), {"tg": tg_id}).fetchone()
        goals = set((row[0] or "").split(",")) if row else set()
        if goal_code not in goals:
            goals.add(goal_code)
        s.execute(sql_text("""
            INSERT INTO user_prefs (tg_id, goals)
            VALUES (:tg, :g)
            ON CONFLICT(tg_id) DO UPDATE SET goals=:g, updated_at=CURRENT_TIMESTAMP
        """), {"tg": tg_id, "g": ",".join([g for g in goals if g])})
        s.commit()

# ---------- Команды ----------
@router.message(CommandStart())
async def start(m: Message):
    # регистрируем пользователя, если новый
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if not u:
            s.add(User(tg_id=str(m.from_user.id), privacy_level="insights"))
            s.commit()

    # обнуляем короткую память чата
    DIALOG_HISTORY.pop(m.chat.id, None)

    # картинка + приветствие + ссылки на правила/политику (как у Дневничка)
    caption = (
        "Привет! Я Помни — бот эмоциональной поддержки и друг-дневник.\n\n"
        "Перед тем как начнём, коротко согласуем правила и включим персонализацию.\n"
        "Продолжая, ты соглашаешься с правилами и политикой: "
        "https://bit.ly/hugme_terms • https://bit.ly/hugme_privacy\n\n"
        "Скорее нажимай — и я всё расскажу 👇"
    )
    try:
        await m.answer_photo(ONB_IMAGES["cover"], caption=caption, reply_markup=onb_start_kb())
    except Exception:
        # если картинка не загрузилась — просто текст
        await m.answer(caption, reply_markup=onb_start_kb())

@router.callback_query(F.data == "onb_hi")
async def onb_hi(cb: CallbackQuery):
    tg_id = str(cb.from_user.id)
    _set_consent(tg_id, True)  # включаем полную персонализацию (при необходимости пользователь может изменить через /privacy)

    text = (
        "Класс! Тогда пару быстрых настроек 🛠️\n\n"
        "Выбери, что сейчас важнее (можно несколько), потом нажми «Готово»:"
    )
    await cb.message.answer(text, reply_markup=onb_goals_kb())
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

@router.callback_query(F.data == "goal_done")
async def onb_goal_done(cb: CallbackQuery):
    msg = (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Расскажи, что у тебя на душе — можно коротко или подробно. Я помогу разложить и нащупать опору.\n"
        "2) Если нужно быстро выдохнуть — могу подсказать дыхательное упражнение на 1 минуту.\n"
        "3) Хочешь структуру — попробуем «Рефлексию» или подберём «Микрошаг».\n\n"
        "Пиши как удобно — я здесь ❤️"
    )
    await cb.message.answer(msg, reply_markup=tools_keyboard())
    await cb.answer()

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Я помогаю осмыслять переживания и подбирать мягкие шаги. В кризисе подскажу, что делать.\n\n"
        "Команды:\n"
        "• /privacy — уровень приватности (none | insights | all)\n"
        "• /insights — показать сохранённые инсайты\n"
        "• /export — выгрузить инсайты\n"
        "• /delete_me — удалить все данные",
        reply_markup=tools_keyboard()
    )

@router.message(Command("privacy"))
async def privacy_cmd(m: Message):
    await m.answer(
        "Выбери уровень приватности (введи одним словом):\n"
        "• none — ничего не хранить\n"
        "• insights — хранить только сохранённые инсайты (по умолчанию)\n"
        "• all — хранить весь диалог для персонализации\n"
    )

@router.message(F.text.in_({"none", "insights", "all"}))
async def set_privacy(m: Message):
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if u:
            u.privacy_level = m.text.strip()
            s.commit()
    # синхронизируем с user_prefs.consent_save_all
    _set_consent(str(m.from_user.id), m.text.strip() == "all")
    await m.answer(f"Ок. Уровень приватности: {m.text.strip()}")

# ---------- Свободный чат ----------
@router.message(F.text)
async def on_text(m: Message):
    global adapter
    if adapter is None:
        adapter = LLMAdapter()

    chat_id = m.chat.id
    tg_id = str(m.from_user.id)
    user_text = (m.text or "").strip()

    # Если идёт сценарий рефлексии — передадим в него
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

    # Safety
    if is_crisis(user_text):
        await m.answer(CRISIS_REPLY)
        return

    # RAG (мягко)
    try:
        rag_ctx = await rag_search(user_text, k=3, max_chars=1200)
    except Exception:
        rag_ctx = ""

    # История: короткая + «суперпамять» (последние дни)
    long_tail = _load_recent_turns(tg_id, days=7, limit=24)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if rag_ctx:
        messages.append({"role": "system", "content": "Короткий контекст:\n" + rag_ctx})
    # длинная память (дни)
    messages.extend(long_tail[-10:])  # берём до 10 последних из БД, чтобы не раздувать контекст
    # короткая память (текущая сессия)
    messages.extend(DIALOG_HISTORY[chat_id])
    # текущая реплика
    messages.append({"role": "user", "content": user_text})

    try:
        answer = await adapter.chat(messages, temperature=0.6)
    except Exception as e:
        answer = f"Не получилось обратиться к модели: {e}"

    # обновим памяти
    _push(chat_id, "user", user_text)
    _push(chat_id, "assistant", answer)
    _save_turn(tg_id, "user", user_text)
    _save_turn(tg_id, "assistant", answer)

    await m.answer(answer, reply_markup=tools_keyboard())

# ---------- Практики ----------
@router.callback_query(F.data == "open_tools")
async def on_open_tools(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer(); return
    await cb.message.answer("Чем займёмся?", reply_markup=tools_keyboard())
    await cb.answer()

@router.callback_query(F.data == "tool_reframe")
async def on_tool_reframe(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer(); return
    stop_user_task(user_id)
    _reframe_state[user_id] = {"step_idx": 0, "answers": {}}

    # подсветим текущую тему из истории
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
    """Выдать 1–2 очень маленьких шага из текущего контекста."""
    global adapter
    if adapter is None:
        adapter = LLMAdapter()

    chat_id = cb.message.chat.id
    tg_id = str(cb.from_user.id)

    # соберём тему из последней пользовательской реплики
    last_user = next((m["content"] for m in reversed(DIALOG_HISTORY[chat_id]) if m["role"] == "user"), "")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if last_user:
        messages.append({"role": "user", "content": last_user})
    messages.append({"role": "user", "content": "Подскажи 1–2 очень маленьких шага на ближайшие 10–30 минут по этой теме."})

    try:
        answer = await adapter.chat(messages, temperature=0.4)
    except Exception as e:
        answer = f"Не получилось обратиться к модели: {e}"

    _push(chat_id, "assistant", answer)
    _save_turn(tg_id, "assistant", answer)

    await cb.message.answer(answer, reply_markup=tools_keyboard())
    await cb.answer()

@router.callback_query(F.data == "tool_stop")
async def on_tool_stop(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    stop_user_task(user_id)
    _reframe_state.pop(user_id, None)
    await cb.message.answer("Остановил. Чем могу помочь дальше?", reply_markup=tools_keyboard())
    await cb.answer()

# ---------- Инсайты ----------
@router.callback_query(F.data == "save_insight")
async def on_save_insight(cb: CallbackQuery):
    msg = cb.message
    if not msg or not (msg.text or msg.caption):
        await cb.answer("Нечего сохранить", show_alert=True)
        return
    preview = (msg.text or msg.caption or "").strip()
    if len(preview) > 1000:
        preview = preview[:1000]
    with db_session() as s:
        s.add(Insight(tg_id=str(cb.from_user.id), text=preview))
        s.commit()
    await cb.answer("Сохранено ✅", show_alert=False)
