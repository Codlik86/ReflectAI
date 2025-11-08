import hmac, hashlib, json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

class VerifyIn(BaseModel):
    init_data: str  # window.Telegram.WebApp.initData

def _check_signature(init_data: str, bot_token: str) -> bool:
    # по документации Telegram Web Apps
    try:
        parsed = dict([p.split('=') for p in init_data.split('&')])
        hash_str = parsed.pop('hash', None)
        data_check_string = '\n'.join([f"{k}={parsed[k]}" for k in sorted(parsed)])
        secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(calc_hash, hash_str)
    except Exception:
        return False

@router.post("/verify")
def verify_webapp(data: VerifyIn):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        raise HTTPException(500, "Bot token not configured")

    ok = _check_signature(data.init_data, bot_token)
    if not ok:
        raise HTTPException(401, "Invalid init data")
    return {"ok": True}
