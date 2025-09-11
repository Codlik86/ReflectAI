from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, Request, HTTPException, Response
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from .bot import router
from .diag import router as diag_router  # ⬅️ добавили

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
BASE_URL = os.getenv("WEBHOOK_BASE_URL")
ALLOWED_UPDATES = [x.strip() for x in os.getenv("ALLOWED_UPDATES", "message,callback_query").split(",") if x.strip()]

if not all([BOT_TOKEN, WEBHOOK_SECRET, BASE_URL]):
    raise RuntimeError("Missing env: TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, WEBHOOK_BASE_URL")

POMNI_VERSION = os.getenv("POMNI_VERSION", "pomni-v1.0")

app = FastAPI()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(router)

# Диагностические эндпоинты
app.include_router(diag_router, prefix="/diag")  # ⬅️ добавили

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.head("/health")
async def health_head():
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"ok": True}

@app.get("/ready")
async def ready():
    return {"bot": True, "webhook": True}

@app.get("/version")
async def version():
    return {"version": POMNI_VERSION}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid secret")

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(
        url=f"{BASE_URL}/telegram/webhook",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=True,
    )

@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
