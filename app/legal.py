# app/legal.py
import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

PROJECT_NAME    = os.getenv("PROJECT_NAME", "Помни")
PUBLIC_BOT_URL  = os.getenv("PUBLIC_BOT_URL", "https://t.me/reflectttaibot")
CONTACT_EMAIL   = os.getenv("CONTACT_EMAIL", "selflect@proton.me")

LEGAL_POLICY_URL = os.getenv("LEGAL_POLICY_URL", "")
LEGAL_OFFER_URL  = os.getenv("LEGAL_OFFER_URL", "")

INN_SELFEMP = os.getenv("INN_SELFEMP", "").strip()

def _a(href: str, text: str) -> str:
    return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{text}</a>'

@router.get("/", response_class=HTMLResponse)
async def landing():
    policy_link = LEGAL_POLICY_URL or "/legal/policy"
    offer_link  = LEGAL_OFFER_URL  or "/legal/offer"
    inn_block = INN_SELFEMP if INN_SELFEMP else "—"

    html = f"""
    <html><head>
      <meta charset="utf-8" />
      <title>{PROJECT_NAME}</title>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif;
               margin: 40px; color:#111; }}
        .card {{ border:1px solid #eee; border-radius:12px; padding:24px; max-width:720px; }}
        h1 {{ font-size:40px; margin:0 0 12px; }}
        .muted {{ color:#555; }}
        a {{ color:#1a73e8; text-decoration:none; }}
        a:hover {{ text-decoration:underline; }}
        .links a {{ margin-right: 16px; }}
      </style>
    </head><body>
      <h1>{PROJECT_NAME}</h1>
      <p class="muted">Эмоциональная поддержка и само-рефлексия в Telegram.</p>
      <div class="card">
        <p>Бот: {_a(PUBLIC_BOT_URL, PUBLIC_BOT_URL)}</p>
        <p>Поддержка: {_a("mailto:"+CONTACT_EMAIL, CONTACT_EMAIL)}</p>
        <p>Самозанятый, ИНН: {inn_block}</p>
        <p class="links">
          {_a("/requisites", "Реквизиты")}
          · {_a(policy_link, "Правила и Политика")}
          · {_a(offer_link, "Оферта")}
        </p>
      </div>
    </body></html>
    """
    return HTMLResponse(html)

@router.get("/requisites", response_class=HTMLResponse)
async def requisites():
    inn_block = INN_SELFEMP if INN_SELFEMP else "—"
    html = f"""
    <html><head><meta charset="utf-8"><title>Реквизиты</title></head>
    <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif; margin:40px">
      <h2>Реквизиты</h2>
      <p>Самозанятый (НПД)</p>
      <p><b>ИНН:</b> {inn_block}</p>
      <p><b>E-mail:</b> {CONTACT_EMAIL}</p>
      <p><a href="/">← На главную</a></p>
    </body></html>
    """
    return HTMLResponse(html)

# Редиректы на внешние документы (на случай, если где-то укажем /legal/policy|offer)
@router.get("/legal/policy")
async def policy_redirect():
    if LEGAL_POLICY_URL:
        return RedirectResponse(LEGAL_POLICY_URL, status_code=302)
    return HTMLResponse("<p>Ссылка на политику не настроена.</p>", status_code=404)

@router.get("/legal/offer")
async def offer_redirect():
    if LEGAL_OFFER_URL:
        return RedirectResponse(LEGAL_OFFER_URL, status_code=302)
    return HTMLResponse("<p>Ссылка на оферту не настроена.</p>", status_code=404)
