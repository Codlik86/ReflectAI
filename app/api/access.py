# app/api/access.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session
from app.db.models import User, Subscription

router = APIRouter(prefix="/api/access", tags=["access"])

# ---- DB dependency (унифицировано под наш проект) --------------------------
async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as s:
        yield s

# ---- Схемы для POST /check -------------------------------------------------
class AccessCheckIn(BaseModel):
    tg_user_id: int  # это именно Telegram id пользователя (users.tg_id)

class AccessCheckOut(BaseModel):
    ok: bool
    until: datetime | None = None
    has_auto_renew: bool | None = None

# ---- Вспомогательная функция ----------------------------------------------
async def _status_for_user(
    session: AsyncSession,
    user: User | None,
) -> dict:
    now = datetime.now(timezone.utc)
    plan = None
    trial_started_at = getattr(user, "trial_started_at", None) if user else None
    subscription_until = None
    has_access = False

    if user:
        sub = (await session.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )).scalar_one_or_none()

        if sub:
            plan = sub.plan
            subscription_until = sub.subscription_until
            has_access = bool(subscription_until and subscription_until > now)

    return {
        "has_access": has_access,
        "plan": plan,
        "trial_started_at": trial_started_at,
        "subscription_until": subscription_until,
    }

# ---- GET /api/access/status  ----------------------------------------------
@router.get("/status")
async def get_access_status(
    user_id: int | None = None,       # внутренний users.id (для быстрого теста)
    tg_id: int | None = None,         # альтернативно можно передать Telegram id
    session: AsyncSession = Depends(get_db),
):
    user: User | None = None

    if user_id is not None:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
    elif tg_id is not None:
        user = (await session.execute(
            select(User).where(User.tg_id == tg_id)   # ВАЖНО: поле называется tg_id
        )).scalar_one_or_none()

    return await _status_for_user(session, user)

# ---- POST /api/access/check  ----------------------------------------------
@router.post("/check", response_model=AccessCheckOut)
async def check_access(
    payload: AccessCheckIn,
    session: AsyncSession = Depends(get_db),
):
    # Ищем по Telegram id (tg_id), а не выдуманному tg_user_id в модели
    user = (await session.execute(
        select(User).where(User.tg_id == payload.tg_user_id)  # <-- правильное поле
    )).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if not user:
        return AccessCheckOut(ok=False)

    sub = (await session.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )).scalar_one_or_none()

    if sub and sub.subscription_until and sub.subscription_until > now:
        return AccessCheckOut(ok=True, until=sub.subscription_until, has_auto_renew=sub.is_auto_renew)

    return AccessCheckOut(ok=False, until=sub.subscription_until if sub else None)

# --- POST /api/access/accept -----------------------------------------------
class AccessAcceptIn(BaseModel):
    tg_user_id: int

@router.post("/accept")
async def accept_policy(
    payload: AccessAcceptIn,
    session: AsyncSession = Depends(get_db),
):
    user = (await session.execute(
        select(User).where(User.tg_id == payload.tg_user_id)
    )).scalar_one_or_none()
    if not user:
        return {"ok": False, "error": "user_not_found"}
    await session.execute(
        text("UPDATE users SET policy_accepted_at = CURRENT_TIMESTAMP WHERE id = :uid"),
        {"uid": int(user.id)}
    )
    await session.commit()
    return {"ok": True}
