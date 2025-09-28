from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy import text
from app.db.core import async_session

router = APIRouter(prefix="/api/payments/yookassa", tags=["payments"])

# Если хочешь, можно включить проверку подписи вебхука (пока опционально)
YK_WEBHOOK_SECRET = os.getenv("YK_WEBHOOK_SECRET", "").strip()

def _get_json(data: Any, *path, default=None):
    cur = data
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

_PLAN_DAYS = {
    "week": 7,
    "month": 30,
    "q3": 90,
    "year": 365,
}

@router.post("/webhook")
async def yookassa_webhook(
    request: Request,
    x_yookassa_signature: Optional[str] = Header(None),  # если включишь проверку — можно валидировать здесь
):
    payload = await request.json()

    event = _get_json(payload, "event", default="")
    obj = _get_json(payload, "object", default={}) or {}

    pay_id = _get_json(obj, "id")
    status = _get_json(obj, "status")
    amount_val = _get_json(obj, "amount", "value", default="0")
    currency = _get_json(obj, "amount", "currency", default="RUB")
    pm_id = _get_json(obj, "payment_method", "id", default=None)

    meta_user_id = _get_json(obj, "metadata", "user_id", default=None)
    meta_plan = (_get_json(obj, "metadata", "plan", default="") or "").lower()

    # Базовые проверки
    if not pay_id:
        raise HTTPException(status_code=400, detail="no payment id")
    try:
        user_id = int(meta_user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="metadata.user_id is required (int)")

    # Нормализуем сумму в копейках
    try:
        amount_cents = int(Decimal(str(amount_val)).scaleb(2).quantize(Decimal("1")))
    except Exception:
        amount_cents = 0

    # Запись платежа + апдейт подписки в одной транзакции
    async with async_session() as s:
        # 1) Запишем платёж (ON CONFLICT по уникальному provider_payment_id — не дублировать)
        #    Структура таблицы "payments" соответствует твоим моделям:
        #    (user_id, provider, provider_payment_id, amount, currency, status, is_recurring, created_at, updated_at, raw)
        q_payment = text("""
            INSERT INTO payments (
                user_id, provider, provider_payment_id, amount, currency, status, is_recurring, created_at, updated_at, raw
            )
            VALUES (
                :user_id, 'yookassa', :ykid, :amount, :currency, :status, FALSE, NOW(), NOW(), CAST(:raw AS JSONB)
            )
            ON CONFLICT (provider_payment_id) DO UPDATE
                SET status = EXCLUDED.status,
                    amount = EXCLUDED.amount,
                    currency = EXCLUDED.currency,
                    updated_at = NOW()
            RETURNING id
        """)
        await s.execute(q_payment, {
            "user_id": user_id,
            "ykid": str(pay_id),
            "amount": amount_cents,
            "currency": currency or "RUB",
            "status": status or "",
            "raw": os.getenv("YK_STORE_RAW", "1") and str(payload),
        })

        # 2) Если платёж успешно прошёл — активируем/продлеваем подписку
        if event == "payment.succeeded" or status == "succeeded":
            days = _PLAN_DAYS.get(meta_plan, 30)  # дефолт: месяц
            # upsert в subscriptions: либо создаём, либо продлеваем.
            # subscription_until = max(subscription_until, now) + days
            q_upsert_sub = text("""
                INSERT INTO subscriptions (user_id, plan, status, is_auto_renew, subscription_until, yk_payment_method_id, created_at, updated_at)
                VALUES (:user_id, :plan, 'active', TRUE, (NOW() AT TIME ZONE 'utc') + (:days || ' days')::interval, :pm_id, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE
                    SET plan = EXCLUDED.plan,
                        status = 'active',
                        is_auto_renew = TRUE,
                        subscription_until = GREATEST(
                            COALESCE(subscriptions.subscription_until, (NOW() AT TIME ZONE 'utc')),
                            (NOW() AT TIME ZONE 'utc')
                        ) + (:days || ' days')::interval,
                        yk_payment_method_id = COALESCE(EXCLUDED.yk_payment_method_id, subscriptions.yk_payment_method_id),
                        updated_at = NOW()
            """)
            await s.execute(q_upsert_sub, {
                "user_id": user_id,
                "plan": meta_plan or "month",
                "days": str(days),
                "pm_id": pm_id,
            })

            # 3) Обновим users.subscription_status (если это поле у тебя есть)
            try:
                await s.execute(text("UPDATE users SET subscription_status='active' WHERE id=:uid"), {"uid": user_id})
            except Exception:
                pass

        await s.commit()

    # Ничего не возвращаем — 200 ОК
    return ""
