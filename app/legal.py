# app/legal.py
import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

# Внешние ссылки
LEGAL_POLICY_URL = os.getenv("LEGAL_POLICY_URL", "").strip()
LEGAL_OFFER_URL  = os.getenv("LEGAL_OFFER_URL", "").strip()

# /legal/policy -> внешний документ или 404
@router.get("/legal/policy")
async def policy_redirect():
    if LEGAL_POLICY_URL:
        return RedirectResponse(LEGAL_POLICY_URL, status_code=302)
    return HTMLResponse("<p>Ссылка на политику не настроена.</p>", status_code=404)

# /legal/offer -> внешний документ или 404
@router.get("/legal/offer")
async def offer_redirect():
    if LEGAL_OFFER_URL:
        return RedirectResponse(LEGAL_OFFER_URL, status_code=302)
    return HTMLResponse("<p>Ссылка на оферту не настроена.</p>", status_code=404)
