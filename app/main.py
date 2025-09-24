# app/main.py
import asyncio
import os
from contextlib import suppress

from fastapi import FastAPI, Request, Header, Response
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update, BotCommand

from .memory_schema import ensure_memory_schema, ensure_users_policy_column
from .memory_schema import ensure_users_created_at_column
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

# aiogram 3.x
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(bot_router)

app = FastAPI(title="ReflectAI webhook")


async def _webhook_watchdog():
    """Периодически проверяем и чиним вебхук, если он слетел/переклеен."""
    while True:
        try:
            info = await bot.get_webhook_info()
            if info.url != WEBHOOK_URL:
                print(f"[watchdog] webhook mismatch ('{info.url}') -> set '{WEBHOOK_URL}'")
                await bot.set_webhook(
                    url=WEBHOOK_URL,
                    secret_token=WEBHOOK_SECRET or None,
                    allowed_updates=[],  # default набор
                )
        except Exception as e:
            print("[watchdog] error:", repr(e))
        await asyncio.sleep(max(5, WATCHDOG_INTERVAL_SEC))  # не давим API


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
    # 1) гарантируем схему памяти (таблицы создадутся сами, если их нет)
    ensure_memory_schema()
    ensure_users_policy_column()
    ensure_users_created_at_column()

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
            BotCommand(command="pay",          description="💳 Подписка (скоро)"),
        ])
    except Exception as e:
        print("[startup] set_my_commands ERROR:", repr(e))

    # 4) стартуем watchdog (если уже был — не запускаем второй раз)
    if not getattr(app.state, "webhook_watchdog", None):
        app.state.webhook_watchdog = asyncio.create_task(_webhook_watchdog())
        print("[startup] webhook watchdog started")


@app.on_event("shutdown")
async def on_shutdown():
    # 1) аккуратно отменяем watchdog
    task = getattr(app.state, "webhook_watchdog", None)
    if task:
        task.cancel()
        with suppress(Exception):
            await task
        app.state.webhook_watchdog = None

    # 2) вебхук можно не удалять (Телега продолжит слать), но ты явно просил чистить:
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        print("[shutdown] webhook deleted")
    except Exception as e:
        print("[shutdown] delete_webhook ERROR:", repr(e))


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
            # фейковые/чужие chat_id — не роняем сервер
            print("[webhook] ignore: chat not found; payload:",
                  json.dumps(data, ensure_ascii=False))
        else:
            print("[webhook] ERROR:", msg)
            print("payload:", json.dumps(data, ensure_ascii=False))
            traceback.print_exc()

    # важно: всегда 200, чтобы Телега не спамила ретраями
    return PlainTextResponse("ok")
