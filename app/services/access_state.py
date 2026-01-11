# app/services/access_state.py

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, Subscription

TRIAL_DAYS = 5


def _calc_access_state(
    *,
    now: datetime,
    trial_started_at: Optional[datetime],
    trial_expires_at: Optional[datetime],
    subscription_until: Optional[datetime],
    subscription_status: Optional[str],
) -> Dict[str, Any]:
    trial_until = trial_expires_at or (
        trial_started_at + timedelta(days=TRIAL_DAYS) if trial_started_at else None
    )
    trial_active = bool(trial_until and trial_until > now)
    sub_active = bool(
        subscription_until
        and (subscription_status or "") == "active"
        and subscription_until > now
    )
    has_access = bool(sub_active or trial_active)
    reason = "subscription" if sub_active else "trial" if trial_active else "none"
    return {
        "has_access": has_access,
        "reason": reason,
        "trial_until": trial_until,
        "subscription_until": subscription_until,
        "subscription_status": subscription_status,
    }


async def get_access_state(session: AsyncSession, user_id: int) -> Dict[str, Any]:
    now = (await session.execute(select(func.now()))).scalar_one()

    u = (
        await session.execute(select(User).where(User.id == int(user_id)))
    ).scalar_one_or_none()
    if not u:
        return {
            "has_access": False,
            "reason": "none",
            "trial_until": None,
            "subscription_until": None,
            "subscription_status": None,
        }

    sub = (
        await session.execute(
            select(Subscription)
            .where(
                Subscription.user_id == int(user_id),
                Subscription.status == "active",
                Subscription.subscription_until > now,
            )
            .order_by(Subscription.subscription_until.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    sub_until = getattr(sub, "subscription_until", None) if sub else None
    sub_status = getattr(sub, "status", None) if sub else None
    trial_started_at = getattr(u, "trial_started_at", None)
    trial_expires_at = getattr(u, "trial_expires_at", None)

    return _calc_access_state(
        now=now,
        trial_started_at=trial_started_at,
        trial_expires_at=trial_expires_at,
        subscription_until=sub_until,
        subscription_status=sub_status,
    )

async def get_access_status(
    session: AsyncSession,
    user: User,
    *,
    policy_accepted: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Unified access status for API responses (bot + miniapp).
    """
    access_state = await get_access_state(session, user.id)

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.subscription_until.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    plan = getattr(sub, "plan", None) if sub else None
    is_auto_renew = getattr(sub, "is_auto_renew", None) if sub else None
    sub_until_any = getattr(sub, "subscription_until", None) if sub else None

    t_started = getattr(user, "trial_started_at", None)
    t_expires = getattr(user, "trial_expires_at", None)
    trial_until = access_state.get("trial_until")

    trial_ever = bool(t_started or t_expires or sub)
    if policy_accepted is None:
        policy_accepted = bool(getattr(user, "policy_accepted_at", None))

    last_deadline = None
    for d in (sub_until_any, trial_until):
        if d and (last_deadline is None or d > last_deadline):
            last_deadline = d

    reason = access_state.get("reason") or "none"
    if reason == "subscription":
        status = "active"
        until = access_state.get("subscription_until") or last_deadline
    elif reason == "trial":
        status = "trial"
        until = trial_until or last_deadline
    else:
        status = "none"
        until = last_deadline

    return {
        "has_access": bool(access_state.get("has_access")),
        "reason": reason,
        "status": status,
        "until": until,
        "plan": plan,
        "is_auto_renew": is_auto_renew,
        "trial_started_at": t_started,
        "trial_expires_at": t_expires,
        "subscription_until": sub_until_any,
        "trial_ever": trial_ever,
        "needs_policy": not bool(policy_accepted),
    }


__all__ = ["get_access_state", "get_access_status", "TRIAL_DAYS"]
