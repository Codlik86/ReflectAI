from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from .llm_adapter import LLMAdapter
from .prompts import SYSTEM_PROMPT
from .safety import is_crisis, CRISIS_REPLY

router = Router()
adapter = None  # <-- отложим создание до первого запроса

@router.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Привет! Я ReflectAI — ассистент для рефлексии на основе КПТ. "
        "Можешь просто выговориться — я рядом."
    )

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Я помогаю осмысливать ситуации, предлагаю мягкие практики и сохраняю инсайты по запросу.\n"
        "В кризисе — подскажу контакты помощи."
    )

@router.message(F.text)
async def on_text(m: Message):
    global adapter
    if adapter is None:
        adapter = LLMAdapter()  # создаём только когда это действительно нужно

    text = (m.text or "").strip()

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

    await m.answer(answer)
