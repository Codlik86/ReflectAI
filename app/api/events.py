# app/api/events.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.core import async_session

router = APIRouter(prefix="/api/events", tags=["events"])


class TrackEventIn(BaseModel):
    tg_user_id: int = Field(..., description="Telegram id пользователя (users.tg_id)")
    event: str = Field(..., description="event name, e.g. miniapp_opened or miniapp_action")
    action: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


async def _ensure_user_id_by_tg(tg_id: int) -> int:
    async with async_session() as s:
        r = await s.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": int(tg_id)})
        uid = r.scalar()
        if uid:
            return int(uid)
        r2 = await s.execute(
            text(
                """
                INSERT INTO users (tg_id, privacy_level, style_profile, created_at)
                VALUES (:tg, 'ask', 'default', NOW())
                RETURNING id
                """
            ),
            {"tg": int(tg_id)},
        )
        uid = r2.scalar_one()
        await s.commit()
        return int(uid)


@router.post("/track")
async def track_event(payload: TrackEventIn):
    event_name = (payload.event or "").strip()
    if not event_name:
        raise HTTPException(status_code=400, detail="event is required")

    uid = await _ensure_user_id_by_tg(int(payload.tg_user_id))
    data = {"action": payload.action, "meta": payload.meta}

    async with async_session() as s:
        await s.execute(
            text(
                """
                INSERT INTO bot_events (user_id, event_type, payload, created_at)
                VALUES (:uid, :etype, :payload, CURRENT_TIMESTAMP)
                """
            ),
            {"uid": int(uid), "etype": event_name, "payload": json.dumps(data, ensure_ascii=False)},
        )
        await s.commit()

    return {"ok": True}
