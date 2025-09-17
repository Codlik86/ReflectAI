# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import logging
from fastapi import FastAPI, Request, Response
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update

# В твоём bot.py должен быть экспортирован router
# (в прошлой ревизии мы именно так и делали: router = Router(); ...; __all__ = ["router"])
from app.bot import router as bot_router  # type: ignore

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # опционально
BOT_DEBUG = os.getenv("BOT_DEBUG", "0") == "1"

# --- Dispatcher с твоим основным роутером
dp = Dispatcher()
dp.include_router(bot_router)

# Ленивая и кэшируемая инициализация бота
_BOT: Bot | None = None
def _ensure_bot() -> Bot:
    global _BOT
    if _BOT is None:
        if not BOT_TOKEN:
            # Явно подсветим в логах и вернём 500 в вебхуке.
            raise RuntimeError("BOT_TOKEN не задан в окружении")
        _BOT = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _BOT

# --- Health
@app.get("/health")
async def health_get():
    return {"status": "ok"}

@app.head("/health")
async def health_head():
    return Response(status_code=200)

# --- Отладочная выдача окружения (включай только временно)
if BOT_DEBUG:
    @app.get("/debug/envz")
    async def debug_env():
        return {
            "has_BOT_TOKEN": bool(BOT_TOKEN),
            "has_WEBHOOK_SECRET": bool(WEBHOOK_SECRET),
            "BOT_DEBUG": BOT_DEBUG,
        }

# --- Сам вебхук Telegram
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    # 1) Секретный заголовок (если включён)
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if got != WEBHOOK_SECRET:
            return Response(status_code=403)

    # 2) Парсим апдейт
    data = await request.json()
    if BOT_DEBUG:
        logging.getLogger("uvicorn.error").info("incoming update: %s", str(data)[:1000])

    try:
        update = Update.model_validate(data)
    except Exception:
        return Response(status_code=200)  # не валим сервис из-за мусорного апдейта

    # 3) Кормим aiogram
    try:
        bot = _ensure_bot()
    except RuntimeError as e:
        logging.getLogger("uvicorn.error").error("Webhook without BOT_TOKEN: %s", e)
        return Response(status_code=500)

    try:
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.getLogger("uvicorn.error").exception("dp.feed_update failed: %s", e)
        # Всё равно 200, чтобы телега не дудосила повторной доставкой
        return Response(status_code=200)

    return Response(status_code=200)

# Корень — 404 (нормально)
@app.get("/")
async def root():
    return Response(status_code=404)
