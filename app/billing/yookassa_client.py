# -*- coding: utf-8 -*-
"""
ЮKassa thin-client: создаёт redirect-ссылку на платёж.
Экспортирует две функции для совместимости с ботом:
- create_payment_link(amount_rub:int, description:str, metadata:dict|None) -> str
- create_redirect_payment(...): legacy-алиас на create_payment_link
"""

from __future__ import annotations
import os
import uuid
from typing import Optional, Dict

try:
    from yookassa import Configuration, Payment
except Exception as e:  # защитим импорт, чтобы не завалить модуль на билд-стадии
    Configuration = None
    Payment = None
    _import_error = e
else:
    _import_error = None

# --- ENV ---
_SHOP_ID     = os.getenv("YK_SHOP_ID", "").strip()
_SECRET_KEY  = os.getenv("YK_SECRET_KEY", "").strip()
_RETURN_URL  = os.getenv("YK_RETURN_URL", "").strip()  # куда вернёт после оплаты

def _ensure_configured() -> None:
    if _import_error:
        raise RuntimeError(f"yookassa SDK not available: {_import_error}")
    if not _SHOP_ID or not _SECRET_KEY:
        raise RuntimeError("YK_SHOP_ID / YK_SECRET_KEY не заданы в окружении")
    Configuration.account_id = _SHOP_ID
    Configuration.secret_key = _SECRET_KEY

def create_payment_link(
    *,
    amount_rub: int,
    description: str,
    metadata: Optional[Dict] = None,
) -> str:
    """
    Создаёт платёж YooKassa (redirect) и возвращает confirmation_url.
    """
    _ensure_configured()
    if not isinstance(amount_rub, int) or amount_rub <= 0:
        raise ValueError("amount_rub должен быть положительным целым числом (в рублях)")

    idempotence_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": _RETURN_URL or "https://t.me/"},
        "capture": True,  # захват средств сразу
        "description": (description or "")[:128],
        "metadata": metadata or {},
    }
    payment = Payment.create(payload, idempotence_key)  # type: ignore[operator]
    # SDK даёт объект; у него есть confirmation.confirmation_url
    url = getattr(getattr(payment, "confirmation", None), "confirmation_url", None)
    if not url:
        raise RuntimeError("YooKassa: не получили confirmation_url")
    return str(url)

# --- legacy-алиас для старого импорт-имени ---
def create_redirect_payment(
    *,
    amount_rub: int,
    description: str,
    metadata: Optional[Dict] = None,
) -> str:
    return create_payment_link(amount_rub=amount_rub, description=description, metadata=metadata)
