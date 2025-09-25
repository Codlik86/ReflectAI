# app/main.py
import asyncio
import os
from contextlib import suppress

# NEW: подхватываем .env до чтения переменных
from dotenv import load_dotenv  # NEW
load_dotenv()                   # NEW

from fastapi import FastAPI, Request, Header, Response
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update, BotCommand

from .memory_schema import (
    ensure_memory_schema_async,
    ensure_users_policy_column_async,
    ensure_users_created_at_column_async,
)
from .bot import router as bot_router

# NEW: подключаем HTTP-роуты оплаты (вебхук ЮKassa)
from app.api import payments as payments_api  # NEW
from app.legal import router as legal_router

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

# aiogram 3.x
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(bot_router)

app = FastAPI(title="ReflectAI webhook")

# NEW: регистрируем роутер оплаты (вебхук /api/payments/yookassa/webhook)
app.include_router(payments_api.router)  # NEW

app.include_router(legal_router)


from aiogram.exceptions import TelegramRetryAfter
import asyncio

async def _webhook_watchdog():
    backoff = 5
    try:
        while True:
            try:
                info = await bot.get_webhook_info()
                if info.url != WEBHOOK_URL:
                    print(f"[watchdog] webhook mismatch ('{info.url}') -> set '{WEBHOOK_URL}'")
                    await bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET, allowed_updates=[])
                    backoff = 5  # сброс бэкоффа
            except TelegramRetryAfter as e:
                wait = int(getattr(e, "retry_after", 1)) + 1
                print(f"[watchdog] retry_after {wait}s")
                await asyncio.sleep(wait)
            except Exception as e:
                print("[watchdog] error:", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

            # не давим API — спим фиксированный интервал
            await asyncio.sleep(max(5, WATCHDOG_INTERVAL_SEC))
    except asyncio.CancelledError:
        # тихо выходим на остановке приложения
        pass

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
    # 1) схему БД не создаём вручную — просто no-op (всё делает Alembic)
    await ensure_memory_schema_async()
    await ensure_users_policy_column_async()
    await ensure_users_created_at_column_async()

    # 2) чистим вебхук и ставим заново
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        ok = await bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=[],  # default набор
        )
        print(f"[startup] set_webhook: {ok} -> {WEBHOOK_URL}")
    except Exception as e:
        # важно не уронить процесс: Telegram всё равно будет пытаться доставлять
        print("[startup] set_webhook ERROR:", repr(e))

    # 3) выставляем список команд (левая панель / меню команд)
    try:
        await bot.set_my_commands([
            BotCommand(command="start",        description="▶️ Старт"),
            BotCommand(command="talk",         description="💬 Поговорить"),
            BotCommand(command="work",         description="🌿 Разобраться"),
            BotCommand(command="meditations",  description="🎧 Медитации"),
            BotCommand(command="settings",     description="⚙️ Настройки"),
            BotCommand(command="privacy",      description="🔒 Приватность (панель)"),
            BotCommand(command="policy",       description="📜 Политика и правила"),
            BotCommand(command="about",        description="ℹ️ О проекте"),
            BotCommand(command="help",         description="🆘 Помощь"),
            BotCommand(command="pay",          description="💳 Подписка"),
        ])
    except Exception as e:
        print("[startup] set_my_commands ERROR:", repr(e))

    # 4) стартуем watchdog (если уже был — не запускаем второй раз)
    if not getattr(app.state, "webhook_watchdog", None):
        app.state.webhook_watchdog = asyncio.create_task(_webhook_watchdog())
        print("[startup] webhook watchdog started")


@app.on_event("shutdown")
async def on_shutdown():
    task = getattr(app.state, "webhook_watchdog", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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
        # aiogram 3 использует pydantic v2; model_validate работает устойчиво
        update = Update.model_validate(data)
        msg_txt = None
        cb_data = None
        try:
            msg_txt = data.get("message", {}).get("text")
            cb_data = data.get("callback_query", {}).get("data")
        except Exception:
            pass
        print("[webhook] incoming:", msg_txt, "cb:", cb_data)

        await dp.feed_update(bot=bot, update=update)
    except Exception as e:
        import json, traceback
        msg = str(e)
        if "chat not found" in msg.lower():
            print("[webhook] ignore: chat not found; payload:",
                  json.dumps(data, ensure_ascii=False))
        else:
            print("[webhook] ERROR:", msg)
            print("payload:", json.dumps(data, ensure_ascii=False))
            traceback.print_exc()

    # важно: всегда 200, чтобы Телега не спамила ретраями
    return PlainTextResponse("ok")
