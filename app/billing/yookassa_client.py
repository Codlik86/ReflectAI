# app/billing/yookassa_client.py
from __future__ import annotations

import os
import uuid
from typing import Optional, Literal

import httpx

# Режимы работы:
# - Если есть YK_SHOP_ID и YK_API_KEY — шлём реальные запросы в YooKassa
# - Иначе работаем в mock-режиме (для локальных/стейдж тестов)
YK_SHOP_ID = os.getenv("YK_SHOP_ID", "").strip()
YK_API_KEY = os.getenv("YK_API_KEY", "").strip()
YK_BASE = "https://api.yookassa.ru/v3"

# Если нужно — можно подложить заглушки
IS_REAL = bool(YK_SHOP_ID and YK_API_KEY)

Headers = {
    "Idempotence-Key": "",
    "Content-Type": "application/json",
}

def _auth():
    return (YK_SHOP_ID, YK_API_KEY)

def _amount_str(amount_rub: int) -> str:
    # Целые рубли -> "1190.00"
    return f"{amount_rub:.2f}"

async def create_payment_link(
    amount_rub: int,
    description: Optional[str],
    return_url: str,
    metadata: dict | None = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Первая оплата через confirmation_url (redirect)
    """
    body = {
        "amount": {"value": _amount_str(amount_rub), "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "description": description or "",
        "metadata": metadata or {},
    }

    if not IS_REAL:
        # MOCK: делаем вид, что ЮKassa вернула ссылку
        return {
            "id": f"mock_{uuid.uuid4().hex[:12]}",
            "status": "pending",
            "amount": {"value": _amount_str(amount_rub), "currency": "RUB"},
            "confirmation": {"type": "redirect", "confirmation_url": "https://example.test/pay"},
        }

    idem = idempotency_key or str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=YK_BASE, auth=_auth(), timeout=30.0) as client:
        r = await client.post(
            "/payments",
            headers={"Idempotence-Key": idem},
            json=body,
        )
        r.raise_for_status()
        return r.json()

async def charge_saved_method(
    *,
    amount_rub: int,
    payment_method_id: str,
    description: Optional[str] = None,
    customer_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    metadata: dict | None = None,
) -> dict:
    """
    Автосписание по сохранённому способу оплаты (payment_method_id / customer_id).
    Вызывается из maintenance/charge_due.
    """
    body = {
        "amount": {"value": _amount_str(amount_rub), "currency": "RUB"},
        "capture": True,
        "description": description or "",
        "metadata": metadata or {},
        "payment_method_id": payment_method_id,
    }
    if customer_id:
        body["customer"] = {"id": customer_id}

    if not IS_REAL:
        # MOCK: имитируем успешное списание
        return {
            "id": f"mock_charge_{uuid.uuid4().hex[:10]}",
            "status": "succeeded",
            "amount": {"value": _amount_str(amount_rub), "currency": "RUB"},
            "payment_method": {"id": payment_method_id, "saved": True},
        }

    idem = idempotency_key or str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=YK_BASE, auth=_auth(), timeout=30.0) as client:
        r = await client.post(
            "/payments",
            headers={"Idempotence-Key": idem},
            json=body,
        )
        r.raise_for_status()
        return r.json()
