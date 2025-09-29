# app/billing/yookassa_client.py
from __future__ import annotations

import os, decimal
import uuid
from typing import Optional, Literal, Dict, Any

import httpx
import aiohttp

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

YK_API = "https://api.yookassa.ru/v3"

def _auth():
    shop_id = os.getenv("YK_SHOP_ID", "")
    secret = os.getenv("YK_SECRET_KEY", "")
    return aiohttp.BasicAuth(login=shop_id, password=secret)

def _amount_str(rub: int) -> str:
    # '1190' -> '1190.00'
    return f"{decimal.Decimal(rub):.2f}"

async def create_payment(
    amount_rub: int,
    currency: str = "RUB",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    payment_method_id: Optional[str] = None,
    capture: bool = True,
    idem_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Если payment_method_id не передан — создаём платёж с редиректом и
    save_payment_method=True (ЮKassa попросит сохранить карту).
    Если передан — повторное списание (автопродление).
    """
    payload: Dict[str, Any] = {
        "amount": {"value": _amount_str(amount_rub), "currency": currency},
        "capture": bool(capture),
        "description": description[:128],
        "metadata": metadata or {},
    }

    if payment_method_id:
        # Рекуррентное списание по сохранённому методу
        payload["payment_method_id"] = payment_method_id
    else:
        # Первый платёж: редирект на страницу оплаты + запрос на сохранение метода
        payload["confirmation"] = {
            "type": "redirect",
            "return_url": os.getenv("YK_RETURN_URL") or "https://t.me/reflectttaibot?start=paid_ok",
        }
        payload["save_payment_method"] = True

    headers = {"Idempotence-Key": idem_key or description or "idem"}
    async with aiohttp.ClientSession(auth=_auth()) as sess:
        async with sess.post(f"{YK_API}/payments", json=payload, headers=headers, timeout=60) as r:
            r.raise_for_status()
            return await r.json()

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
