# app/billing/yookassa_client.py
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Literal, Optional, Tuple

# внешний HTTP-клиент – используем requests, он обычно есть в базовом окружении
# если у тебя в deps его нет — добавь "requests" в requirements.txt
import requests

Plan = Literal["week", "month", "quarter", "year"]

# Твои цены оставляю как есть
PRICES_RUB: dict[Plan, int] = {
    "week": 499,
    "month": 1190,
    "quarter": 2990,
    "year": 7990,
}

# ENV
YK_SHOP_ID = os.getenv("YK_SHOP_ID", "").strip()
YK_SECRET_KEY = os.getenv("YK_SECRET_KEY", "").strip()
# куда вернёт пользователя после оплаты (кнопка «Вернуться в магазин»)
YK_RETURN_URL = os.getenv("YK_RETURN_URL", "https://t.me/").strip()

YOOKASSA_API = "https://api.yookassa.ru/v3/payments"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _is_prod_ready() -> bool:
    return bool(YK_SHOP_ID and YK_SECRET_KEY)


def _build_headers(idempotency_key: str) -> dict:
    # Basic auth c shop_id:secret
    from base64 import b64encode
    auth = b64encode(f"{YK_SHOP_ID}:{YK_SECRET_KEY}".encode()).decode()
    return {
        "Idempotence-Key": idempotency_key,
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


def create_payment_link(*, user_id: int, plan: Plan) -> Tuple[str, str]:
    """
    Создаёт платёж ЮKassa и возвращает кортеж:
    (payment_id, confirmation_url)
    В DEMO-режиме возвращает тестовые значения, чтобы бот не падал.
    """
    amount = PRICES_RUB[plan]
    if not _is_prod_ready():
        # DEMO: генерим фиктивные id/ссылку – бот сможет работать,
        # а оплату ты эмулируешь вебхуками.
        pid = f"test_{_now_str()}"
        url = f"https://yookassa.mock/confirm/{pid}"
        return pid, url

    idem = f"create_{user_id}_{plan}_{_now_str()}"
    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": YK_RETURN_URL},
        "description": f"Pomni {plan} plan",
        "metadata": {"user_id": user_id, "plan": plan},
    }

    resp = requests.post(
        YOOKASSA_API,
        data=json.dumps(payload),
        headers=_build_headers(idem),
        timeout=25,
    )
    # поднимаем исключение, чтобы лог сразу показал проблему конфигурации
    resp.raise_for_status()
    data = resp.json()
    payment_id = data.get("id") or ""
    confirm_url = (data.get("confirmation") or {}).get("confirmation_url") or ""
    if not payment_id or not confirm_url:
        raise RuntimeError(f"Invalid YooKassa response: {data}")
    return payment_id, confirm_url


def charge_saved_method(
    *,
    user_id: int,
    plan: Plan,
    payment_method_id: str,
    customer_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Списание по сохранённому способу оплаты (для автопродлений).
    Возвращает (payment_id, status) – где status обычно 'pending' или 'succeeded'.
    В DEMO-режиме просто генерит тестовый успешный платёж.
    """
    amount = PRICES_RUB[plan]
    if not _is_prod_ready():
        pid = f"autopay_test_{_now_str()}"
        return pid, "succeeded"

    idem = idempotency_key or f"charge_{user_id}_{plan}_{_now_str()}"
    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": f"Pomni auto-renew {plan}",
        "metadata": {"user_id": user_id, "plan": plan, "auto": True},
        # если используешь customer_id – можно добавить:
        # "customer": {"id": customer_id}  # при необходимости
    }

    resp = requests.post(
        YOOKASSA_API,
        data=json.dumps(payload),
        headers=_build_headers(idem),
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("id", ""), data.get("status", "unknown")
