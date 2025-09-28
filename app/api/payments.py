from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/payments/yookassa", tags=["payments"])

# Необязательный секрeт для валидации заголовка (если решишь включить)
YK_WEBHOOK_SECRET = os.getenv("YK_WEBHOOK_SECRET", "").strip()


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    cur: Any = obj
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def _as_kop(value_str: str) -> int:
    """
    "1190.00" -> 119000 (копейки)
    """
    try:
        return int((Decimal(value_str) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError):
        return 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/webhook")
async def yookassa_webhook(
    request: Request,
    x_yookassa_signature: Optional[str] = Header(None),  # если включишь подпись — проверяй тут
) -> Response:
    # --- (опционально) простая проверка секрета ---
    if YK_WEBHOOK_SECRET:
        if not x_yookassa_signature or x_yookassa_signature.strip() != YK_WEBHOOK_SECRET:
            # Сигнатуру не валидируем криптографически — просто «секрет из заголовка»
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    obj = _get(payload, "object", default={})
    provider_payment_id = _get(obj, "id")
    status = (_get(obj, "status") or "").lower()
    amount_str = _get(obj, "amount", "value", default="0")
    currency = _get(obj, "amount", "currency", default="RUB")
    pm_id = _get(obj, "payment_method", "id")  # может быть None
    meta_user_id = _get(obj, "metadata", "user_id")  # мы кладём сюда users.id
    plan = (_get(obj, "metadata", "plan", default="") or "").lower()

    if not provider_payment_id:
        raise HTTPException(status_code=400, detail="no payment id")
    if meta_user_id is None:
        raise HTTPException(status_code=400, detail="no user_id in metadata")

    amount_kop = _as_kop(str(amount_str))
    now = _utcnow()

    async with async_session() as session:  # type: AsyncSession
        from app.db.models import User, Payment, Subscription  # type: ignore

        # --- найдём пользователя по users.id; если метадата внезапно была tg_id — аккуратно пробуем вторым шагом
        u = (
            await session.execute(select(User).where(User.id == int(meta_user_id)))
        ).scalar_one_or_none()
        if not u:
            try:
                u = (
                    await session.execute(select(User).where(User.tg_id == int(meta_user_id)))
                ).scalar_one_or_none()
            except Exception:
                u = None
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        # --- upsert платежа по уникальному provider_payment_id ---
        existing_payment = (
            await session.execute(
                select(Payment).where(Payment.provider_payment_id == provider_payment_id)
            )
        ).scalar_one_or_none()

        if existing_payment:
            await session.execute(
                update(Payment)
                .where(Payment.id == existing_payment.id)
                .values(
                    user_id=u.id,
                    provider="yookassa",
                    amount=amount_kop,
                    currency=currency or "RUB",
                    status=status,
                    updated_at=now,
                    raw=obj,
                )
            )
        else:
            # вставка через «сырое» выражение — чтобы не тянуть ORM объект
            await session.execute(
                Payment.__table__.insert().values(
                    user_id=u.id,
                    provider="yookassa",
                    provider_payment_id=provider_payment_id,
                    amount=amount_kop,
                    currency=currency or "RUB",
                    status=status,
                    is_recurring=False,
                    created_at=now,
                    updated_at=now,
                    raw=obj,
                )
            )

        # --- если оплата прошла — активируем/продлеваем подписку (минимально) ---
        if status == "succeeded":
            sub = (
                await session.execute(
                    select(Subscription).where(Subscription.user_id == u.id)
                )
            ).scalar_one_or_none()

            if sub:
                await session.execute(
                    update(Subscription)
                    .where(Subscription.id == sub.id)
                    .values(
                        plan=plan or (sub.plan or "month"),
                        status="active",
                        is_auto_renew=True,
                        updated_at=now,
                    )
                )
            else:
                await session.execute(
                    Subscription.__table__.insert().values(
                        user_id=u.id,
                        plan=plan or "month",
                        status="active",
                        is_auto_renew=True,
                        subscription_until=None,  # если нужно — можно вычислять по plan
                        yk_payment_method_id=pm_id,
                        yk_customer_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )

        await session.commit()

    # пустой 200 — YooKassa довольна
    return Response(status_code=200)
