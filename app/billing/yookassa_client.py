import os
import uuid
import httpx
from typing import Any, Dict, Optional

YK_API_URL = "https://api.yookassa.ru/v3"

YK_SHOP_ID = os.getenv("YK_SHOP_ID", "").strip()
YK_SECRET_KEY = os.getenv("YK_SECRET_KEY", "").strip()

def _auth() -> tuple[str, str]:
    if not YK_SHOP_ID or not YK_SECRET_KEY:
        raise RuntimeError("YK_SHOP_ID / YK_SECRET_KEY are not set")
    # HTTP Basic: user=shop-id, password=secret-key
    return (YK_SHOP_ID, YK_SECRET_KEY)

def _headers(idem_key: Optional[str] = None) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if idem_key:
        h["Idempotence-Key"] = idem_key
    return h

async def create_payment(
    amount_rub: int,
    currency: str,
    description: str,
    metadata: Dict[str, Any],
    payment_method_id: Optional[str] = None,
    capture: bool = True,
    idem_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Создаёт платёж в ЮKassa.
    - amount_rub: сумма в рублях (мы конвертим в копейки)
    - payment_method_id: сохранённый способ оплаты клиента (для рекуррентных)
    """
    if amount_rub <= 0:
        raise ValueError("amount_rub must be > 0")
    if not idem_key:
        idem_key = str(uuid.uuid4())

    payload: Dict[str, Any] = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": currency or "RUB"},
        "capture": capture,
        "description": description[:127],
        "metadata": metadata or {},
        "confirmation": {"type": "redirect", "return_url": "https://example.org/ok"},  # не используется при payment_method_id
    }

    # если есть сохранённый метод – списываем без подтверждения клиента
    if payment_method_id:
        payload["payment_method_id"] = payment_method_id
        payload.pop("confirmation", None)

    async with httpx.AsyncClient(timeout=30.0, auth=_auth()) as client:
        resp = await client.post(
            f"{YK_API_URL}/payments",
            headers=_headers(idem_key),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()
