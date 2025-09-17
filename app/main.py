from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, Request, Response
import logging, os
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.bot import router as app_router

# diag router is optional and must be an aiogram Router()
try:
    from app.diag import router as diag_router  # may not be aiogram Router or may not exist
except Exception:
    diag_router = None

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
BASE_URL = os.getenv("WEBHOOK_BASE_URL")

ALLOWED_UPDATES = ("message", "callback_query")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(app_router)
if isinstance(diag_router, Router):
    dp.include_router(diag_router)

app = FastAPI()

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if os.getenv('BOT_DEBUG','0')=='1':
        logging.getLogger('aiogram').setLevel(logging.DEBUG)
    logging.getLogger('webhook').info('incoming update')

    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return Response(status_code=403)
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=200)

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

@app.api_route("/health", methods=["GET","HEAD"], include_in_schema=False)
async def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    # оставляем прежнее поведение GET — JSON {"status":"ok"}
    return {"status": "ok"}
