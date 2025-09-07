# app/bot.py
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.llm_adapter import LLMAdapter
from app.prompts import SYSTEM_PROMPT
from app.safety import is_crisis, CRISIS_REPLY
from app.db import db_session, User, Insight
from app.tools import (
    REFRAMING_STEPS, BODY_SCAN_TEXT,
    start_breathing_task, stop_user_task, has_running_task, debounce_ok
)

router = Router()
adapter = None
_reframe_state: dict[str, dict] = {}  # user_id -> {"step_idx": int, "answers": dict}

def tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Рефрейминг", callback_data="tool_reframe")],
        [InlineKeyboardButton(text="🫁 Дыхательная пауза 60 сек", callback_data="tool_breathe")],
        [InlineKeyboardButton(text="🧘 Body Scan (3–5 мин)", callback_data="tool_bodyscan")],
    ])

def save_insight_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить инсайт", callback_data="save_insight")],
        [InlineKeyboardButton(text="🧰 Практики", callback_data="open_tools")],
    ])

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹️ Стоп", callback_data="tool_stop")]
    ])

@router.message(CommandStart())
async def start(m: Message):
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if not u:
            s.add(User(tg_id=str(m.from_user.id), privacy_level="insights"))
            s.commit()
    await m.answer(
        "Привет! Я ReflectAI — ассистент для рефлексии на основе КПТ.\n"
        "Можешь просто выговориться — я рядом.\n\n"
        "Полезные команды: /privacy /insights /export /delete_me /help",
        reply_markup=tools_keyboard()
    )

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Я помогаю осмысливать ситуации, предлагаю мягкие практики и сохраняю инсайты по запросу.\n"
        "В кризисе — подскажу контакты помощи.\n\n"
        "Команды:\n"
        "• /privacy — уровень приватности (none | insights | all)\n"
        "• /insights — показать последние сохранённые\n"
        "• /export — выгрузить все инсайты\n"
        "• /delete_me — удалить все мои данные",
        reply_markup=tools_keyboard()
    )

@router.message(Command("test"))
async def test_cmd(m: Message):
    await m.answer("Тест клавиатуры 👇", reply_markup=tools_keyboard())

# === PRIVACY ===
@router.message(Command("privacy"))
async def privacy_cmd(m: Message):
    await m.answer(
        "Выбери уровень приватности (пришли одним словом):\n"
        "• none — ничего не хранить\n"
        "• insights — хранить только сохранённые инсайты (по умолчанию)\n"
        "• all — можно хранить всё (для будущих фич)\n"
    )

@router.message(F.text.in_({"none", "insights", "all"}))
async def set_privacy(m: Message):
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if u:
            u.privacy_level = m.text.strip()
            s.commit()
    await m.answer(f"Ок. Уровень приватности: {m.text.strip()}")

# === MAIN CHAT ===
@router.message(F.text)
async def on_text(m: Message):
    global adapter
    if adapter is None:
        adapter = LLMAdapter()

    user_id = str(m.from_user.id)
    text = (m.text or "").strip()

    # Если пользователь внутри рефрейминга — ведём по шагам
    if user_id in _reframe_state:
        st = _reframe_state[user_id]
        step_idx = st["step_idx"]
        key, _prompt = REFRAMING_STEPS[step_idx]
        st["answers"][key] = text

        if step_idx + 1 < len(REFRAMING_STEPS):
            st["step_idx"] += 1
            _, next_prompt = REFRAMING_STEPS[st["step_idx"]]
            await m.answer(next_prompt, reply_markup=stop_keyboard())
            return
        else:
            a = st["answers"]
            summary = (
                "🧩 Итог рефрейминга\n\n"
                f"• Мысль: {a.get('thought','—')}\n"
                f"• Эмоция (1–10): {a.get('emotion','—')}\n"
                f"• Действие: {a.get('behavior','—')}\n"
                f"• Альтернативная мысль: {a.get('alternative','—')}\n\n"
                "Как это меняет твой взгляд на ситуацию? Что маленькое и конкретное можно сделать дальше?"
            )
            _reframe_state.pop(user_id, None)
            await m.answer(summary, reply_markup=save_insight_keyboard())
            return

    # Обычный диалог
    if is_crisis(text):
        await m.answer(CRISIS_REPLY)
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": text},
    ]

    try:
        answer = await adapter.chat(messages, temperature=0.7)
    except Exception as e:
        answer = f"Упс, не получилось обратиться к модели: {e}"

    await m.answer(answer, reply_markup=save_insight_keyboard())

# === INLINE: сохранить инсайт ===
@router.callback_query(F.data == "save_insight")
async def on_save_insight(cb: CallbackQuery):
    msg = cb.message
    if not msg or not msg.text:
        await cb.answer("Нечего сохранить", show_alert=True)
        return
    preview = msg.text.strip()
    if len(preview) > 1000:
        preview = preview[:1000]
    with db_session() as s:
        s.add(Insight(tg_id=str(cb.from_user.id), text=preview))
        s.commit()
    await cb.answer("Сохранено ✅", show_alert=False)

# === INLINE: открыть меню практик ===
@router.callback_query(F.data == "open_tools")
async def on_open_tools(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer()  # тихо игнорим дабл-клик
        return
    await cb.message.answer("Выбери практику:", reply_markup=tools_keyboard())
    await cb.answer()

# === INLINE: рефрейминг ===
@router.callback_query(F.data == "tool_reframe")
async def on_tool_reframe(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer()
        return
    # если что-то уже крутится (дыхание и т.п.) — остановим
    stop_user_task(user_id)
    _reframe_state[user_id] = {"step_idx": 0, "answers": {}}
    _, prompt = REFRAMING_STEPS[0]
    await cb.message.answer("🔄 Запускаем рефрейминг: 4 шага, займёт ~2 минуты.", reply_markup=stop_keyboard())
    await cb.message.answer(prompt, reply_markup=stop_keyboard())
    await cb.answer()

# === INLINE: дыхательная пауза (остановим всё и запустим новую) ===
@router.callback_query(F.data == "tool_breathe")
async def on_tool_breathe(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer()
        return
    # Сброс любых текущих активностей
    _reframe_state.pop(user_id, None)
    stop_user_task(user_id)

    await cb.message.answer("🫁 Хорошо. Я буду подсказывать ритм в этом сообщении.", reply_markup=stop_keyboard())
    start_breathing_task(cb.message, user_id)  # запускаем асинхронно
    await cb.answer()

# === INLINE: body scan (просто текст + возможность стопа, на случай будущих аудио) ===
@router.callback_query(F.data == "tool_bodyscan")
async def on_tool_bodyscan(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    if not debounce_ok(user_id):
        await cb.answer()
        return
    # Сброс параллельных активностей
    _reframe_state.pop(user_id, None)
    stop_user_task(user_id)
    await cb.message.answer(BODY_SCAN_TEXT, reply_markup=save_insight_keyboard())
    await cb.answer()

# === INLINE: Стоп (останавливает любую текущую практику) ===
@router.callback_query(F.data == "tool_stop")
async def on_tool_stop(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    # Остановим дыхание/таймеры
    stop_user_task(user_id)
    # Сбросим рефрейминг, если шёл
    _reframe_state.pop(user_id, None)
    await cb.message.answer("⏹️ Остановлено. Чем я могу помочь дальше?", reply_markup=tools_keyboard())
    await cb.answer()
