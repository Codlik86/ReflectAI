# app/api/admin.py
import os
from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import get_session

router = APIRouter(prefix="/api/admin", tags=["admin"])

# --- простая Bearer-авторизация ---
auth = HTTPBearer()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

def require_admin(creds: HTTPAuthorizationCredentials = Depends(auth)) -> None:
    if not ADMIN_TOKEN or creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/ping", dependencies=[Depends(require_admin)])
async def ping() -> dict:
    return {"ok": True}

# ---------- USERS ----------
@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(
    q: Optional[str] = Query(None, description="Поиск по tg_id/id/части email/имени"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    from app.db.models import User  # type: ignore

    stmt = select(User).order_by(User.id.desc())
    if q:
        try:
            as_int = int(q)
        except Exception:
            as_int = None
        if as_int is not None:
            stmt = stmt.where(or_(User.tg_id == as_int, User.id == as_int))
        else:
            # если есть поля name/email — можно добавить:
            try:
                stmt = stmt.where(or_(
                    User.privacy_level.ilike(f"%{q}%")
                ))
            except Exception:
                pass

    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    def _row(u) -> dict:
        return {
            "id": u.id,
            "tg_id": u.tg_id,
            "privacy_level": getattr(u, "privacy_level", None),
            "policy_accepted_at": (getattr(u, "policy_accepted_at", None) or None),
            "trial_started_at": (getattr(u, "trial_started_at", None) or None),
            "trial_expires_at": (getattr(u, "trial_expires_at", None) or None),
            "subscription_status": (getattr(u, "subscription_status", None) or None),
            "created_at": (getattr(u, "created_at", None) or None),
        }
    return {"items": [_row(u) for u in rows], "limit": limit, "offset": offset}

@router.get("/users/{tg_id}", dependencies=[Depends(require_admin)])
async def user_by_tg(
    tg_id: int,
    session: AsyncSession = Depends(get_session),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        return {"found": False}
    return {
        "found": True,
        "id": u.id,
        "tg_id": u.tg_id,
        "privacy_level": getattr(u, "privacy_level", None),
        "policy_accepted_at": getattr(u, "policy_accepted_at", None),
        "trial_started_at": getattr(u, "trial_started_at", None),
        "trial_expires_at": getattr(u, "trial_expires_at", None),
        "subscription_status": getattr(u, "subscription_status", None),
        "created_at": getattr(u, "created_at", None),
    }

@router.get("/export/users.csv", dependencies=[Depends(require_admin)])
async def export_users_csv(
    session: AsyncSession = Depends(get_session),
):
    from app.db.models import User  # type: ignore
    rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    headers = [
        "id","tg_id","privacy_level","policy_accepted_at",
        "trial_started_at","trial_expires_at","subscription_status","created_at"
    ]
    def as_csv_val(v: Any) -> str:
        if v is None: return ""
        s = str(v)
        if any(ch in s for ch in [",", ";", "\n", '"']):
            s = '"' + s.replace('"', '""') + '"'
        return s
    lines = [",".join(headers)]
    for u in rows:
        vals = [
            as_csv_val(getattr(u,"id",None)),
            as_csv_val(getattr(u,"tg_id",None)),
            as_csv_val(getattr(u,"privacy_level",None)),
            as_csv_val(getattr(u,"policy_accepted_at",None)),
            as_csv_val(getattr(u,"trial_started_at",None)),
            as_csv_val(getattr(u,"trial_expires_at",None)),
            as_csv_val(getattr(u,"subscription_status",None)),
            as_csv_val(getattr(u,"created_at",None)),
        ]
        lines.append(",".join(vals))
    csv_data = "\n".join(lines)
    return Response(content=csv_data, media_type="text/csv")
# ---------- PAYMENTS (опционально) ----------
@router.get("/payments", dependencies=[Depends(require_admin)])
async def list_payments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    # эта секция работает, только если у тебя есть модель Payment
    try:
        from app.db.models import Payment  # type: ignore
    except Exception:
        return {"items": [], "limit": limit, "offset": offset, "note": "Payment model not present"}

    stmt = select(Payment).order_by(Payment.id.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    def _row(p) -> dict:
        return {
            "id": p.id,
            "user_id": p.user_id,
            "yk_payment_id": getattr(p, "yk_payment_id", None),
            "plan": getattr(p, "plan", None),
            "amount_rub": getattr(p, "amount_rub", None),
            "status": getattr(p, "status", None),
            "created_at": getattr(p, "created_at", None),
        }
    return {"items": [_row(p) for p in rows], "limit": limit, "offset": offset}
