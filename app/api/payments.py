# app/payments.py
from __future__ import annotations

import os
import uuid
import base64
import hmac
import hashlib
from typing import Any, Optional, Literal, Dict, cast

import httpx
from fastapi import APIRouter, Request, HTTPException, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/payments", tags=["payments"])

YK_WEBHOOK_SECRET = (os.getenv("YK_WEBHOOK_SECRET") or "").strip()
YK_SHOP_ID = (os.getenv("YK_SHOP_ID") or "").strip()
YK_SECRET_KEY = (os.getenv("YK_SECRET_KEY") or "").strip()
MINIAPP_BASE = (os.getenv("MINIAPP_BASE") or "").rstrip("/")

PRICES_RUB: Dict[str, str] = {
    "week": "499.00",
    "month": "1190.00",
    "quarter": "2990.00",
    "year": "7990.00",
}

def _get(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur

def _as_kop(amount_str: str) -> int:
    s = str(amount_str).strip().replace(",", ".")
    if "." in s:
        r, c = s.split(".", 1)
        c = (c + "00")[:2]
    else:
        r, c = s, "00"
    if not r:
        r = "0"
    return int(r) * 100 + int(c)

def _utcnow():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc)

async def _verify_webhook(
    request: Request,
    *,
    x_hmac: Optional[str],
    authorization: Optional[str],
) -> None:
    if not YK_WEBHOOK_SECRET:
        return

    if x_hmac:
        raw = await request.body()
        digest = hmac.new(
            YK_WEBHOOK_SECRET.encode("utf-8"),
            raw,
            hashlib.sha256,
        ).hexdigest()
        digest_b64 = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if hmac.compare_digest(x_hmac.strip(), digest) or hmac.compare_digest(x_hmac.strip(), digest_b64):
            return

    if authorization and authorization.startswith("Basic "):
        try:
            creds = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
            login, pwd = creds.split(":", 1)
            if (not YK_SHOP_ID) or (login.strip() == YK_SHOP_ID and pwd.strip() == YK_WEBHOOK_SECRET):
                return
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="invalid webhook signature")

def _auth_header() -> str:
    if not (YK_SHOP_ID and YK_SECRET_KEY):
        return ""
    token = base64.b64encode(f"{YK_SHOP_ID}:{YK_SECRET_KEY}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"

@router.post("/yookassa/webhook")
async def yookassa_webhook(
    request: Request,
    x_content_hmac_sha256: Optional[str] = Header(None, alias="X-Content-HMAC-SHA256"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_yookassa_signature: Optional[str] = Header(None),
):
    x_hmac = x_content_hmac_sha256 or x_yookassa_signature
    await _verify_webhook(request, x_hmac=x_hmac, authorization=authorization)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    obj = _get(body, "object", default={})
    provider_payment_id = _get(obj, "id")
    status = _get(obj, "status", default="unknown")
    amount_str = _get(obj, "amount", "value", default="0")
    currency = _get(obj, "amount", "currency", default="RUB")
    pm_id = _get(obj, "payment_method", "id")
    meta_user_id = _get(obj, "metadata", "user_id")
    plan = (_get(obj, "metadata", "plan", default="") or "").lower()

    if not provider_payment_id:
        raise HTTPException(status_code=400, detail="no payment id")
    if meta_user_id is None:
        raise HTTPException(status_code=400, detail="no user_id in metadata")

    amount_kop = _as_kop(amount_str)
    now = _utcnow()

    async with async_session() as _s:
        session = cast(AsyncSession, _s)
        from app.db.models import User, Payment, Subscription  # type: ignore

        u = (await session.execute(select(User).where(User.id == int(meta_user_id)))).scalar_one_or_none()
        if not u:
            try:
                u = (await session.execute(select(User).where(User.tg_id == int(meta_user_id)))).scalar_one_or_none()
            except Exception:
                u = None
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        existing = (await session.execute(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )).scalar_one_or_none()

        if existing:
            existing.status = status
            existing.updated_at = now
            try:
                existing.raw = body  # type: ignore[assignment]
            except Exception:
                pass
        else:
            p = Payment(
                user_id=u.id,
                provider="yookassa",
                provider_payment_id=provider_payment_id,
                amount=amount_kop,
                currency=currency,
                status=status,
                is_recurring=False,
                created_at=now,
                updated_at=now,
                raw=body,  # type: ignore[arg-type]
            )
            session.add(p)

        if status == "succeeded":
            add_days = 30
            if plan in ("week", "weekly"):
                add_days = 7
            elif plan in ("quarter", "3m", "q"):
                add_days = 90
            elif plan in ("year", "annual", "y"):
                add_days = 365

            sub = (await session.execute(
                select(Subscription).where(Subscription.user_id == u.id)
            )).scalar_one_or_none()

            import datetime as dt
            base_until = getattr(sub, "subscription_until", None) if sub else None
            if base_until and base_until > now:
                new_until = base_until + dt.timedelta(days=add_days)
            else:
                new_until = now + dt.timedelta(days=add_days)

            premium = new_until > now

            if sub:
                sub.plan = plan or (sub.plan or "month")
                sub.status = "active"
                sub.is_auto_renew = True
                sub.subscription_until = new_until
                sub.is_premium = premium
                sub.tier = getattr(sub, "tier", None) or "basic"
                if pm_id:
                    sub.yk_payment_method_id = pm_id
                try:
                    sub.renewed_at = now
                    sub.expires_at = new_until
                except Exception:
                    pass
                sub.updated_at = now
            else:
                sub = Subscription(
                    user_id=u.id,
                    plan=plan or "month",
                    status="active",
                    is_auto_renew=True,
                    subscription_until=new_until,
                    is_premium=premium,
                    tier="basic",
                    yk_payment_method_id=pm_id,
                    yk_customer_id=None,
                    created_at=now,
                    updated_at=now,
                )
                try:
                    sub.renewed_at = now
                    sub.expires_at = new_until
                except Exception:
                    pass
                session.add(sub)

            await session.execute(
                update(User).where(User.id == u.id).values(subscription_status="active")
            )

        elif status in ("canceled", "cancellation_pending"):
            sub = (await session.execute(
                select(Subscription).where(Subscription.user_id == u.id)
            )).scalar_one_or_none()
            if sub:
                sub.status = "canceled"
                sub.is_premium = False
                sub.updated_at = now

        await session.commit()

    return ""

class CreatePaymentReq(BaseModel):
    user_id: int = Field(..., description="Внутренний users.id или tg_id (кладём в metadata)")
    plan: Literal["week", "month", "quarter", "year"] = "month"
    description: Optional[str] = Field(None, description="Необязательное описание в чеке")
    return_url: Optional[str] = None
    ad: Optional[str] = None

class CreatePaymentResp(BaseModel):
    payment_id: str
    confirmation_url: str

def _amount_for_plan(plan: str) -> str:
    return PRICES_RUB.get(plan, PRICES_RUB["month"])

@router.post("/yookassa/create", response_model=CreatePaymentResp)
async def create_yookassa_payment(req: CreatePaymentReq, request: Request):
    if not (YK_SHOP_ID and YK_SECRET_KEY):
        raise HTTPException(status_code=500, detail="YK credentials are not configured")

    amount = _amount_for_plan(req.plan)
    idempotence_key = str(uuid.uuid4())

    fallback_return = f"{MINIAPP_BASE}/paywall?status=ok" if MINIAPP_BASE else None
    return_url = (req.return_url or fallback_return)
    if not return_url:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        scheme = "https"
        if host:
            return_url = f"{scheme}://{host}/paywall?status=ok"
        else:
            raise HTTPException(status_code=400, detail="return_url is required and MINIAPP_BASE is not set")

    payload = {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "description": req.description or f"Помни — {req.plan}",
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": {"user_id": str(req.user_id), "plan": req.plan, "ad": (req.ad or ""), "source": "miniapp"},
    }

    headers = {"Authorization": _auth_header(), "Idempotence-Key": idempotence_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post("https://api.yookassa.ru/v3/payments", json=payload, headers=headers)

    if r.status_code not in (200, 201):
        try:
            err = r.json()
        except Exception:
            err = {"error": r.text}
        raise HTTPException(status_code=502, detail={"yookassa_error": err})

    data = r.json()
    confirmation = (data.get("confirmation") or {})
    url = confirmation.get("confirmation_url")
    payment_id = data.get("id")
    if not url or not payment_id:
        raise HTTPException(status_code=502, detail="invalid response from YooKassa")

    return CreatePaymentResp(payment_id=payment_id, confirmation_url=url)

@router.get("/yookassa/plans")
async def list_plans():
    return {"currency": "RUB", "plans": PRICES_RUB}

# ==============================
# 3) Статус доступа/подписки (для MiniApp Paywall)
# ==============================
def _iso(dtobj) -> Optional[str]:
    try:
        return dtobj.astimezone(None).isoformat()
    except Exception:
        return None

def _trial_window(started, *, days: int = 5):
    import datetime as dt
    if not started:
        return False, None
    try:
        expires = started + dt.timedelta(days=days)
        return (expires > _utcnow()), expires
    except Exception:
        return False, None

async def _load_user(session: AsyncSession, *, user_id: Optional[int], tg_user_id: Optional[int]):
    from app.db.models import User  # type: ignore
    u = None
    if user_id is not None:
        u = (await session.execute(select(User).where(User.id == int(user_id)))).scalar_one_or_none()
    if not u and tg_user_id is not None:
        u = (await session.execute(select(User).where(User.tg_id == int(tg_user_id)))).scalar_one_or_none()
    return u

@router.get("/status")
async def payments_status(
    user_id: Optional[int] = Query(None, description="users.id (внутренний id)"),
    tg_user_id: Optional[int] = Query(None, description="Telegram id (users.tg_id)"),
    start_trial: bool = Query(False, description="Авто-старт триала (delayed trial)"),
):
    """
    Универсальный статус доступа для мини-аппа.
    Возвращает:
      - has_access: bool
      - plan: str | None
      - status: "active" | "trial" | "none"
      - until: ISO8601 | None  (подписка ИЛИ триал)
      - is_auto_renew: bool | None
      - trial_started_at: ISO | None
      - trial_expires_at: ISO | None
      - needs_policy: bool
    """
    if user_id is None and tg_user_id is None:
        raise HTTPException(status_code=400, detail="provide user_id or tg_user_id")

    now = _utcnow()
    async with async_session() as _s:
        session = cast(AsyncSession, _s)
        from app.db.models import User, Subscription  # type: ignore

        u = await _load_user(session, user_id=user_id, tg_user_id=tg_user_id)
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        policy_accepted = bool(getattr(u, "policy_accepted_at", None))
        needs_policy = not policy_accepted

        sub = (await session.execute(
            select(Subscription).where(Subscription.user_id == u.id)
        )).scalar_one_or_none()

        plan = getattr(sub, "plan", None)
        sub_status = getattr(sub, "status", None)
        until = getattr(sub, "subscription_until", None)
        is_auto = getattr(sub, "is_auto_renew", None)
        has_sub_access = bool(until and sub_status == "active" and until > now)

        started = getattr(u, "trial_started_at", None)
        trial_active, trial_expires_at = _trial_window(started)

        # delayed trial: НЕ стартуем до принятия политики
        if start_trial and (not has_sub_access) and (not trial_active) and policy_accepted:
            from sqlalchemy import update as sa_update
            await session.execute(
                sa_update(User).where(User.id == u.id).values(trial_started_at=now)
            )
            await session.commit()
            started = now
            trial_active, trial_expires_at = _trial_window(started)

        has_access = bool(has_sub_access or trial_active)

        # нормализованный статус
        if has_sub_access:
            norm_status = "active"
            unified_until = until
        elif trial_active:
            norm_status = "trial"
            unified_until = trial_expires_at
        else:
            norm_status = "none"
            unified_until = None

        return {
            "has_access": has_access,
            "plan": plan,
            "status": norm_status,
            "until": _iso(unified_until) if unified_until else None,
            "is_auto_renew": bool(is_auto) if is_auto is not None else None,
            "trial_started_at": _iso(started) if started else None,
            "trial_expires_at": _iso(trial_expires_at) if trial_expires_at else None,
            "needs_policy": needs_policy,
        }

@router.get("/alias/access-status")
async def access_status_alias(
    user_id: Optional[int] = Query(None),
    tg_user_id: Optional[int] = Query(None),
    start_trial: bool = Query(False),
):
    return await payments_status(user_id=user_id, tg_user_id=tg_user_id, start_trial=start_trial)
