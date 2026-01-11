# app/api/access.py
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session
from app.db.models import User
from app.services.access_state import get_access_status as get_access_status_svc

router = APIRouter(prefix="/api/access", tags=["access"])

# ---- DB dependency ----------------------------------------------------------
async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as s:
        yield s


# ---- Схемы -----------------------------------------------------------------
class AccessCheckIn(BaseModel):
    tg_user_id: int  # Telegram id пользователя (users.tg_id)
    start_trial: bool | None = None  # опционально: запустить триал (если не запущен)


class AccessStatusOut(BaseModel):
    has_access: bool
    status: str  # "active" | "trial" | "none"
    until: datetime | None = None             # последний дедлайн (даже если уже прошёл)
    plan: str | None = None                   # "week" | "month" | "quarter" | "year" | None
    is_auto_renew: bool | None = None
    needs_policy: bool | None = None

    # НОВОЕ: история для корректного различения pre-trial vs expired
    trial_started_at: datetime | None = None
    trial_expires_at: datetime | None = None
    subscription_until: datetime | None = None
    trial_ever: bool = False                  # был ли когда-либо триал или подписка


class AccessCheckOut(AccessStatusOut):
    ok: bool


# ---- Вспомогательная бизнес-логика -----------------------------------------
async def _compose_status(
    session: AsyncSession,
    user: User | None,
) -> AccessStatusOut:
    if not user:
        return AccessStatusOut(
            has_access=False,
            status="none",
            until=None,
            plan=None,
            is_auto_renew=None,
            needs_policy=None,
            trial_started_at=None,
            trial_expires_at=None,
            subscription_until=None,
            trial_ever=False,
        )
    data = await get_access_status_svc(session, user)
    return AccessStatusOut(
        has_access=bool(data.get("has_access")),
        status=str(data.get("status") or "none"),
        until=data.get("until"),
        plan=data.get("plan"),
        is_auto_renew=data.get("is_auto_renew"),
        needs_policy=data.get("needs_policy"),
        trial_started_at=data.get("trial_started_at"),
        trial_expires_at=data.get("trial_expires_at"),
        subscription_until=data.get("subscription_until"),
        trial_ever=bool(data.get("trial_ever")),
    )


# ---- GET /api/access/status -------------------------------------------------
@router.get("/status", response_model=AccessStatusOut)
async def get_access_status_endpoint(
    user_id: int | None = None,        # users.id (для тестов/админок)
    tg_user_id: int | None = None,     # Telegram id (основной путь)
    tg_id: int | None = None,          # b/c совместимость (если на фронте оставался tg_id)
    session: AsyncSession = Depends(get_db),
):
    user: User | None = None
    if user_id is not None:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
    else:
        # принимаем tg_user_id или устаревший tg_id
        the_tg = tg_user_id if tg_user_id is not None else tg_id
        if the_tg is not None:
            user = (
                await session.execute(select(User).where(User.tg_id == the_tg))
            ).scalar_one_or_none()

    return await _compose_status(session, user)


# ---- POST /api/access/check -------------------------------------------------
@router.post("/check", response_model=AccessCheckOut)
async def check_access(
    payload: AccessCheckIn,
    session: AsyncSession = Depends(get_db),
):
    # Ищем по Telegram id (users.tg_id)
    user = (
        await session.execute(
            select(User).where(User.tg_id == payload.tg_user_id)
        )
    ).scalar_one_or_none()

    if not user:
        return AccessCheckOut(
            ok=False,
            has_access=False,
            status="none",
            until=None,
            plan=None,
            is_auto_renew=None,
            trial_started_at=None,
            trial_expires_at=None,
            subscription_until=None,
            trial_ever=False,
        )

    status_out = await _compose_status(session, user)
    trial_ever = bool(status_out.trial_ever)
    policy_accepted = bool(getattr(user, "policy_accepted_at", None))

    # Опционально: авто-старт триала (delayed trial) по запросу клиента
    if payload.start_trial and policy_accepted and not trial_ever:
        await session.execute(
            text("UPDATE users SET trial_started_at = CURRENT_TIMESTAMP WHERE id = :uid"),
            {"uid": int(user.id)},
        )
        await session.commit()
        await session.refresh(user)
        status_out = await _compose_status(session, user)

    return AccessCheckOut(ok=True, **status_out.dict())


# ---- POST /api/access/accept ------------------------------------------------
class AccessAcceptIn(BaseModel):
    tg_user_id: int


@router.post("/accept")
async def accept_policy(
    payload: AccessAcceptIn,
    session: AsyncSession = Depends(get_db),
):
    user = (
        await session.execute(select(User).where(User.tg_id == payload.tg_user_id))
    ).scalar_one_or_none()
    if not user:
        return {"ok": False, "error": "user_not_found"}

    await session.execute(
        text("UPDATE users SET policy_accepted_at = CURRENT_TIMESTAMP WHERE id = :uid"),
        {"uid": int(user.id)},
    )
    await session.commit()
    return {"ok": True}
