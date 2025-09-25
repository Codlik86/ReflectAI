from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.core import get_session
from app.billing.service import apply_success_payment

router = APIRouter()

@router.post("/api/payments/yookassa/webhook")
async def yk_webhook(req: Request, session: AsyncSession = Depends(get_session)):
    data = await req.json()
    event = data.get("event")
    obj = data.get("object", {}) or {}

    if event == "payment.succeeded" and obj.get("status") == "succeeded":
        meta = obj.get("metadata") or {}
        user_id = int(meta.get("user_id"))
        plan = meta.get("plan")
        pm = obj.get("payment_method") or {}
        payment_method_id = pm.get("id")
        customer = obj.get("customer") or {}
        customer_id = customer.get("id")
        await apply_success_payment(
            user_id, plan, obj["id"],
            payment_method_id, customer_id, session
        )
        return {"ok": True}

    return {"ok": True}
