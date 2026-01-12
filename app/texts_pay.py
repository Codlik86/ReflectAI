# app/texts_pay.py
from __future__ import annotations

import os

PAY_HELP_TEXT_RU = (
    "Оформить подписку можно так:\n"
    "1) Введи команду /pay в этом чате — появятся тарифы и кнопка оплаты.\n"
    "2) Либо открой меню бота (кнопка ☰/«команды» рядом со строкой ввода) → нажми «Подписка».\n"
    "Дальше выбери тариф и способ оплаты — Telegram откроет страницу YooKassa.\n"
    "Если что-то не получается — напиши, что именно ты видишь (ошибка/кнопка не открывается), и я подскажу."
)


def get_pay_help_text() -> str:
    email = (os.getenv("CONTACT_EMAIL") or "").strip()
    if not email:
        return PAY_HELP_TEXT_RU
    return PAY_HELP_TEXT_RU + f"\n\nПоддержка: {email}"
