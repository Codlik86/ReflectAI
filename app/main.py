from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, Request, Response
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.bot import router
from app.diag import router as diag_router

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
BASE_URL = os.getenv("WEBHOOK_BASE_URL")

# обязательно: и message, и callback_query
ALLOWED_UPDATES = ("message", "callback_query")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(router)
dp.include_router(diag_router)

app = FastAPI()

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    # защитный заголовок
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return Response(status_code=403)
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=200)

@app.get("/health")
async def health():
    return {"status": "ok"}

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
