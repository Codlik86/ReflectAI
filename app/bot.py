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
    stop_user_task,
    debounce_ok,
)
from app.rag_qdrant import search as rag_search

router = Router()
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
        [InlineKeyboardButton(text="⏹️ Стоп", callback_data="tool_stop")]
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

@router.callback_query(F.data == "goal_done")
async def onb_goal_done(cb: CallbackQuery):
    msg = (
        "Что дальше? Несколько вариантов:\n\n"
        "1) Расскажи, что у тебя на душе — коротко или подробно. Я помогу разложить и нащупать опору.\n"
        "2) Нужно быстро выдохнуть — дам дыхательное упражнение на 1 минуту.\n"
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
    level = (m.text or "").strip()
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if u:
            u.privacy_level = level
            s.commit()
    _set_consent(str(m.from_user.id), level == "all")
    await m.answer(f"Ок. Уровень приватности: {level}")


@router.message(F.text.in_({"🧩 Разобраться","Разобраться"}))
async def open_work_text(m: Message):
    await cmd_work(m)


@router.message(F.text.in_({"🎧 Медитации","Медитации"}))
async def open_medit_text(m: Message):
    await cmd_meditations(m)


@router.message(F.text.in_({"⚙️ Настройки","Настройки"}))
async def open_settings_text(m: Message):
    await m.answer("Тут будут настройки (тон, подход, приватность). Пока — в разработке.")


@router.message(F.text.in_({"💬 Поговорить","Поговорить"}))
async def talk_text(m: Message):
    await m.answer("Я рядом. Можешь просто написать, что на душе.")
# -------------------- СВОБОДНЫЙ ЧАТ --------------------
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

    await m.answer(answer, reply_markup=tools_keyboard())

# -------------------- ПРАКТИКИ --------------------
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

    await cb.message.answer(answer, reply_markup=tools_keyboard())
    await cb.answer()

@router.callback_query(F.data == "tool_stop")
async def on_tool_stop(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    stop_user_task(user_id)
    _reframe_state.pop(user_id, None)
    await cb.message.answer("Остановил. Чем могу помочь дальше?", reply_markup=tools_keyboard())
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
