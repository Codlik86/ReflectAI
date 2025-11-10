# app/api/telegram_webapp.py
from __future__ import annotations

import os
import hmac
import hashlib
import json
import time
from typing import Any, Dict, Tuple, Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from urllib.parse import parse_qsl

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# --- конфиг (можно переопределить через ENV)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "") or os.getenv("BOT_TOKEN", "")
# доп. защита: сколько секунд считаем initData «свежим» (0 = не проверять)
INITDATA_MAX_AGE = int(os.getenv("TG_INITDATA_MAX_AGE_SEC", "0"))  # напр., 86400 (1 день)


class VerifyIn(BaseModel):
    init_data: str  # window.Telegram.WebApp.initData (сырая строка)


def _secret_key_from_bot_token(bot_token: str) -> bytes:
    # секретный ключ = HMAC_SHA256(key="WebAppData", msg=bot_token)
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def _parse_init_data(init_data: str) -> Tuple[Dict[str, str], Optional[str]]:
    """
    Корректный парсинг query-строки initData c URL-декодом.
    Возвращает: (поля_кроме_hash, hash).
    """
    items = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    hash_value = items.pop("hash", None)
    return items, hash_value


def _build_data_check_string(fields: Dict[str, str]) -> str:
    # сортировка по ключам, склейка "key=value" через \n
    parts = [f"{k}={fields[k]}" for k in sorted(fields.keys())]
    return "\n".join(parts)


def _check_signature(init_data: str, bot_token: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Валидирует подпись initData и, если ок, возвращает (True, parsed_payload).
    parsed_payload включает: все поля initData (без hash) и, если есть, user (dict).
    """
    try:
        fields, got_hash = _parse_init_data(init_data)
        if not got_hash:
            return False, {}

        dcs = _build_data_check_string(fields)
        secret = _secret_key_from_bot_token(bot_token)
        calc_hex = hmac.new(secret, dcs.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc_hex, got_hash):
            return False, {}

        # необязательная проверка свежести auth_date
        if INITDATA_MAX_AGE > 0:
            auth_date = fields.get("auth_date")
            if not auth_date or not auth_date.isdigit():
                return False, {}
            ts = int(auth_date)
            if (time.time() - ts) > INITDATA_MAX_AGE:
                return False, {}

        # распарсим user, если он есть (в initData он JSON-строкой)
        payload: Dict[str, Any] = dict(fields)
        user_raw = fields.get("user")
        if user_raw:
            try:
                payload["user"] = json.loads(user_raw)
            except Exception:
                # оставим как есть, если вдруг не JSON
                payload["user"] = user_raw

        return True, payload
    except Exception:
        return False, {}


@router.post("/verify")
def verify_webapp(body: VerifyIn):
    bot_token = BOT_TOKEN
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")

    ok, payload = _check_signature(body.init_data, bot_token)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid init data")

    return {
        "ok": True,
        "user": payload.get("user"),
        "auth_date": payload.get("auth_date"),
    }


# --- (опционально) верификация через заголовок, если фронт шлёт X-Telegram-Init-Data
@router.post("/verify-header")
def verify_webapp_header(x_init_data: str = Header("", alias="X-Telegram-Init-Data")):
    bot_token = BOT_TOKEN
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")
    if not x_init_data:
        raise HTTPException(status_code=400, detail="Missing X-Telegram-Init-Data")

    ok, payload = _check_signature(x_init_data, bot_token)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid init data")

    return {
        "ok": True,
        "user": payload.get("user"),
        "auth_date": payload.get("auth_date"),
    }
