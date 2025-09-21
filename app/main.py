import os
from fastapi import FastAPI, Request, Header, Response, HTTPException
from fastapi.responses import PlainTextResponse
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update

# твои хендлеры
from .bot import router as bot_router

# === Env: читаем как на Render ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # используется как secret header

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN (или BOT_TOKEN) не задан")
if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL не задан (например, https://<app>.onrender.com)")

WEBHOOK_PATH = "/telegram/webhook"  # путь остаётся прежний
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

# aiogram 3.x
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(bot_router)

app = FastAPI(title="ReflectAI webhook")

@app.get("/")
async def root():
    return PlainTextResponse("ok")

@app.get("/health")
async def health_get():
    return PlainTextResponse("ok")

@app.head("/health")
async def health_head():
    return Response(status_code=200)

# === на старте сбрасываем и ставим вебхук с секретом ===
@app.on_event("startup")
async def on_startup():
    await bot.delete_webhook(drop_pending_updates=True)
    # secret_token -> Telegram начнёт присылать заголовок X-Telegram-Bot-Api-Secret-Token
    await bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET, allowed_updates=[])

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook(drop_pending_updates=False)

# === сам вебхук ===
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")):
    # проверяем секрет из заголовка только если он задан
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return Response(status_code=403)

    try:
        data = await request.json()
        update = Update.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad update: {e}")

    await dp.feed_update(bot, update)
    return PlainTextResponse("ok")
