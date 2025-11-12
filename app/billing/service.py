# app/billing/service.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, List, Dict, Any, Tuple

from sqlalchemy import text, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# -------------------------------
# Планы и цены — держим в одном месте
# -------------------------------

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
    "month": timedelta(days=30),
    "quarter": timedelta(days=90),
    "year": timedelta(days=365),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def plan_price_rub(plan: str | None) -> int:
    p = (plan or "month").lower()
    if p in PRICES_RUB:
        return PRICES_RUB[p]  # type: ignore[index]
    return PRICES_RUB["month"]


# -------------------------------
# Основная фиксация успешного платежа
# -------------------------------

async def apply_success_payment(
    user_id: int,
    plan: Plan,
    provider_payment_id: str,
    payment_method_id: Optional[str],
    customer_id: Optional[str],
    session: AsyncSession,
    raw_event: Optional[dict] = None,
    *,
    provider: str = "yookassa",
    currency: str = "RUB",
    is_recurring: bool = True,
    amount_override: Optional[int] = None,
) -> None:
    """
    Фиксируем успешный платёж и продлеваем подписку.
    Таблицы: payments, subscriptions (и users только читаем).

    provider/currency/is_recurring/amount_override позволяют переиспользовать
    функцию для разных провайдеров (YooKassa, Telegram Stars и т.п.).
    """
    if plan not in PRICES_RUB:
        raise ValueError(f"Unknown plan: {plan}")

    now = datetime.now(timezone.utc)
    amount = amount_override if amount_override is not None else PRICES_RUB[plan]
    delta = PLAN_TO_DELTA[plan]

    # 1) Записываем платёж
    insert_payment_sql = text(
        """
        INSERT INTO payments (
            user_id, provider, provider_payment_id,
            amount, currency, status, is_recurring, created_at, updated_at, raw
        ) VALUES (
            :user_id, :provider, :provider_payment_id,
            :amount, :currency, 'succeeded', :is_recurring, :now, :now, CAST(:raw_json AS JSON)
        )
        ON CONFLICT DO NOTHING
    """
    )
    await session.execute(
        insert_payment_sql,
        {
            "user_id": user_id,
            "provider": provider,
            "provider_payment_id": provider_payment_id,
            "amount": amount,
            "currency": currency,
            "is_recurring": is_recurring,
            "now": now,
            "raw_json": json.dumps(raw_event or {}),
        },
    )

    # 2) Читаем текущую подписку пользователя (если есть)
    select_sub_sql = text(
        """
        SELECT id, subscription_until
        FROM subscriptions
        WHERE user_id = :user_id
        ORDER BY id DESC
        LIMIT 1
    """
    )
    row = (await session.execute(select_sub_sql, {"user_id": user_id})).first()
    current_until = row[1] if row else None

    # 3) Новая дата окончания: max(now, current_until) + delta
    base_start = current_until if (current_until and current_until > now) else now
    new_until = base_start + delta

    if row:
        update_sql = text(
            """
            UPDATE subscriptions
            SET plan = :plan,
                status = 'active',
                is_auto_renew = :is_recurring,
                subscription_until = :new_until,
                yk_payment_method_id = COALESCE(:pm_id, yk_payment_method_id),
                yk_customer_id = COALESCE(:cust_id, yk_customer_id),
                updated_at = :now
            WHERE user_id = :user_id
        """
        )
        await session.execute(
            update_sql,
            {
                "plan": plan,
                "new_until": new_until,
                "pm_id": payment_method_id,
                "cust_id": customer_id,
                "now": now,
                "user_id": user_id,
                "is_recurring": is_recurring,
            },
        )
    else:
        insert_sub_sql = text(
            """
            INSERT INTO subscriptions (
                user_id, plan, status, is_auto_renew,
                subscription_until, yk_payment_method_id, yk_customer_id,
                created_at, updated_at
            ) VALUES (
                :user_id, :plan, 'active', :is_recurring,
                :new_until, :pm_id, :cust_id,
                :now, :now
            )
        """
        )
        await session.execute(
            insert_sub_sql,
            {
                "user_id": user_id,
                "plan": plan,
                "new_until": new_until,
                "pm_id": payment_method_id,
                "cust_id": customer_id,
                "now": now,
                "is_recurring": is_recurring,
            },
        )

    await session.commit()


# -------------------------------
# Trial helpers (CTA "Начать пробный период")
# -------------------------------

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
    return u.trial_expires_at > utcnow()

async def start_trial_for_user(session: AsyncSession, user_id: int, days: int = 5):
    """Ставит даты триала через ORM-назначения, без bulk UPDATE."""
    from app.db.models import User  # локальный импорт, чтобы избежать циклов
    started = utcnow()
    expires = started + timedelta(days=days)

    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise ValueError("User not found")

    u.trial_started_at = started
    u.trial_expires_at = expires
    # не делаем commit здесь — пусть коммитит вызывающий код
    await session.flush()
    return started, expires

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


# -------------------------------
# YooKassa webhook helper: обновление users.subscription_status
# -------------------------------

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


# -------------------------------
# Автопродление / обслуживание (expire, charge)
# -------------------------------

async def expire_overdue_subscriptions(session: AsyncSession) -> int:
    """
    Пометить все активные подписки, у которых subscription_until <= now, как expired.
    """
    now = utcnow()
    # читаем ORM-ом для совместимости со схемой
    from app.db.models import Subscription  # type: ignore
    subs = (await session.execute(
        select(Subscription).where(
            Subscription.status == "active",
            Subscription.subscription_until <= now,
        )
    )).scalars().all()

    count = 0
    for s in subs:
        s.status = "expired"
        # если в схеме есть поле is_premium — снимем флаг
        try:
            s.is_premium = False
        except Exception:
            pass
        s.updated_at = now
        count += 1

    await session.commit()
    return count


async def get_subscriptions_due(session: AsyncSession, within_hours: int = 24) -> List[Any]:
    """
    Истекающие подписки (<= now + within_hours), у которых включено автообновление.
    Берём и active, и canceled/expired — чтобы можно было «реактивировать/пролонгировать».
    """
    from app.db.models import Subscription  # type: ignore
    horizon = utcnow() + timedelta(hours=max(0, within_hours))

    subs = (await session.execute(
        select(Subscription).where(
            Subscription.is_auto_renew == True,           # noqa: E712
            Subscription.status.in_(("active", "canceled", "expired")),
            Subscription.subscription_until <= horizon,
        )
    )).scalars().all()
    return list(subs)


async def charge_subscription(session: AsyncSession, sub: Any) -> Dict[str, Any]:
    """
    Создаёт платёж через YooKassa API по сохранённому payment_method_id.
    Возвращает JSON YooKassa платежа.
    """
    pm_id = getattr(sub, "yk_payment_method_id", None)
    if not pm_id:
        raise RuntimeError("No payment_method_id saved for subscription")

    user_id = int(getattr(sub, "user_id"))
    plan = getattr(sub, "plan", "month") or "month"
    amount = plan_price_rub(plan)
    description = f"Renew {plan} for user {user_id}"

    # импортируем внутри, чтобы не ловить циклы
    from app.billing.yookassa_client import create_payment

    # простая идемпотентность
    idem_key = f"sub_{getattr(sub, 'id', 'x')}_renew_{int(utcnow().timestamp())}"

    payment = await create_payment(
        amount_rub=amount,
        currency="RUB",
        description=description,
        metadata={"user_id": user_id, "plan": plan},
        payment_method_id=pm_id,
        capture=True,
        idem_key=idem_key,
    )
    return payment

async def charge_due_subscriptions(
    session: AsyncSession,
    within_hours: int = 24,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Ищем истекающие подписки и создаём платежи. Возвращаем сводку.
    """
    due = await get_subscriptions_due(session, within_hours=within_hours)
    ok: List[Dict[str, Any]] = []
    fail: List[Dict[str, Any]] = []

    if dry_run:
        return {"due": [int(getattr(x, "id", 0)) for x in due], "ok": ok, "fail": fail, "dry_run": True}

    for sub in due:
        try:
            p = await charge_subscription(session, sub)
            ok.append({"sub_id": int(getattr(sub, "id", 0)), "yk_id": p.get("id")})
        except Exception as e:
            fail.append({"sub_id": int(getattr(sub, "id", 0)), "error": str(e)})

    return {"due": [int(getattr(x, "id", 0)) for x in due], "ok": ok, "fail": fail, "dry_run": False}

# -------------------------------
# Управление подпиской из бота (cancel / auto_renew off)
# -------------------------------

async def get_active_subscription_row(session: AsyncSession, user_id: int) -> Optional[dict]:
    """
    Возвращает активную подписку пользователя (с максимальным сроком), либо None.
    """
    row = await session.execute(text("""
        SELECT id, user_id, plan, status, is_auto_renew, subscription_until,
               yk_payment_method_id, yk_customer_id
        FROM subscriptions
        WHERE user_id = :uid AND status = 'active'
        ORDER BY subscription_until DESC
        LIMIT 1
    """), {"uid": user_id})
    return row.mappings().first()

async def disable_auto_renew(session: AsyncSession, user_id: int) -> Tuple[bool, Optional[datetime]]:
    """
    Отключает автопродление у активной подписки. Доступ остаётся до конца оплаченного периода.
    Возвращает (изменилось_ли_что-то, subscription_until).
    """
    now = utcnow()
    sub = await get_active_subscription_row(session, user_id)
    if not sub:
        return False, None

    await session.execute(text("""
        UPDATE subscriptions
        SET is_auto_renew = FALSE, updated_at = :now
        WHERE id = :sid
    """), {"sid": sub["id"], "now": now})
    await session.commit()
    return True, sub["subscription_until"]

async def cancel_subscription_now(session: AsyncSession, user_id: int) -> bool:
    """
    Полная отмена подписки: закрываем доступ сразу.
    Ставит status='canceled', is_auto_renew=FALSE, subscription_until=now.
    Плюс пытаемся синхронизировать users.subscription_status, если поле есть.
    """
    now = utcnow()
    sub = await get_active_subscription_row(session, user_id)
    if not sub:
        return False

    await session.execute(text("""
        UPDATE subscriptions
        SET status = 'canceled',
            is_auto_renew = FALSE,
            subscription_until = :now,
            updated_at = :now
        WHERE id = :sid
    """), {"sid": sub["id"], "now": now})

    # best-effort: если есть колонка users.subscription_status — пометим как неактивную
    try:
        await session.execute(text("""
            UPDATE users
            SET subscription_status = 'inactive'
            WHERE id = :uid
        """), {"uid": user_id})
    except Exception:
        pass

    await session.commit()
    return True
