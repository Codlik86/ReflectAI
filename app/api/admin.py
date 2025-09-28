# app/api/admin.py
import os
from typing import Optional, Any
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    Request,
    Body,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, or_, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Common helpers / auth
# ---------------------------------------------------------------------------

async def get_session_dep():
    async with async_session() as s:
        yield s

auth = HTTPBearer()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

def require_admin(creds: HTTPAuthorizationCredentials = Depends(auth)) -> None:
    if not ADMIN_TOKEN or creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

PLAN_TO_DAYS: dict[str, int] = {
    "week": 7,
    "weekly": 7,
    "month": 30,
    "monthly": 30,
    "quarter": 90,
    "3m": 90,
    "q": 90,
    "year": 365,
    "annual": 365,
    "y": 365,
}
def _plan_days(plan: Optional[str]) -> int:
    if not plan:
        return 30
    return PLAN_TO_DAYS.get(plan.lower(), 30)

@router.get("/ping", dependencies=[Depends(require_admin)])
async def ping() -> dict:
    return {"ok": True}

# ---------------------------------------------------------------------------
# USERS
# ---------------------------------------------------------------------------

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
                stmt = stmt.where(User.privacy_level.ilike(f"%{q}%"))
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

# CSV экспорт пользователей
@router.get("/export/users.csv", dependencies=[Depends(require_admin)])
async def export_users_csv(session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import User  # type: ignore
    rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    headers = [
        "id", "tg_id", "privacy_level", "policy_accepted_at",
        "trial_started_at", "trial_expires_at", "subscription_status", "created_at",
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
            as_csv_val(getattr(u, "id", None)),
            as_csv_val(getattr(u, "tg_id", None)),
            as_csv_val(getattr(u, "privacy_level", None)),
            as_csv_val(getattr(u, "policy_accepted_at", None)),
            as_csv_val(getattr(u, "trial_started_at", None)),
            as_csv_val(getattr(u, "trial_expires_at", None)),
            as_csv_val(getattr(u, "subscription_status", None)),
            as_csv_val(getattr(u, "created_at", None)),
        ]
        lines.append(",".join(vals))
    csv_data = "\n".join(lines)
    return Response(content=csv_data, media_type="text/csv")

@router.get("/export/payments.csv", dependencies=[Depends(require_admin)])
async def export_payments_csv(session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import Payment  # type: ignore
    rows = (await session.execute(select(Payment).order_by(Payment.id))).scalars().all()
    headers = ["id","user_id","provider_payment_id","amount","currency","status","created_at"]
    def val(x): 
        s = "" if x is None else str(x)
        return '"' + s.replace('"','""') + '"' if any(c in s for c in [",",";","\n",'"']) else s
    lines = [",".join(headers)]
    for p in rows:
        lines.append(",".join([
            val(getattr(p,"id",None)),
            val(getattr(p,"user_id",None)),
            val(getattr(p,"provider_payment_id",None)),
            val(getattr(p,"amount",None)),
            val(getattr(p,"currency",None)),
            val(getattr(p,"status",None)),
            val(getattr(p,"created_at",None)),
        ]))
    return Response("\n".join(lines), media_type="text/csv")


@router.get("/export/subscriptions.csv", dependencies=[Depends(require_admin)])
async def export_subscriptions_csv(session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import Subscription  # type: ignore
    rows = (await session.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    headers = [
        "id","user_id","plan","status","is_auto_renew","subscription_until",
        "yk_payment_method_id","yk_customer_id","created_at","updated_at"
    ]
    def val(x): 
        s = "" if x is None else str(x)
        return '"' + s.replace('"','""') + '"' if any(c in s for c in [",",";","\n",'"']) else s
    lines = [",".join(headers)]
    for s in rows:
        lines.append(",".join([
            val(getattr(s,"id",None)),
            val(getattr(s,"user_id",None)),
            val(getattr(s,"plan",None)),
            val(getattr(s,"status",None)),
            val(getattr(s,"is_auto_renew",None)),
            val(getattr(s,"subscription_until",None)),
            val(getattr(s,"yk_payment_method_id",None)),
            val(getattr(s,"yk_customer_id",None)),
            val(getattr(s,"created_at",None)),
            val(getattr(s,"updated_at",None)),
        ]))
    return Response("\n".join(lines), media_type="text/csv")

# ---------------------------------------------------------------------------
# PAYMENTS
# ---------------------------------------------------------------------------

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
            "plan": getattr(p, "plan", None),
            "amount_rub": getattr(p, "amount", None),
            "status": getattr(p, "status", None),
            "created_at": getattr(p, "created_at", None),
        }

    return {"items": [_row(p) for p in rows], "limit": limit, "offset": offset}

# CSV экспорт платежей
@router.get("/export/payments.csv", dependencies=[Depends(require_admin)])
async def export_payments_csv(session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import Payment  # type: ignore
    rows = (await session.execute(select(Payment).order_by(Payment.id))).scalars().all()
    headers = ["id", "user_id", "provider_payment_id", "amount", "currency", "status", "created_at"]

    def as_csv_val(v: Any) -> str:
        if v is None:
            return ""
        s = str(v)
        if any(ch in s for ch in [",", ";", "\n", '"']):
            s = '"' + s.replace('"', '""') + '"'
        return s

    lines = [",".join(headers)]
    for p in rows:
        vals = [
            as_csv_val(getattr(p, "id", None)),
            as_csv_val(getattr(p, "user_id", None)),
            as_csv_val(getattr(p, "provider_payment_id", None)),
            as_csv_val(getattr(p, "amount", None)),
            as_csv_val(getattr(p, "currency", None)),
            as_csv_val(getattr(p, "status", None)),
            as_csv_val(getattr(p, "created_at", None)),
        ]
        lines.append(",".join(vals))
    csv_data = "\n".join(lines)
    return Response(content=csv_data, media_type="text/csv")

# ---------------------------------------------------------------------------
# SUBSCRIPTIONS
# ---------------------------------------------------------------------------

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

# CSV экспорт подписок
@router.get("/export/subscriptions.csv", dependencies=[Depends(require_admin)])
async def export_subscriptions_csv(session: AsyncSession = Depends(get_session_dep)):
    from app.db.models import Subscription  # type: ignore
    rows = (await session.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    headers = [
        "id", "user_id", "plan", "status", "is_auto_renew", "subscription_until",
        "yk_payment_method_id", "yk_customer_id", "created_at", "updated_at",
    ]

    def as_csv_val(v: Any) -> str:
        if v is None:
            return ""
        s = str(v)
        if any(ch in s for ch in [",", ";", "\n", '"']):
            s = '"' + s.replace('"', '""') + '"'
        return s

    lines = [",".join(headers)]
    for s in rows:
        vals = [
            as_csv_val(getattr(s, "id", None)),
            as_csv_val(getattr(s, "user_id", None)),
            as_csv_val(getattr(s, "plan", None)),
            as_csv_val(getattr(s, "status", None)),
            as_csv_val(getattr(s, "is_auto_renew", None)),
            as_csv_val(getattr(s, "subscription_until", None)),
            as_csv_val(getattr(s, "yk_payment_method_id", None)),
            as_csv_val(getattr(s, "yk_customer_id", None)),
            as_csv_val(getattr(s, "created_at", None)),
            as_csv_val(getattr(s, "updated_at", None)),
        ]
        lines.append(",".join(vals))
    csv_data = "\n".join(lines)
    return Response(content=csv_data, media_type="text/csv")

# --- массовая отметка просроченных ---
@router.post("/subscriptions/expire", dependencies=[Depends(require_admin)])
async def expire_subscriptions(_: Request, session: AsyncSession = Depends(get_session_dep)):
    now = _utcnow()
    # raw SQL проще и надёжнее для массового апдейта
    stmt = text("""
        UPDATE subscriptions
           SET status = 'expired',
               is_auto_renew = FALSE,
               updated_at = CURRENT_TIMESTAMP
         WHERE status = 'active'
           AND subscription_until IS NOT NULL
           AND subscription_until < :now
    """)
    await session.execute(stmt, {"now": now})
    await session.commit()
    return {"ok": True, "ran_at": now.isoformat()}

# --- отмена подписки конкретного пользователя ---
@router.post("/subscriptions/cancel", dependencies=[Depends(require_admin)])
async def cancel_subscription(
    _: Request,
    session: AsyncSession = Depends(get_session_dep),
    user_id: int = Body(..., embed=True),
):
    from app.db.models import Subscription  # type: ignore
    now = _utcnow()
    sub = (await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="subscription not found")
    sub.status = "canceled"
    sub.is_auto_renew = False
    sub.updated_at = now
    await session.commit()
    return {"ok": True, "user_id": user_id, "status": "canceled"}

# --- реактивация/продление подписки ---
@router.post("/subscriptions/reactivate", dependencies=[Depends(require_admin)])
async def reactivate_subscription(
    _: Request,
    session: AsyncSession = Depends(get_session_dep),
    user_id: int = Body(..., embed=True),
    plan: Optional[str] = Body(None, embed=True),
):
    from app.db.models import Subscription  # type: ignore
    now = _utcnow()
    add_days = _plan_days(plan)

    sub = (await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )).scalar_one_or_none()

    if not sub:
        # создать «с нуля»
        until = now + timedelta(days=add_days)
        sub = Subscription(
            user_id=user_id,
            plan=(plan or "month"),
            status="active",
            is_auto_renew=True,
            subscription_until=until,
            created_at=now,
            updated_at=now,
        )
        session.add(sub)
    else:
        base = sub.subscription_until or now
        if base < now:
            base = now
        sub.plan = plan or (sub.plan or "month")
        sub.status = "active"
        sub.is_auto_renew = True
        sub.subscription_until = base + timedelta(days=add_days)
        sub.updated_at = now

    await session.commit()
    return {"ok": True, "user_id": user_id, "plan": (plan or sub.plan)}

# ---------------------------------------------------------------------------
# TRIAL / subscription flags на users
# ---------------------------------------------------------------------------

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
        new_expires = started + timedelta(days=days)
        await session.execute(update(User).where(User.id == u.id).values(trial_expires_at=new_expires))
        expires = new_expires
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

# ---------------------------------------------------------------------------
# Полный срез по пользователю (user + payments + subscriptions)
# ---------------------------------------------------------------------------

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
            "privacy_level": getattr(u, "privacy_level", None),
            "policy_accepted_at": getattr(u, "policy_accepted_at", None),
            "trial_started_at": getattr(u, "trial_started_at", None),
            "trial_expires_at": getattr(u, "trial_expires_at", None),
            "subscription_status": getattr(u, "subscription_status", None),
            "created_at": getattr(u, "created_at", None),
        }

    def _pay(p):
        return {
            "id": p.id, "user_id": p.user_id,
            "provider_payment_id": getattr(p, "provider_payment_id", None),
            "amount": getattr(p, "amount", None),
            "currency": getattr(p, "currency", None),
            "status": getattr(p, "status", None),
            "created_at": getattr(p, "created_at", None),
        }

    def _sub(su):
        return {
            "id": getattr(su, "id", None),
            "user_id": getattr(su, "user_id", None),
            "plan": getattr(su, "plan", None),
            "status": getattr(su, "status", None),
            "subscription_until": getattr(su, "subscription_until", None),
            "is_auto_renew": getattr(su, "is_auto_renew", None),
            "updated_at": getattr(su, "updated_at", None),
            "created_at": getattr(su, "created_at", None),
        }

    return {"user": _user(u), "payments": [_pay(p) for p in pays], "subscriptions": [_sub(x) for x in subs]}
