# app/api/admin.py
import os
from typing import Optional, Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---- DB dependency (FastAPI-friendly) ---------------------------------------
async def get_session_dep():
    async with async_session() as s:
        yield s

# ---- auth (Bearer) ----------------------------------------------------------
auth = HTTPBearer()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

def require_admin(creds: HTTPAuthorizationCredentials = Depends(auth)) -> None:
    if not ADMIN_TOKEN or creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/ping", dependencies=[Depends(require_admin)])
async def ping() -> dict:
    return {"ok": True}

# ---------- USERS: список / поиск / карточка --------------------------------
@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(
    q: Optional[str] = Query(None, description="Поиск по tg_id/id/части полей"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session_dep),
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
            stmt = stmt.where(User.privacy_level.ilike(f"%{q}%"))

    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()

    def _row(u) -> dict:
        return {
            "id": u.id,
            "tg_id": u.tg_id,
            "privacy_level": getattr(u, "privacy_level", None),
            "policy_accepted_at": getattr(u, "policy_accepted_at", None),
            "trial_started_at": getattr(u, "trial_started_at", None),
            "trial_expires_at": getattr(u, "trial_expires_at", None),
            "subscription_status": getattr(u, "subscription_status", None),
            "created_at": getattr(u, "created_at", None),
        }

    return {"items": [_row(u) for u in rows], "limit": limit, "offset": offset}

@router.get("/users/{tg_id}", dependencies=[Depends(require_admin)])
async def user_by_tg(
    tg_id: int,
    session: AsyncSession = Depends(get_session_dep),
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
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    headers = [
        "id","tg_id","privacy_level","policy_accepted_at",
        "trial_started_at","trial_expires_at","subscription_status","created_at"
    ]

    def as_csv_val(v: Any) -> str:
        if v is None:
            return ""
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

# ---------- PAYMENTS (опционально) ------------------------------------------
@router.get("/payments", dependencies=[Depends(require_admin)])
async def list_payments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session_dep),
):
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
            "provider_payment_id": getattr(p, "provider_payment_id", None),
            "plan": getattr(p, "plan", None),
            "amount": getattr(p, "amount", None),
            "status": getattr(p, "status", None),
            "created_at": getattr(p, "created_at", None),
        }

    return {"items": [_row(p) for p in rows], "limit": limit, "offset": offset}

# ---------- ACTIONS: manage subscription & trial -----------------------------
from app.billing.service import start_trial_for_user  # используем готовый хелпер

def _utcnow():
    return datetime.now(timezone.utc)

@router.post("/users/{tg_id}/subscription/activate", dependencies=[Depends(require_admin)])
async def admin_activate_subscription(
    tg_id: int,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    setattr(u, "subscription_status", "active")
    await session.commit()
    return {"ok": True, "user_id": u.id, "subscription_status": "active"}

@router.post("/users/{tg_id}/subscription/deactivate", dependencies=[Depends(require_admin)])
async def admin_deactivate_subscription(
    tg_id: int,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    setattr(u, "subscription_status", "none")
    await session.commit()
    return {"ok": True, "user_id": u.id, "subscription_status": "none"}

@router.post("/users/{tg_id}/trial/start", dependencies=[Depends(require_admin)])
async def admin_trial_start(
    tg_id: int,
    days: int = 5,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    started, expires = await start_trial_for_user(session, u.id)
    if days != 5:
        new_exp = started + timedelta(days=days)
        setattr(u, "trial_expires_at", new_exp)
        expires = new_exp
    await session.commit()
    return {"ok": True, "user_id": u.id, "trial_started_at": started, "trial_expires_at": expires}

@router.post("/users/{tg_id}/trial/end", dependencies=[Depends(require_admin)])
async def admin_trial_end(
    tg_id: int,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    now = _utcnow()
    setattr(u, "trial_expires_at", now - timedelta(seconds=1))
    await session.commit()
    return {"ok": True, "user_id": u.id, "trial_expires_at": getattr(u, "trial_expires_at", None)}

@router.post("/users/{tg_id}/trial/extend", dependencies=[Depends(require_admin)])
async def admin_trial_extend(
    tg_id: int,
    days: int = 1,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    now = _utcnow()
    cur_exp = getattr(u, "trial_expires_at", None)
    base = cur_exp if cur_exp and cur_exp > now else now
    new_exp = base + timedelta(days=days)
    setattr(u, "trial_expires_at", new_exp)
    await session.commit()
    return {"ok": True, "user_id": u.id, "trial_expires_at": new_exp}

# === SUBSCRIPTIONS: list & csv =================================================

from fastapi import Path

@router.get("/subscriptions", dependencies=[Depends(require_admin)])
async def list_subscriptions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session_dep),
):
    try:
        from app.db.models import Subscription  # type: ignore
    except Exception:
        return {"items": [], "limit": limit, "offset": offset, "note": "Subscription model not present"}

    stmt = select(Subscription).order_by(Subscription.id.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    def _row(s) -> dict:
        return {
            "id": getattr(s, "id", None),
            "user_id": getattr(s, "user_id", None),
            "plan": getattr(s, "plan", None),
            "status": getattr(s, "status", None),
            "is_auto_renew": getattr(s, "is_auto_renew", None),
            "subscription_until": getattr(s, "subscription_until", None),
            "yk_payment_method_id": getattr(s, "yk_payment_method_id", None),
            "yk_customer_id": getattr(s, "yk_customer_id", None),
            "created_at": getattr(s, "created_at", None),
            "updated_at": getattr(s, "updated_at", None),
        }
    return {"items": [_row(s) for s in rows], "limit": limit, "offset": offset}


@router.get("/export/subscriptions.csv", dependencies=[Depends(require_admin)])
async def export_subscriptions_csv(
    session: AsyncSession = Depends(get_session_dep),
):
    try:
        from app.db.models import Subscription  # type: ignore
    except Exception:
        return Response(content="model missing", media_type="text/plain", status_code=500)

    rows = (await session.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    headers = [
        "id","user_id","plan","status","is_auto_renew","subscription_until",
        "yk_payment_method_id","yk_customer_id","created_at","updated_at"
    ]
    def as_csv_val(v):
        if v is None: return ""
        s = str(v)
        if any(ch in s for ch in [",",";","\n",'"']):
            s = '"' + s.replace('"','""') + '"'
        return s
    lines = [",".join(headers)]
    for s in rows:
        vals = [as_csv_val(getattr(s,k,None)) for k in [
            "id","user_id","plan","status","is_auto_renew","subscription_until",
            "yk_payment_method_id","yk_customer_id","created_at","updated_at"
        ]]
        lines.append(",".join(vals))
    return Response(content="\n".join(lines), media_type="text/csv")


# === USERS FULL CARD: user + payments + subscriptions =========================

@router.get("/users/{tg_id}/full", dependencies=[Depends(require_admin)])
async def user_full(
    tg_id: int = Path(..., description="Telegram user id"),
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    # payments
    try:
        from app.db.models import Payment  # type: ignore
        pay_rows = (await session.execute(
            select(Payment).where(Payment.user_id == u.id).order_by(Payment.id.desc())
        )).scalars().all()
        payments = [{
            "id": p.id,
            "provider": p.provider,
            "provider_payment_id": getattr(p,"provider_payment_id",None),
            "amount": p.amount,
            "currency": p.currency,
            "status": p.status,
            "is_recurring": p.is_recurring,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        } for p in pay_rows]
    except Exception:
        payments = []

    # subscriptions
    try:
        from app.db.models import Subscription  # type: ignore
        sub_rows = (await session.execute(
            select(Subscription).where(Subscription.user_id == u.id).order_by(Subscription.id.desc())
        )).scalars().all()
        subs = [{
            "id": s.id,
            "plan": s.plan,
            "status": s.status,
            "is_auto_renew": s.is_auto_renew,
            "subscription_until": s.subscription_until,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        } for s in sub_rows]
    except Exception:
        subs = []

    return {
        "user": {
            "id": u.id,
            "tg_id": u.tg_id,
            "privacy_level": getattr(u, "privacy_level", None),
            "policy_accepted_at": getattr(u, "policy_accepted_at", None),
            "trial_started_at": getattr(u, "trial_started_at", None),
            "trial_expires_at": getattr(u, "trial_expires_at", None),
            "trial_until": getattr(u, "trial_until", None),
            "subscription_status": getattr(u, "subscription_status", None),
            "created_at": getattr(u, "created_at", None),
        },
        "payments": payments,
        "subscriptions": subs,
    }

@router.get("/users/{tg_id}/full", dependencies=[Depends(require_admin)])
async def user_full(
    tg_id: int,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User, Payment, Subscription  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Not Found")

    # payments
    pays = (await session.execute(
        select(Payment).where(Payment.user_id == u.id).order_by(Payment.id.desc())
    )).scalars().all()

    # subscription (по user_id один рядок; если больше — возьмём активный/последний)
    subs = (await session.execute(
        select(Subscription).where(Subscription.user_id == u.id).order_by(Subscription.id.desc())
    )).scalars().all()

    def p_row(p):
        return {
            "id": p.id,
            "provider": p.provider,
            "provider_payment_id": p.provider_payment_id,
            "amount": p.amount,
            "currency": p.currency,
            "status": p.status,
            "is_recurring": p.is_recurring,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }

    def s_row(s):
        return {
            "id": s.id,
            "plan": s.plan,
            "status": s.status,
            "is_auto_renew": s.is_auto_renew,
            "subscription_until": s.subscription_until,
            "yk_payment_method_id": s.yk_payment_method_id,
            "yk_customer_id": s.yk_customer_id,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }

    user = {
        "id": u.id,
        "tg_id": u.tg_id,
        "privacy_level": getattr(u, "privacy_level", None),
        "policy_accepted_at": getattr(u, "policy_accepted_at", None),
        "trial_started_at": getattr(u, "trial_started_at", None),
        "trial_expires_at": getattr(u, "trial_expires_at", None),
        "subscription_status": getattr(u, "subscription_status", None),
        "created_at": getattr(u, "created_at", None),
    }

    return {"user": user, "payments": [p_row(p) for p in pays], "subscriptions": [s_row(s) for s in subs]}
