# app/billing/service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Планы и цены — держим в одном месте
Plan = Literal["week", "month", "quarter", "year"]

PRICES_RUB: dict[Plan, int] = {
    "week": 499,
    "month": 1190,
    "quarter": 2990,
    "year": 7990,
}

# На сколько продлеваем подписку
PLAN_TO_DELTA: dict[Plan, timedelta] = {
    "week": timedelta(days=7),
    "month": timedelta(days=30),     # календарные месяцы можно позже заменить на dateutil.relativedelta
    "quarter": timedelta(days=90),
    "year": timedelta(days=365),
}


async def apply_success_payment(
    user_id: int,
    plan: Plan,
    provider_payment_id: str,
    payment_method_id: Optional[str],
    customer_id: Optional[str],
    session: AsyncSession,
) -> None:
    """
    Фиксируем успешный платёж и продлеваем подписку.
    Таблицы: payments, subscriptions, users (только читаем).
    """
    if plan not in PRICES_RUB:
        raise ValueError(f"Unknown plan: {plan}")

    now = datetime.now(timezone.utc)
    amount = PRICES_RUB[plan]
    delta = PLAN_TO_DELTA[plan]

    # 1) Записываем платёж
    insert_payment_sql = text("""
        INSERT INTO payments (
            user_id, provider, provider_payment_id,
            amount, currency, status, is_recurring, created_at, updated_at, raw
        ) VALUES (
            :user_id, 'yookassa', :provider_payment_id,
            :amount, 'RUB', 'succeeded', true, :now, :now, :raw_json
        )
        ON CONFLICT DO NOTHING
    """)
    await session.execute(
        insert_payment_sql,
        {
            "user_id": user_id,
            "provider_payment_id": provider_payment_id,
            "amount": amount,
            "now": now,
            "raw_json": {},  # при желании сюда можно прокинуть весь объект от ЮKassa
        },
    )

    # 2) Читаем текущую подписку пользователя (если есть)
    select_sub_sql = text("""
        SELECT id, subscription_until
        FROM subscriptions
        WHERE user_id = :user_id
        ORDER BY id DESC
        LIMIT 1
    """)
    row = (await session.execute(select_sub_sql, {"user_id": user_id})).first()

    # Считаем новую дату окончания: с максимума(now, old_until) + delta
    if row:
        _, current_until = row
    else:
        current_until = None

    base_start = now
    if current_until and current_until > now:
        base_start = current_until

    new_until = base_start + delta

    if row:
        # 3a) Обновляем существующую подписку
        update_sql = text("""
            UPDATE subscriptions
            SET plan = :plan,
                status = 'active',
                is_auto_renew = true,
                subscription_until = :new_until,
                yk_payment_method_id = COALESCE(:pm_id, yk_payment_method_id),
                yk_customer_id = COALESCE(:cust_id, yk_customer_id),
                updated_at = :now
            WHERE user_id = :user_id
        """)
        await session.execute(
            update_sql,
            {
                "plan": plan,
                "new_until": new_until,
                "pm_id": payment_method_id,
                "cust_id": customer_id,
                "now": now,
                "user_id": user_id,
            },
        )
    else:
        # 3b) Создаём новую подписку
        insert_sub_sql = text("""
            INSERT INTO subscriptions (
                user_id, plan, status, is_auto_renew,
                subscription_until, yk_payment_method_id, yk_customer_id,
                created_at, updated_at
            ) VALUES (
                :user_id, :plan, 'active', true,
                :new_until, :pm_id, :cust_id,
                :now, :now
            )
        """)
        await session.execute(
            insert_sub_sql,
            {
                "user_id": user_id,
                "plan": plan,
                "new_until": new_until,
                "pm_id": payment_method_id,
                "cust_id": customer_id,
                "now": now,
            },
        )

    await session.commit()

# === Trial helpers (CTA "Начать пробный период") ===
from sqlalchemy import select, update

TRIAL_DAYS = 5  # длина пробного периода в днях

async def _get_user_by_id(session: AsyncSession, user_id: int):
    from app.db.models import User
    q = await session.execute(select(User).where(User.id == user_id))
    return q.scalar_one_or_none()

async def _get_user_by_tg(session: AsyncSession, tg_id: int):
    from app.db.models import User
    q = await session.execute(select(User).where(User.tg_id == tg_id))
    return q.scalar_one_or_none()

async def is_trial_active(session: AsyncSession, user_id: int) -> bool:
    """Есть ли у пользователя ещё активный триал (по дате истечения)."""
    u = await _get_user_by_id(session, user_id)
    if not u or not getattr(u, "trial_expires_at", None):
        return False
    return u.trial_expires_at > datetime.now(timezone.utc)

async def start_trial_for_user(session: AsyncSession, user_id: int):
    """
    Стартуем триал: выставляем trial_started_at=now (UTC),
    trial_expires_at=now+TRIAL_DAYS. Возвращаем (started, expires).
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=TRIAL_DAYS)
    from app.db.models import User
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(trial_started_at=now, trial_expires_at=expires)
    )
    return now, expires

async def has_active_subscription(session: AsyncSession, user_id: int) -> bool:
    """
    Минимальная проверка подписки: users.subscription_status == 'active'.
    (Позже можно заменить на полноценную таблицу subscriptions.)
    """
    u = await _get_user_by_id(session, user_id)
    return bool(u and getattr(u, "subscription_status", None) == "active")

async def check_access(session: AsyncSession, user_id: int) -> bool:
    """Доступ к функциям: есть активная подписка ИЛИ активный триал."""
    return (await has_active_subscription(session, user_id)) or (await is_trial_active(session, user_id))

# === YooKassa webhook handler: activate subscription on success ===
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

async def handle_yookassa_webhook(session: AsyncSession, event: dict):
    """
    Ожидаем тело вида:
    {
      "event": "payment.succeeded" | "payment.canceled" | ...,
      "object": {
        "id": "<yk_payment_id>",
        "status": "succeeded" | "canceled" | ...,
        "amount": {"value": "1190.00", "currency": "RUB"},
        "metadata": {"user_id": <int>, "plan": "month" }
      }
    }
    """
    obj = event.get("object") or {}
    event_name = (event.get("event") or "").strip()
    status = (obj.get("status") or "").strip()
    metadata = obj.get("metadata") or {}
    user_id = metadata.get("user_id")

    # --- необязательный учёт платежей (если есть модель Payment) ---
    try:
        from app.db.models import Payment  # если модели нет — блок тихо пропустится
        yk_payment_id = obj.get("id")
        if yk_payment_id:
            q = await session.execute(select(Payment).where(Payment.yk_payment_id == yk_payment_id))
            p = q.scalar_one_or_none()
            if p:
                p.status = status or p.status
            else:
                amount_val = (obj.get("amount") or {}).get("value")
                try:
                    amount_rub = int(float(amount_val)) if amount_val else 0
                except Exception:
                    amount_rub = 0
                session.add(Payment(
                    user_id=int(user_id) if user_id is not None else 0,
                    yk_payment_id=yk_payment_id,
                    status=status or "unknown",
                    plan=str(metadata.get("plan") or ""),
                    amount_rub=amount_rub,
                ))
    except Exception:
        # учёт платежей — best-effort, не ломаем основной поток
        pass

    # --- успешная оплата → активируем подписку пользователю ---
    if event_name == "payment.succeeded" or status == "succeeded":
        if not user_id:
            # без user_id активировать некого
            return
        from app.db.models import User
        await session.execute(
            update(User)
            .where(User.id == int(user_id))
            .values(subscription_status="active")
        )
        return

    # --- отмена/неуспех → просто логируем/фиксируем статус платежа ---
    if event_name == "payment.canceled" or status == "canceled":
        # здесь ничего активировать не нужно
        return
