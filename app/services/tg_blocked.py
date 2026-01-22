# app/services/tg_blocked.py
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def mark_user_blocked(
    session: AsyncSession,
    user_id: int,
    tg_id: int | None = None,
    reason: Optional[str] = None,
) -> None:
    try:
        where = "id = :uid"
        params = {"uid": int(user_id)}
        if tg_id is not None:
            where += " AND tg_id = :tg"
            params["tg"] = int(tg_id)

        await session.execute(
            text(
                f"""
                UPDATE public.users
                SET tg_is_blocked = TRUE,
                    tg_blocked_at = NOW()
                WHERE {where}
                """
            ),
            params,
        )
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass


async def mark_user_unblocked(session: AsyncSession, user_id: int) -> None:
    try:
        await session.execute(
            text(
                """
                UPDATE public.users
                SET tg_is_blocked = FALSE
                WHERE id = :uid
                """
            ),
            {"uid": int(user_id)},
        )
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass


__all__ = ["mark_user_blocked", "mark_user_unblocked"]
