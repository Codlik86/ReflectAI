import os
from typing import Optional, Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/admin", tags=["admin"])

async def get_session_dep():
    async with async_session() as s:
        yield s

# ---- auth
auth = HTTPBearer()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

def require_admin(creds: HTTPAuthorizationCredentials = Depends(auth)) -> None:
    if not ADMIN_TOKEN or creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/ping", dependencies=[Depends(require_admin)])
async def ping() -> dict:
    return {"ok": True}

# ---------- USERS
@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(
    q: Optional[str] = Query(None),
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
            try:
                stmt = stmt.where(or_(User.privacy_level.ilike(f"%{q}%")))
            except Exception:
                pass

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
async def export_users_csv(session: AsyncSession = Depends(get_session_dep)):
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

# ---------- PAYMENTS
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
            "yk_payment_id": getattr(p, "provider_payment_id", None),
            "plan": getattr(p, "plan", None),  # может отсутствовать в модели — тогда будет None
            "amount_rub": getattr(p, "amount", None),
            "status": getattr(p, "status", None),
            "created_at": getattr(p, "created_at", None),
        }
    return {"items": [_row(p) for p in rows], "limit": limit, "offset": offset}

# ---------- SUBSCRIPTIONS
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

# ---------- ACTIONS (trial/subscription flags in users)
def _utcnow():
    return datetime.now(timezone.utc)

from app.billing.service import start_trial_for_user  # готовый хелпер

@router.post("/users/{tg_id}/subscription/activate", dependencies=[Depends(require_admin)])
async def admin_activate_subscription(tg_id: int, session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    await session.execute(update(User).where(User.id == u.id).values(subscription_status="active"))
    await session.commit()
    return {"ok": True, "user_id": u.id, "subscription_status": "active"}

@router.post("/users/{tg_id}/subscription/deactivate", dependencies=[Depends(require_admin)])
async def admin_deactivate_subscription(tg_id: int, session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    await session.execute(update(User).where(User.id == u.id).values(subscription_status="none"))
    await session.commit()
    return {"ok": True, "user_id": u.id, "subscription_status": "none"}

@router.post("/users/{tg_id}/trial/start", dependencies=[Depends(require_admin)])
async def admin_trial_start(tg_id: int, days: int = 5, session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    started, expires = await start_trial_for_user(session, u.id)
    if days != 5:
        await session.execute(update(User).where(User.id == u.id).values(trial_expires_at=started + timedelta(days=days)))
        expires = started + timedelta(days=days)
    await session.commit()
    return {"ok": True, "user_id": u.id, "trial_started_at": started, "trial_expires_at": expires}

@router.post("/users/{tg_id}/trial/end", dependencies=[Depends(require_admin)])
async def admin_trial_end(tg_id: int, session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import User  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    new_exp = _utcnow() - timedelta(seconds=1)
    await session.execute(update(User).where(User.id == u.id).values(trial_expires_at=new_exp))
    await session.commit()
    return {"ok": True, "user_id": u.id, "trial_expires_at": new_exp}


@router.get("/users/{tg_id}/full", dependencies=[Depends(require_admin)])
async def user_full(
    tg_id: int,
    session: AsyncSession = Depends(get_session_dep),
):
    from app.db.models import User, Payment, Subscription  # type: ignore
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    pays = (await session.execute(
        select(Payment).where(Payment.user_id == u.id).order_by(Payment.id.desc()).limit(50)
    )).scalars().all()
    subs = (await session.execute(
        select(Subscription).where(Subscription.user_id == u.id).order_by(Subscription.id.desc())
    )).scalars().all()

    def _user(u):
        return {
            "id": u.id, "tg_id": u.tg_id,
            "privacy_level": getattr(u,"privacy_level",None),
            "policy_accepted_at": getattr(u,"policy_accepted_at",None),
            "trial_started_at": getattr(u,"trial_started_at",None),
            "trial_expires_at": getattr(u,"trial_expires_at",None),
            "subscription_status": getattr(u,"subscription_status",None),
            "created_at": getattr(u,"created_at",None),
        }
    def _pay(p):
        return {
            "id": p.id, "user_id": p.user_id,
            "provider_payment_id": getattr(p,"provider_payment_id",None),
            "amount": getattr(p,"amount",None),
            "currency": getattr(p,"currency",None),
            "status": getattr(p,"status",None),
            "created_at": getattr(p,"created_at",None),
        }
    def _sub(su):
        return {
            "id": getattr(su,"id",None),
            "user_id": getattr(su,"user_id",None),
            "plan": getattr(su,"plan",None),
            "status": getattr(su,"status",None),
            "subscription_until": getattr(su,"subscription_until",None),
            "is_auto_renew": getattr(su,"is_auto_renew",None),
            "updated_at": getattr(su,"updated_at",None),
            "created_at": getattr(su,"created_at",None),
        }
    return {"user": _user(u), "payments": [_pay(p) for p in pays], "subscriptions": [_sub(x) for x in subs]}
