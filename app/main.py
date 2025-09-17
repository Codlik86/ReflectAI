
import os
from fastapi import FastAPI, Request, Header, Response
from fastapi.responses import PlainTextResponse
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
import asyncio

# Import router
try:
    from app.bot import router as bot_router
except Exception:
    from bot import router as bot_router

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
assert BOT_TOKEN, "BOT_TOKEN must be set"

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()
dp.include_router(bot_router)

app = FastAPI()

@app.get("/health")
async def health_get():
    return PlainTextResponse("ok")

@app.head("/health")
async def health_head():
    return Response(status_code=200)

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")):
    # Optional secret verification
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return Response(status_code=403)

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return PlainTextResponse("ok")
