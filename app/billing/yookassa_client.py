# app/billing/yookassa_client.py
from __future__ import annotations

import os
import decimal
import uuid
from typing import Optional, Dict, Any, Tuple

import httpx
import aiohttp

# ===== Конфиг =====
# Креды: берём YK_SECRET_KEY (боевое имя). Если в .env остался YK_API_KEY — тоже подхватим.
YK_SHOP_ID: str = (os.getenv("YK_SHOP_ID") or "").strip()
YK_SECRET_KEY: str = (os.getenv("YK_SECRET_KEY") or os.getenv("YK_API_KEY") or "").strip()

# Базовый URL ЮKassa
YK_BASE = "https://api.yookassa.ru/v3"

# Флаг: реальный режим (иначе можно мокать/логировать причину)
IS_REAL = bool(YK_SHOP_ID and YK_SECRET_KEY)

# ===== Настройки чеков (receipt) =====
# Если в кабинете включена отправка чеков — этот блок обязателен.
# Можно пробросить e-mail покупателя через metadata["email"].
RECEIPT_EMAIL_FALLBACK: str = (os.getenv("YK_RECEIPT_EMAIL") or "").strip()  # например, receipts@yourdomain.ru
RECEIPT_ITEM_NAME: str = (os.getenv("YK_ITEM_NAME") or "Подписка «Помни»").strip()

# Система налогообложения (СНО). 1 — ОСН. Диапазон 1..6 (смотри доки ЮKassa).
# 1 — ОСН, 2 — УСН доход, 3 — УСН доход-расход, 4 — ЕНВД, 5 — ЕСХН, 6 — Патент
TAX_SYSTEM_CODE: int = int(os.getenv("YK_TAX_SYSTEM_CODE", "1"))

# Код НДС. 1 — без НДС. (Допустимые: 1, 2, 3, 4, 5, 6)
# 1 — без НДС, 2 — 0%, 3 — 10%, 4 — 20%, 5 — расч. ставка 10/110, 6 — расч. ставка 20/120
VAT_CODE: int = int(os.getenv("YK_VAT_CODE", "1"))

def _auth_tuple() -> Tuple[str, str]:
    # httpx.BasicAuth совместим с кортежом (user, password)
    return (YK_SHOP_ID, YK_SECRET_KEY)

def _auth_aio() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(login=YK_SHOP_ID, password=YK_SECRET_KEY)

def _amount_str(rub: int) -> str:
    # 599 -> "599.00"
    return f"{decimal.Decimal(rub):.2f}"

def _build_receipt(*, amount_rub: int, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Формируем тело 'receipt' согласно требованиям ЮKassa.
    Нужны: customer.email ИЛИ customer.phone, items[], tax_system_code.
    Если нет e-mail в metadata — берём из YK_RECEIPT_EMAIL.
    Если и там пусто — возвращаем None (платёж, скорее всего, упадёт с 400 invalid_request/receipt).
    """
    md = metadata or {}
    customer_email = (md.get("email") or "").strip()
    if not customer_email:
        customer_email = RECEIPT_EMAIL_FALLBACK

    if not customer_email:
        # Без e-mail/телефона чек не примут — пусть будет явный лог.
        print("[yk] receipt: no customer email (metadata['email'] or YK_RECEIPT_EMAIL).")
        return None

    receipt = {
        "customer": {
            "email": customer_email
        },
        "items": [
            {
                "description": RECEIPT_ITEM_NAME[:128],
                "amount": {
                    "value": _amount_str(int(amount_rub)),
                    "currency": "RUB",
                },
                "quantity": "1.0",
                "vat_code": VAT_CODE,
                # при необходимости можно добавить payment_subject / payment_mode
                # "payment_subject": "service",
                # "payment_mode": "full_prepayment",
            }
        ],
        "tax_system_code": TAX_SYSTEM_CODE,
    }
    return receipt

# ======================================================================
# СИНХРОННЫЙ helper: получить ссылку на оплату с редиректом
# ======================================================================
def create_payment_link(
    *,
    amount_rub: int,
    description: str,
    metadata: Optional[Dict[str, Any]] = None,
    return_url: Optional[str] = None,
    idempotence_key: Optional[str] = None,
) -> Optional[str]:
    """
    Синхронный helper для создания платежа с редиректом.
    Возвращает URL (confirmation_url) или None при ошибке.
    """
    if not IS_REAL:
        print("[yk] IS_REAL = False — нет YK_SHOP_ID или YK_SECRET_KEY. Проверь .env/Render env vars.")
        return None

    payload: Dict[str, Any] = {
        "amount": {"value": _amount_str(int(amount_rub)), "currency": "RUB"},
        "capture": True,
        "description": (description or "")[:128],
        "metadata": metadata or {},
        "confirmation": {
            "type": "redirect",
            "return_url": return_url or os.getenv("YK_RETURN_URL") or "https://t.me/reflectttaibot?start=paid_ok",
        },
        # просим сохранить платёжный метод для автопродления
        "save_payment_method": True,
    }

    # чек (если включён в личном кабинете, без него будет 400 invalid_request: receipt)
    receipt = _build_receipt(amount_rub=amount_rub, metadata=metadata)
    if receipt:
        payload["receipt"] = receipt

    idem = idempotence_key or str(uuid.uuid4())
    try:
        with httpx.Client(base_url=YK_BASE, auth=_auth_tuple(), timeout=30.0) as client:
            r = client.post(
                "/payments",
                json=payload,
                headers={"Idempotence-Key": idem},
            )
            if r.status_code >= 400:
                # напечатаем подробности, чтобы увидеть первопричину
                try:
                    print(f"[yk] create_payment_link HTTP {r.status_code}: {r.text}")
                except Exception:
                    print(f"[yk] create_payment_link HTTP {r.status_code} (no body)")
                return None

            data = r.json()
            conf = (data or {}).get("confirmation") or {}
            url = conf.get("confirmation_url")
            if not url:
                print(f"[yk] no confirmation_url in response: {data}")
                return None
            return url
    except Exception as e:
        # чтобы не «прятать» ошибку
        print(f"[yk] create_payment_link exception: {e}")
        return None

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

    # чек добавляем и для асинхронного метода
    receipt = _build_receipt(amount_rub=amount_rub, metadata=metadata)
    if receipt:
        payload["receipt"] = receipt

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

    # Для рекуррентных чек тоже может потребоваться (по политике ФЗ-54).
    receipt = _build_receipt(amount_rub=amount_rub, metadata=metadata)
    if receipt:
        body["receipt"] = receipt

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
