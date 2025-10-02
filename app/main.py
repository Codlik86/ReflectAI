# app/main.py
import os
import asyncio
from contextlib import suppress

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

# схемы/миграции
from .memory_schema import (
    ensure_memory_schema_async,
    ensure_users_policy_column_async,
    ensure_users_created_at_column_async,
)

# основной бот-роутер
from .bot import router as bot_router

# внешние/внутренние API-роутеры
from app.legal import router as legal_router               # /requisites, /legal/*
from app.api import payments as payments_api               # /api/payments/yookassa/webhook
from app.api import admin as admin_api                     # /api/admin/*

# админка (HTML)
from app.site.admin_ui import router as admin_ui_router    # /admin

# НОВОЕ: API для саммари и maintenance
from app.site.summaries_api import router as summaries_router      # /api/summaries/*
from app.maintenance import router as maintenance_router           # /api/admin/maintenance/summarize

# --- ENV ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL не задан (например, https://<app>.onrender.com)")

WEBHOOK_PATH = "/telegram/webhook"
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

WATCHDOG_INTERVAL_SEC = int(os.getenv("WEBHOOK_WATCHDOG_SEC", "60"))

# aiogram 3.x
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(bot_router)

app = FastAPI(title="ReflectAI webhook")

# --------- Роутеры FastAPI ----------
app.include_router(legal_router)                        # /requisites, /legal/*
app.include_router(payments_api.router, prefix="")      # /api/payments/yookassa/webhook
app.include_router(admin_api.router,    prefix="")      # /api/admin/*
app.include_router(admin_ui_router)                     # /admin (HTML)

# НОВОЕ: подключаем API для саммари и maintenance
app.include_router(summaries_router,       prefix="/api")                    # /api/summaries/*
app.include_router(maintenance_router,     prefix="/api/admin/maintenance")  # /api/admin/maintenance/summarize
# -------------------------------------

# ==== Мини-лендинг для модерации YooKassa ====

PROJECT_NAME = os.getenv("PROJECT_NAME", "Помни")
PUBLIC_BOT_URL = os.getenv("PUBLIC_BOT_URL", "https://t.me/reflectttaibot")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "selflect@proton.me")
LEGAL_OFFER_URL = os.getenv("LEGAL_OFFER_URL", "")
LEGAL_POLICY_URL = os.getenv("LEGAL_POLICY_URL", "")
INN_SELFEMP = os.getenv("INN_SELFEMP", "")
OWNER_FULL_NAME = os.getenv("OWNER_FULL_NAME", "")

def _page(title: str, body: str) -> str:
    return f"""<!doctype html><meta charset="utf-8">
<title>{escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<style>
  body {{ font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial; max-width:740px; margin:40px auto; padding:0 16px; color:#111}}
  h1,h2 {{ margin:0 0 12px }} .muted{{color:#666}} a{{color:#136ef5; text-decoration:none}}
  .card{{border:1px solid #eee; border-radius:12px; padding:16px; margin:12px 0}}
  .label{{color:#666}}
</style>
{body}
"""

@app.get("/", response_class=HTMLResponse)
async def landing():
    fio = OWNER_FULL_NAME or "—"
    inn = INN_SELFEMP or "—"
    return HTMLResponse(_page(
        f"{PROJECT_NAME} — Telegram-помощник",
        f"""
        <h1>{escape(PROJECT_NAME)}</h1>
        <p class="muted">Эмоциональная поддержка и само-рефлексия в Telegram.</p>
        <div class="card">
          <p><span class="label">Бот:</span> <a href="{escape(PUBLIC_BOT_URL)}">{escape(PUBLIC_BOT_URL)}</a></p>
          <p><span class="label">Поддержка:</span> <a href="mailto:{escape(CONTACT_EMAIL)}">{escape(CONTACT_EMAIL)}</a></p>
          <p><span class="label">Самозанятый:</span> <b>{escape(fio)}</b></p>
          <p><span class="label">ИНН:</span> <b>{escape(inn)}</b></p>
        </div>
        <p>
          <a href="/requisites">Реквизиты</a>
          · <a href="/legal/policy">Правила и Политика</a>
          · <a href="/legal/offer">Оферта</a>
        </p>
        """
    ))

@app.get("/requisites", response_class=HTMLResponse)
async def requisites():
    fio = OWNER_FULL_NAME or "—"
    inn = INN_SELFEMP or "—"
    return HTMLResponse(_page(
        "Реквизиты",
        f"""
        <h1>Реквизиты</h1>
        <div class="card">
          <p><span class="label">Самозанятый (НПД):</span> <b>{escape(fio)}</b></p>
          <p><span class="label">ИНН:</span> <b>{escape(inn)}</b></p>
          <p><span class="label">E-mail:</span> <a href="mailto:{escape(CONTACT_EMAIL)}">{escape(CONTACT_EMAIL)}</a></p>
          <p><span class="label">Telegram:</span> <a href="{escape(PUBLIC_BOT_URL)}">{escape(PUBLIC_BOT_URL)}</a></p>
          <p><a href="/">← На главную</a></p>
        </div>
        """
    ))

@app.get("/legal/policy")
async def legal_policy():
    return RedirectResponse(LEGAL_POLICY_URL or "/")

@app.get("/legal/offer")
async def legal_offer():
    return RedirectResponse(LEGAL_OFFER_URL or "/")

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
                    backoff = 5
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
    await ensure_memory_schema_async()
    await ensure_users_policy_column_async()
    await ensure_users_created_at_column_async()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        ok = await bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=[],
        )
        print(f"[startup] set_webhook: {ok} -> {WEBHOOK_URL}")
    except Exception as e:
        print("[startup] set_webhook ERROR:", repr(e))

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

@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default="")
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return Response(status_code=403)

    data = await request.json()

    try:
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

    return PlainTextResponse("ok")
