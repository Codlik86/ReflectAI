from __future__ import annotations

import os
import base64
import hmac
import hashlib
from typing import Any, Optional, cast

from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import async_session

router = APIRouter(prefix="/api/payments/yookassa", tags=["payments"])

# Переменные окружения
YK_WEBHOOK_SECRET = (os.getenv("YK_WEBHOOK_SECRET") or "").strip()   # секрет вебхука (HMAC или пароль Basic)
YK_SHOP_ID = (os.getenv("YK_SHOP_ID") or "").strip()                 # для Basic-проверки


# -----------------------------
# Вспомогательные функции
# -----------------------------

def _get(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def _as_kop(amount_str: str) -> int:
    """
    "1190.00" -> 119000 коп.
    """
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
    """
    Проверяем подлинность уведомления одним из способов (любой проходной):
    1) HMAC по «сырым» байтам тела:   заголовок X-Content-HMAC-SHA256
    2) Basic: Authorization: Basic base64(shopId:secret)

    Если YK_WEBHOOK_SECRET пуст — проверку пропускаем.
    """
    if not YK_WEBHOOK_SECRET:
        return

    # --- 1) HMAC-подпись тела (если заголовок присутствует)
    if x_hmac:
        raw = await request.body()
        digest = hmac.new(
            YK_WEBHOOK_SECRET.encode("utf-8"),
            raw,
            hashlib.sha256,
        ).hexdigest()
        # ЮKassa может прислать подпись в hex; на всякий попробуем и base64
        digest_b64 = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if hmac.compare_digest(x_hmac.strip(), digest) or hmac.compare_digest(x_hmac.strip(), digest_b64):
            return  # ok

    # --- 2) Basic авторизация
    if authorization and authorization.startswith("Basic "):
        try:
            creds = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
            login, pwd = creds.split(":", 1)
            if (not YK_SHOP_ID) or (login.strip() == YK_SHOP_ID and pwd.strip() == YK_WEBHOOK_SECRET):
                return  # ok
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="invalid webhook signature")


# -----------------------------
# Вебхук
# -----------------------------

@router.post("/webhook")
async def yookassa_webhook(
    request: Request,
    # alias — реальные имена заголовков в HTTP
    x_content_hmac_sha256: Optional[str] = Header(None, alias="X-Content-HMAC-SHA256"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    # на случай кастомного прокси — совместимость с твоим прежним именем
    x_yookassa_signature: Optional[str] = Header(None),
):
    # --- Подпись: поддерживаем оба заголовка (приоритет у X-Content-HMAC-SHA256)
    x_hmac = x_content_hmac_sha256 or x_yookassa_signature
    await _verify_webhook(request, x_hmac=x_hmac, authorization=authorization)

    # --- JSON тела
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    obj = _get(body, "object", default={})
    event = _get(body, "event", default="")

    provider_payment_id = _get(obj, "id")
    status = _get(obj, "status", default="unknown")
    amount_str = _get(obj, "amount", "value", default="0")
    currency = _get(obj, "amount", "currency", default="RUB")
    pm_id = _get(obj, "payment_method", "id")                 # может быть None
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

        # --- находим пользователя (сначала по users.id; если прислали tg_id — пробуем вторым шагом)
        u = (await session.execute(select(User).where(User.id == int(meta_user_id)))).scalar_one_or_none()
        if not u:
            try:
                u = (await session.execute(select(User).where(User.tg_id == int(meta_user_id)))).scalar_one_or_none()
            except Exception:
                u = None
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        # --- апсерт платежа по уникальному provider_payment_id
        existing = (await session.execute(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )).scalar_one_or_none()

        if existing:
            # обновляем только то, что может меняться по тому же id (статус/сырое тело/время)
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

        # --- успешный платёж: продлеваем/создаём подписку + включаем флаг у пользователя
        if status == "succeeded":
            # маппинг плана в количество дней
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
                    is_premium=premium,   # NOT NULL-safe
                    tier="basic",         # NOT NULL-safe
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

            # users.subscription_status -> active
            await session.execute(
                update(User).where(User.id == u.id).values(subscription_status="active")
            )

        # --- отменённый платёж: помечаем подписку как canceled (историю не трогаем)
        elif status in ("canceled", "cancellation_pending"):
            sub = (await session.execute(
                select(Subscription).where(Subscription.user_id == u.id)
            )).scalar_one_or_none()
            if sub:
                sub.status = "canceled"
                sub.is_premium = False
                sub.updated_at = now

        await session.commit()

    # YooKassa достаточно 200 OK без тела
    return ""
