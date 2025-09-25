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
