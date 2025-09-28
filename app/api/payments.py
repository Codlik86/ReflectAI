from __future__ import annotations

import os
from typing import Any, Optional, cast
from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/payments/yookassa", tags=["payments"])

YK_WEBHOOK_SECRET = os.getenv("YK_WEBHOOK_SECRET", "").strip()


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def _as_kop(amount_str: str) -> int:
    """
    "1190.00" -> 119000 коп.
    """
    s = str(amount_str).strip().replace(",", ".")
    if "." in s:
        r, c = s.split(".", 1)
        c = (c + "00")[:2]
    else:
        r, c = s, "00"
    if not r:
        r = "0"
    return int(r) * 100 + int(c)


def _utcnow():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc)


@router.post("/webhook")
async def yookassa_webhook(
    request: Request,
    x_yookassa_signature: Optional[str] = Header(None),  # если включишь подпись — проверяй тут
):
    body = await request.json()

    # --- необязательная проверка подписи (секрет задаётся в ENV YK_WEBHOOK_SECRET)
    if YK_WEBHOOK_SECRET:
        sig = (x_yookassa_signature or "").strip()
        if not sig or sig != YK_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="bad signature")

    obj = _get(body, "object", default={})
    event = _get(body, "event", default="")

    provider_payment_id = _get(obj, "id")
    status = _get(obj, "status", default="unknown")
    amount_str = _get(obj, "amount", "value", default="0")
    currency = _get(obj, "amount", "currency", default="RUB")
    pm_id = _get(obj, "payment_method", "id")  # может быть None
    meta_user_id = _get(obj, "metadata", "user_id")
    plan = (_get(obj, "metadata", "plan", default="") or "").lower()

    if not provider_payment_id:
        raise HTTPException(status_code=400, detail="no payment id")
    if meta_user_id is None:
        raise HTTPException(status_code=400, detail="no user_id in metadata")

    amount_kop = _as_kop(amount_str)
    now = _utcnow()

    async with async_session() as _s:
        session = cast(AsyncSession, _s)
        from app.db.models import User, Payment, Subscription  # type: ignore

        # --- ищем пользователя по users.id; если вдруг прислали tg_id — пробуем вторым шагом
        u = (await session.execute(select(User).where(User.id == int(meta_user_id)))).scalar_one_or_none()
        if not u:
            try:
                u = (await session.execute(select(User).where(User.tg_id == int(meta_user_id)))).scalar_one_or_none()
            except Exception:
                u = None
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        # --- апсерт платежа по уникальному provider_payment_id
        existing = (await session.execute(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )).scalar_one_or_none()

        if existing:
            existing.status = status
            existing.updated_at = now
            try:
                existing.raw = body  # type: ignore[assignment]
            except Exception:
                pass
        else:
            p = Payment(
                user_id=u.id,
                provider="yookassa",
                provider_payment_id=provider_payment_id,
                amount=amount_kop,
                currency=currency,
                status=status,
                is_recurring=False,
                created_at=now,
                updated_at=now,
                raw=body,  # type: ignore[arg-type]
            )
            session.add(p)

        # --- если платёж успешен — апдейтим/создаём подписку (+ продление, если активна)
        if status == "succeeded":
            # план -> дни
            add_days = 30
            if plan in ("week", "weekly"):
                add_days = 7
            elif plan in ("quarter", "3m", "q"):
                add_days = 90
            elif plan in ("year", "annual", "y"):
                add_days = 365

            sub = (await session.execute(
                select(Subscription).where(Subscription.user_id == u.id)
            )).scalar_one_or_none()

            import datetime as dt
            # расчёт новой даты окончания
            base_until = getattr(sub, "subscription_until", None) if sub else None
            if base_until and base_until > now:
                new_until = base_until + dt.timedelta(days=add_days)
            else:
                new_until = now + dt.timedelta(days=add_days)

            # premium = активна и не истекла
            premium = (new_until is not None) and (new_until > now)

            if sub:
                sub.plan = plan or (sub.plan or "month")
                sub.status = "active"
                sub.is_auto_renew = True
                sub.subscription_until = new_until
                sub.is_premium = premium           # <-- ВАЖНО: всегда проставляем
                sub.tier = getattr(sub, "tier", None) or "basic"
                sub.yk_payment_method_id = pm_id or sub.yk_payment_method_id
                # если в схеме есть доп. поля — обновим их бережно
                try:
                    sub.renewed_at = now
                    sub.expires_at = new_until
                except Exception:
                    pass
                sub.updated_at = now
            else:
                sub = Subscription(
                    user_id=u.id,
    plan=plan or "month",
    status="active",
    is_auto_renew=True,
    subscription_until=new_until,
    is_premium=premium,
    tier="basic",                   # <-- добавили, чтобы не было NULL в NOT NULL колонке
    yk_payment_method_id=pm_id,
    yk_customer_id=None,
    created_at=now,
    updated_at=now,
                )
                # опциональные поля (если есть в твоей модели)
                try:
                    sub.tier = "basic"
                    sub.renewed_at = now
                    sub.expires_at = new_until
                except Exception:
                    pass
                session.add(sub)

        # --- если платёж отменён — помечаем подписку (не удаляем историю)
        elif status in ("canceled", "cancellation_pending"):
            sub = (await session.execute(
                select(Subscription).where(Subscription.user_id == u.id)
            )).scalar_one_or_none()
            if sub:
                sub.status = "canceled"
                sub.is_premium = False              # <-- на всякий случай
                sub.updated_at = now

        await session.commit()

    # YooKassa достаточно 200 OK без тела
    return ""