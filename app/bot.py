from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from .llm_adapter import LLMAdapter
from .prompts import SYSTEM_PROMPT
from .safety import is_crisis, CRISIS_REPLY

# NEW:
from .db import db_session, User, Insight

router = Router()
adapter = None  # отложим создание до первого запроса

def save_insight_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить инсайт", callback_data="save_insight")]
    ])

@router.message(CommandStart())
async def start(m: Message):
    # NEW: гарантируем, что юзер есть в БД
    with db_session() as s:
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if not u:
            s.add(User(tg_id=str(m.from_user.id), privacy_level="insights"))
            s.commit()

    await m.answer(
        "Привет! Я ReflectAI — ассистент для рефлексии на основе КПТ. "
        "Можешь просто выговориться — я рядом.\n\n"
        "Полезные команды: /privacy /insights /export /delete_me /help"
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
        "• /delete_me — удалить все мои данные"
    )

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

    text = (m.text or "").strip()

    if is_crisis(text):
        await m.answer(CRISIS_REPLY)
        return

    # TODO: сюда позже добавим RAG-контекст из embeddings_index.json
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": text},
    ]

    try:
        answer = await adapter.chat(messages, temperature=0.7)
    except Exception as e:
        answer = f"Упс, не получилось обратиться к модели: {e}"

    # показываем кнопку "Сохранить инсайт"
    await m.answer(answer, reply_markup=save_insight_keyboard())

# === SAVE INSIGHT (inline) ===
@router.callback_query(F.data == "save_insight")
async def on_save_insight(cb: CallbackQuery):
    # Берём текст последнего сообщения бота в этом чате — Telegram не присылает его прямо здесь,
    # поэтому просим пользователя сохранить вручную через reply, но для MVP упростим:
    # сохраним ТЕКСТ сообщения, на которое нажали кнопку (оно и есть ответ бота).
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

# === LIST INSIGHTS ===
@router.message(Command("insights"))
async def list_insights(m: Message):
    with db_session() as s:
        items = (
            s.query(Insight)
            .filter(Insight.tg_id == str(m.from_user.id))
            .order_by(Insight.created_at.desc())
            .limit(5)
            .all()
        )
    if not items:
        await m.answer("Пока нет сохранённых инсайтов.")
        return

    text = "Последние инсайты:\n\n" + "\n\n".join(
        [f"• {i.text[:300]}" for i in items]
    )
    await m.answer(text)

# === EXPORT ALL ===
@router.message(Command("export"))
async def export_insights(m: Message):
    with db_session() as s:
        items = (
            s.query(Insight)
            .filter(Insight.tg_id == str(m.from_user.id))
            .order_by(Insight.created_at.asc())
            .all()
        )
    if not items:
        await m.answer("Нет данных для экспорта.")
        return
    # Простой JSON-текст (для MVP). Потом сделаем файл-документ.
    import json
    payload = [{"text": it.text, "created_at": it.created_at.isoformat()} for it in items]
    j = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(j) <= 3500:
        await m.answer(f"<code>{j}</code>")
    else:
        await m.answer("Много данных — в следующей версии отдам файлом.")

# === DELETE ME ===
@router.message(Command("delete_me"))
async def delete_me(m: Message):
    with db_session() as s:
        s.query(Insight).filter(Insight.tg_id == str(m.from_user.id)).delete()
        u = s.query(User).filter(User.tg_id == str(m.from_user.id)).first()
        if u:
            s.delete(u)
        s.commit()
    await m.answer("Ок. Все твои данные удалены. Можешь начать сначала через /start.")
