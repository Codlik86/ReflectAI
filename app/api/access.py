# app/api/access.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session
from app.db.models import User, Subscription

router = APIRouter(prefix="/api/access", tags=["access"])

TRIAL_DAYS = 5  # канонично: 5-дневный отложенный триал


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
    until: datetime | None = None
    plan: str | None = None  # "week" | "month" | "quarter" | "year" | None
    is_auto_renew: bool | None = None


class AccessCheckOut(AccessStatusOut):
    ok: bool


# ---- Вспомогательная бизнес-логика -----------------------------------------
def _trial_until(user: User | None) -> datetime | None:
    if not user or not getattr(user, "trial_started_at", None):
        return None
    return user.trial_started_at + timedelta(days=TRIAL_DAYS)


async def _compose_status(
    session: AsyncSession,
    user: User | None,
) -> AccessStatusOut:
    now = datetime.now(timezone.utc)
    plan: str | None = None
    is_auto_renew: bool | None = None
    until: datetime | None = None
    status = "none"
    has_access = False

    if user:
        # Подписка
        sub = (
            await session.execute(
                select(Subscription).where(Subscription.user_id == user.id)
            )
        ).scalar_one_or_none()

        if sub and sub.subscription_until:
            plan = sub.plan
            is_auto_renew = sub.is_auto_renew
            if sub.subscription_until > now:
                until = sub.subscription_until
                status = "active"
                has_access = True

        # Если подписка не активна — пытаемся дать доступ по триалу
        if not has_access:
            t_until = _trial_until(user)
            if t_until and t_until > now:
                until = t_until
                status = "trial"
                has_access = True

    return AccessStatusOut(
        has_access=has_access,
        status=status,
        until=until,
        plan=plan,
        is_auto_renew=is_auto_renew,
    )


# ---- GET /api/access/status -------------------------------------------------
@router.get("/status", response_model=AccessStatusOut)
async def get_access_status(
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
        )

    # Опционально: авто-старт триала (delayed trial) по запросу клиента
    if payload.start_trial and not getattr(user, "trial_started_at", None):
        await session.execute(
            text("UPDATE users SET trial_started_at = CURRENT_TIMESTAMP WHERE id = :uid"),
            {"uid": int(user.id)},
        );
        await session.commit()

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
