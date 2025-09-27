# app/api/payments.py
from fastapi import APIRouter, Request, Response, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.core import get_session
from app.billing.service import handle_yookassa_webhook

router = APIRouter(prefix="/api/payments", tags=["payments"])

@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    body = await request.json()
    # необязательно, но полезно в логах
    obj = body.get("object", {})
    print(f"[YooKassa] webhook: status={obj.get('status')} id={obj.get('id')} meta={obj.get('metadata')}")
    try:
        await handle_yookassa_webhook(session, body)
        await session.commit()
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        await session.rollback()
        print(f"[YooKassa] webhook error: {e}")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
