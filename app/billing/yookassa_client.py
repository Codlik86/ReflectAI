# app/billing/yookassa_client.py
from __future__ import annotations

import os
import decimal
import uuid
from typing import Optional, Literal, Dict, Any

import httpx
import aiohttp

# ===== Конфиг =====
# Поддержим обе переменные ключа: YK_SECRET_KEY (боевое имя) и YK_API_KEY (как было у тебя)
YK_SHOP_ID: str = (os.getenv("YK_SHOP_ID") or "").strip()
YK_SECRET_KEY: str = (os.getenv("YK_SECRET_KEY") or os.getenv("YK_API_KEY") or "").strip()

YK_BASE = "https://api.yookassa.ru/v3"
IS_REAL = bool(YK_SHOP_ID and YK_SECRET_KEY)  # если нет кредов — считаем, что работаем в mock

def _auth_tuple() -> tuple[str, str]:
    """auth для httpx"""
    return (YK_SHOP_ID, YK_SECRET_KEY)

def _auth_aio() -> aiohttp.BasicAuth:
    """auth для aiohttp"""
    return aiohttp.BasicAuth(login=YK_SHOP_ID, password=YK_SECRET_KEY)

def _amount_str(rub: int) -> str:
    # 1190 -> "1190.00"
    return f"{decimal.Decimal(rub):.2f}"

# ======================================================================
# СИНХРОННАЯ обёртка для бота: вернуть ссылку на оплату (confirmation_url)
# ======================================================================
def create_payment_link(
    *,
    amount_rub: int,
    description: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Делает sync-запрос к YooKassa и возвращает confirmation_url.
    В случае ошибки поднимает RuntimeError с кодом и телом ответа,
    чтобы видеть причину в логах Render.
    """
    payload: Dict[str, Any] = {
        "amount": {"value": _amount_str(amount_rub), "currency": "RUB"},
        "capture": True,
        "description": (description or "")[:128],
        "metadata": metadata or {},
        "confirmation": {
            "type": "redirect",
            "return_url": os.getenv("YK_RETURN_URL") or "https://t.me/reflectttaibot?start=paid_ok",
        },
        "save_payment_method": True,
    }

    if not IS_REAL:
        # mock для локалки/стейджа
        return f"https://example.com/mock/{uuid.uuid4().hex}"

    idem = str(uuid.uuid4())
    headers = {
        "Idempotence-Key": idem,
        "Content-Type": "application/json",
        # По опыту иногда помогает явно проставить accept
        "Accept": "application/json",
    }

    try:
        r = httpx.post(
            f"{YK_BASE}/payments",
            auth=_auth_tuple(),          # (shop_id, secret_key)
            headers=headers,
            json=payload,
            timeout=30.0,
        )
    except Exception as e:
        # Сетевые/SSL/таймауты — увидим в логах
        raise RuntimeError(f"YooKassa request failed: {type(e).__name__}: {e}") from e

    # Не используем .raise_for_status(), чтобы включить тело ответа в ошибку
    if r.status_code // 100 != 2:
        body_preview = r.text[:800]  # чтобы не зафлудить логи
        raise RuntimeError(f"YooKassa HTTP {r.status_code}: {body_preview}")

    data = r.json()
    url = (data.get("confirmation") or {}).get("confirmation_url")
    if not url:
        raise RuntimeError(f"YooKassa: confirmation_url not found in response: {data}")

    return url

# ======================================================================
# АСИНХРОННЫЕ функции: создавать платеж и списывать сохранённый метод
# ======================================================================
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
    Если payment_method_id не передан — создаём платёж с редиректом и save_payment_method=True.
    Если передан — делаем рекуррент по сохранённому способу.
    """
    payload: Dict[str, Any] = {
        "amount": {"value": _amount_str(amount_rub), "currency": currency},
        "capture": bool(capture),
        "description": (description or "")[:128],
        "metadata": metadata or {},
    }

    if payment_method_id:
        payload["payment_method_id"] = payment_method_id
    else:
        payload["confirmation"] = {
            "type": "redirect",
            "return_url": os.getenv("YK_RETURN_URL") or "https://t.me/reflectttaibot?start=paid_ok",
        }
        payload["save_payment_method"] = True

    if not IS_REAL:
        # MOCK: имитируем ответ YooKassa
        return {
            "id": f"mock_pay_{uuid.uuid4().hex[:10]}",
            "status": "pending",
            "confirmation": {"type": "redirect", "confirmation_url": f"https://example.com/mock/{uuid.uuid4().hex}"},
        }

    headers = {"Idempotence-Key": (idem_key or str(uuid.uuid4()))}
    async with aiohttp.ClientSession(auth=_auth_aio()) as sess:
        async with sess.post(f"{YK_BASE}/payments", json=payload, headers=headers, timeout=60) as r:
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
    async with httpx.AsyncClient(base_url=YK_BASE, auth=_auth_tuple(), timeout=30.0) as client:
        r = await client.post(
            "/payments",
            headers={"Idempotence-Key": idem},
            json=body,
        )
        r.raise_for_status()
        return r.json()
