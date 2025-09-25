# app/main.py
import os
import asyncio
from contextlib import suppress

# NEW: подхватываем .env до чтения переменных
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Header, Response
from fastapi.responses import PlainTextResponse, HTMLResponse, RedirectResponse
from markupsafe import escape

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update, BotCommand
from aiogram.exceptions import TelegramRetryAfter

from .memory_schema import (
    ensure_memory_schema_async,
    ensure_users_policy_column_async,
    ensure_users_created_at_column_async,
)
from .bot import router as bot_router

# NEW: подключаем HTTP-роуты оплаты (вебхук ЮKassa) и легальные страницы
from app.api import payments as payments_api  # NEW
from app.legal import router as legal_router   # NEW

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

# NEW: регистрируем роутеры
app.include_router(payments_api.router)  # /api/payments/yookassa/webhook
app.include_router(legal_router)         # /legal/requisites, /legal/offer

# ==== Мини-лендинг для модерации YooKassa ====

NAME = os.getenv("PROJECT_NAME", "Помни")
BOT_URL = os.getenv("PUBLIC_BOT_URL", "https://t.me/reflectttaibot")
MAIL = os.getenv("CONTACT_EMAIL", "selflect@proton.me")
OFFER = os.getenv("LEGAL_OFFER_URL", "")
POLICY = os.getenv("LEGAL_POLICY_URL", "")
INN = os.getenv("INN_SELFEMP", "")  # ИНН самозанятого

def _page(title: str, body: str) -> str:
    return f"""<!doctype html><meta charset="utf-8">
<title>{escape(title)}</title>
<meta name="robots" content="noindex,nofollow">
<style>
  body {{ font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial; max-width:720px; margin:40px auto; padding:0 16px; color:#111}}
  h1,h2 {{ margin:0 0 12px }} .muted{{color:#666}} a{{color:#136ef5; text-decoration:none}}
  .card{{border:1px solid #eee; border-radius:12px; padding:16px; margin:12px 0}}
</style>
{body}
"""

@app.get("/", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(_page(
        f"{NAME} — Telegram-помощник",
        f"""
        <h1>{escape(NAME)}</h1>
        <p class="muted">Эмоциональная поддержка и само-рефлексия в Telegram.</p>
        <div class="card">
          <p>Бот: <a href="{escape(BOT_URL)}">{escape(BOT_URL)}</a></p>
          <p>Поддержка: <a href="mailto:{escape(MAIL)}">{escape(MAIL)}</a></p>
          <p>Самозанятый, ИНН: <b>{escape(INN or "—")}</b></p>
        </div>
        <p><a href="/requisites">Реквизиты</a> · <a href="/legal/policy">Политика</a> · <a href="/legal/offer">Оферта</a></p>
        """
    ))

@app.get("/requisites", response_class=HTMLResponse)
async def requisites():
    return HTMLResponse(_page(
        "Реквизиты",
        f"""
        <h1>Реквизиты</h1>
        <div class="card">
          <p>Самозанятый (НПД).</p>
          <p>ИНН: <b>{escape(INN or "—")}</b></p>
          <p>E-mail: <a href="mailto:{escape(MAIL)}">{escape(MAIL)}</a></p>
          <p>Telegram: <a href="{escape(BOT_URL)}">{escape(BOT_URL)}</a></p>
        </div>
        """
    ))

@app.get("/legal/policy")
async def legal_policy():
    return RedirectResponse(POLICY or "/")

@app.get("/legal/offer")
async def legal_offer():
    return RedirectResponse(OFFER or "/")

# ==== /Мини-лендинг ====

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
            await asyncio.sleep(max(5, WATCHDOG_INTERVAL_SEC))
    except asyncio.CancelledError:
        pass

@app.get("/health")
async def health_get():
    return PlainTextResponse("ok")

@app.head("/health")
async def health_head():
    return Response(status_code=200)

@app.on_event("startup")
async def on_startup():
    # 1) схема БД мигрируется Alembic; здесь — доп. проверки старых таблиц (async)
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
        print("[startup] set_webhook ERROR:", repr(e))

    # 3) список команд
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
        update = Update.model_validate(data)  # pydantic v2
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
