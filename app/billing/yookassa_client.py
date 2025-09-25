# app/billing/yookassa_client.py
from __future__ import annotations
import os, base64, uuid
from typing import Literal, Optional

import httpx

YK_API = "https://api.yookassa.ru/v3"
YK_SHOP_ID = os.getenv("YK_SHOP_ID", "")
YK_SECRET_KEY = os.getenv("YK_SECRET_KEY", "")
YK_RETURN_URL = os.getenv("YK_RETURN_URL", "https://t.me")

if not (YK_SHOP_ID and YK_SECRET_KEY):
    # Не падаем при импорте — просто не сможем создать платёж
    pass

def _auth_headers() -> dict[str, str]:
    token = base64.b64encode(f"{YK_SHOP_ID}:{YK_SECRET_KEY}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Idempotence-Key": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

async def create_payment_rub(
    amount_rub: int,
    description: str,
    user_id: int,
    plan: Literal["week","month","quarter","year"],
    metadata: Optional[dict] = None,
) -> dict:
    """
    Возвращает JSON платёжа ЮKassa. Ссылку берём из result['confirmation']['confirmation_url'].
    """
    payload = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "description": description[:128],
        "confirmation": {
            "type": "redirect",
            "return_url": YK_RETURN_URL,
        },
        # Сохранить карту для рекуррента
        "save_payment_method": True,
        "payment_method_data": { "type": "bank_card" },
        "metadata": {
            "user_id": str(user_id),
            "plan": plan,
            **(metadata or {}),
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{YK_API}/payments", headers=_auth_headers(), json=payload)
        r.raise_for_status()
        return r.json()
