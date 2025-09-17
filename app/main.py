# -*- coding: utf-8 -*-
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
import logging
from typing import Optional

from fastapi import FastAPI, Request, Response
from aiogram import Dispatcher, Router
from aiogram.types import Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram import Bot

# --- Env
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL") or os.getenv("WEBHOOK_URL", "").replace("/telegram/webhook", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or os.getenv("TELEGRAM_WEBHOOK_SECRET")
ALLOWED_UPDATES = ("message", "callback_query")

# --- Aiogram dispatcher (бот создаём лениво)
dp = Dispatcher()
_bot: Optional[Bot] = None

def _ensure_bot() -> Bot:
    """Ленивая инициализация бота (без токена — понятное исключение)."""
    global _bot
    if _bot is None:
        if not BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN не задан в окружении")
        _bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _bot

# основной роутер приложения
from app.bot import router as app_router  # noqa: E402

# опциональный диагностика-роутер
diag_router: Optional[Router] = None
try:
    from app.diag import router as diag_router  # type: ignore
except Exception:
    diag_router = None

# fallback (ping + ответ на любой текст)
fallback_router: Optional[Router] = None
try:
    from app.fallback import router as fallback_router  # type: ignore
except Exception:
    fallback_router = None

# порядок важен: fallback — последним
dp.include_router(app_router)
if isinstance(diag_router, Router):
    dp.include_router(diag_router)
if isinstance(fallback_router, Router):
    dp.include_router(fallback_router)

# --- FastAPI
app = FastAPI()

@app.get("/health")
async def health_get():
    return {"status": "ok"}

@app.head("/health")
async def health_head():
    return Response(status_code=200)

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if os.getenv("BOT_DEBUG","0") == "1":
        logging.getLogger("aiogram").setLevel(logging.DEBUG)
    logging.getLogger("webhook").info("incoming update")

    # Верифицируем секрет (если задан)
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if got != WEBHOOK_SECRET:
            return Response(status_code=403)

    data = await request.body()
    try:
        update = Update.model_validate_json(data)
    except Exception:
        update = Update.model_validate(await request.json())

    bot = _ensure_bot()
    await dp.feed_update(bot, update)
    return Response(status_code=200)

@app.on_event("startup")
async def on_startup():
    # Пытаемся выставить вебхук, только если есть URL и токен
    if WEBHOOK_BASE_URL and BOT_TOKEN:
        try:
            bot = _ensure_bot()
            await bot.set_webhook(
                url=f"{WEBHOOK_BASE_URL}/telegram/webhook",
                secret_token=WEBHOOK_SECRET,
                allowed_updates=ALLOWED_UPDATES,
                drop_pending_updates=True,
            )
        except Exception as e:
            logging.getLogger("startup").warning("set_webhook failed: %r", e)

@app.on_event("shutdown")
async def on_shutdown():
    if _bot:
        await _bot.session.close()
