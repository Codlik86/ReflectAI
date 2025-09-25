# app/handlers/pay.py
from __future__ import annotations
import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from app.billing.yookassa_client import create_payment_rub
from app.billing.service import PRICES_RUB  # цены и коды планов

router = Router(name="pay")

PLANS = [
    ("week",    "Неделя — 499 ₽"),
    ("month",   "Месяц — 1190 ₽"),
    ("quarter", "3 месяца — 2990 ₽"),
    ("year",    "Год — 7990 ₽"),
]

def plans_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"pay:{code}")]
        for code, label in PLANS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(Command("pay"))
async def cmd_pay(m: Message):
    text = (
        "Подписка «Помни»\n"
        "• Все функции без ограничений\n"
        "• 5 дней бесплатно, далее по тарифу\n\n"
        "Выбери план:"
    )
    await m.answer(text, reply_markup=plans_keyboard())

@router.callback_query(F.data.startswith("pay:"))
async def choose_plan(cb: CallbackQuery):
    plan = cb.data.split(":", 1)[1]
    if plan not in PRICES_RUB:
        await cb.answer("Неизвестный план", show_alert=True)
        return

    user_id = cb.from_user.id
    amount = PRICES_RUB[plan]
    try:
        payment = await create_payment_rub(
            amount_rub=amount,
            description=f"Подписка Помни — {plan}",
            user_id=user_id,
            plan=plan,
        )
        url = payment["confirmation"]["confirmation_url"]
        await cb.message.answer(
            "Готово! Оформи оплату по ссылке (карта РФ):\n"
            f"{url}\n\n"
            "После оплаты я автоматически активирую подписку.",
        )
        await cb.answer()
    except Exception as e:
        await cb.answer("Не удалось создать оплату. Попробуй позже.", show_alert=True)
