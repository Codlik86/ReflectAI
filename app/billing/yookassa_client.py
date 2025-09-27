# app/billing/yookassa_client.py
import os
import uuid
from typing import Optional

from yookassa import Configuration, Payment

YK_SHOP_ID = os.getenv("YK_SHOP_ID")
YK_SECRET_KEY = os.getenv("YK_SECRET_KEY")
YK_RETURN_URL = os.getenv("YK_RETURN_URL")

class YooKassaError(Exception):
    pass

def _configure():
    if not (YK_SHOP_ID and YK_SECRET_KEY):
        raise YooKassaError("YK_SHOP_ID/YK_SECRET_KEY are not set")
    Configuration.account_id = YK_SHOP_ID
    Configuration.secret_key = YK_SECRET_KEY

def create_payment_link(amount_rub: int, description: str, metadata: dict, return_url: Optional[str] = None) -> str:
    """
    Создаёт платёж YooKassa и возвращает redirect URL.
    amount_rub — целые рубли.
    """
    _configure()
    idempotence_key = str(uuid.uuid4())
    amount_value = f"{int(amount_rub)}.00"

    payment = Payment.create({
        "amount": {"value": amount_value, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": return_url or YK_RETURN_URL or "https://google.com",
        },
        "capture": True,
        "description": description[:127],  # ограничим длину
        "metadata": metadata or {},
    }, idempotence_key)

    # confirmation_url может лежать либо прямо в объекте, либо внутри confirmation
    confirmation = getattr(payment, "confirmation", None) or {}
    url = getattr(confirmation, "confirmation_url", None) or confirmation.get("confirmation_url")
    if not url:
        raise YooKassaError("No confirmation_url in YooKassa response")
    return url
