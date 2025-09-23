# app/main.py
import asyncio
import os
from fastapi import FastAPI, Request, Header, Response, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update, BotCommand

# твои хендлеры
from .bot import router as bot_router

# --- env (строго единые имена) ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL не задан (например, https://<app>.onrender.com)")

WEBHOOK_PATH = "/telegram/webhook"  # путь остаётся прежний
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

# === Watchdog: авто-восстановление вебхука, если он пуст/чужой ===
WATCHDOG_INTERVAL_SEC = int(os.getenv("WEBHOOK_WATCHDOG_SEC", "60"))

async def _webhook_watchdog():
    while True:
        try:
            info = await bot.get_webhook_info()
            if info.url != WEBHOOK_URL:
                print(f"[watchdog] webhook mismatch ('{info.url}') -> set '{WEBHOOK_URL}'")
                await bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET, allowed_updates=[])
        except Exception as e:
            print("[watchdog] error:", e)
        await asyncio.sleep(WATCHDOG_INTERVAL_SEC)

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

@app.on_event("startup")
async def on_startup():
    # чистим вебхук и ставим заново
    await bot.delete_webhook(drop_pending_updates=True)
    ok = await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=[],
    )
    print(f"set_webhook: {ok} -> {WEBHOOK_URL}")

    # выставляем список команд (левая панель / меню команд)
    await bot.set_my_commands([
        BotCommand(command="start",        description="Старт"),
        BotCommand(command="menu",         description="Меню"),
        BotCommand(command="talk",         description="Поговорить"),
        BotCommand(command="work",         description="Разобраться"),
        BotCommand(command="meditations",  description="Медитации"),
        BotCommand(command="settings",     description="Настройки"),
        BotCommand(command="privacy",      description="Приватность (панель)"),
        BotCommand(command="policy",       description="Политика и правила"),
        BotCommand(command="about",        description="О проекте"),
        BotCommand(command="help",         description="Помощь"),
        BotCommand(command="pay",          description="Подписка (скоро)"),
    ])

    # стартуем watchdog
    app.state.webhook_watchdog = asyncio.create_task(_webhook_watchdog())

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook(drop_pending_updates=False)
    task = getattr(app.state, "webhook_watchdog", None)
    if task:
        task.cancel()

# === сам вебхук (безопасный) ===
@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default="")
):
    # 1) секрет из заголовка (если задан)
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return Response(status_code=403)

    # 2) читаем JSON
    data = await request.json()

    # 3) пробуем обработать; любые сбои логируем и всё равно возвращаем 200
    try:
        update = Update.model_validate(data)
        print("[webhook] incoming:", data.get("message", {}).get("text"), "cb:", data.get("callback_query", {}).get("data"))
        await dp.feed_update(bot=bot, update=update)
    except Exception as e:
        import json, traceback
        msg = str(e)
        if "chat not found" in msg.lower():
            # фейковые/чужие chat_id — не роняем сервер
            print("[webhook] ignore: chat not found; payload:", json.dumps(data, ensure_ascii=False))
        else:
            print("[webhook] ERROR:", msg)
            print("payload:", json.dumps(data, ensure_ascii=False))
            traceback.print_exc()

    # важно: всегда 200, чтобы Телега не спамила ретраями
    return PlainTextResponse("ok")
