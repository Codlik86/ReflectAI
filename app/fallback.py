# -*- coding: utf-8 -*-
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="fallback")

@router.message(Command("ping"))
async def on_ping(m: Message):
    try:
        await m.answer("pong ✅")
    except Exception:
        pass

# Последний рубеж — если профильные хэндлеры не сработали
@router.message(F.text)
async def last_resort(m: Message):
    try:
        txt = (m.text or "").strip()
        if txt:
            await m.answer("я здесь 🌿 " + (txt[:120] + ("…" if len(txt) > 120 else "")))
        else:
            await m.answer("я здесь 🌿")
    except Exception:
        pass
