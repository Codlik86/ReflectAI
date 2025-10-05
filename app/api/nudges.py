# app/api/nudges.py
from __future__ import annotations

import os
from typing import List, Dict, Any, Tuple

from fastapi import APIRouter, Header, Response, HTTPException, Query
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from sqlalchemy import text
from app.db.core import async_session, get_session
from app.billing.service import check_access

router = APIRouter(prefix="/api/admin/nudges", tags=["admin-nudges"])

ADMIN_API_SECRET = os.getenv("ADMIN_API_SECRET", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

if not BOT_TOKEN:
    # не бросаем исключение при импортe, но в рантайме проверим
    pass


WEEK_MSG_ACTIVE = (
    "Привет! Неделю не виделись. Если сейчас всё спокойно — я искренне рад за тебя. "
    "Захочется навести ясность — я рядом. 💛"
)

MONTH_MSG_ACTIVE = (
    "Привет! Кажется, месяц пролетел без разговоров. Пусть это будет признак стабильности. "
    "Если понадобится тёплый разбор — пиши, я здесь. 💛"
)

WEEK_MSG_NOACCESS = (
    "Привет! Неделя после паузы. Надеюсь, у тебя все хорошо! "
    "Если снова захочется опоры, можно включить доступ в /pay — я буду рядом."
)

MONTH_MSG_NOACCESS = (
    "Как ты там? Давно не виделись — уже месяц с паузы. Держу место тёплым: когда пригодится поддержка, "
    "загляни и включи доступ в /pay. Я тут. ✨"
)


def _kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")]
    ])


def _kb_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")],
        [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
    ])


async def _pick_targets(period: str) -> List[Dict[str, Any]]:
    """
    Возвращает список юзеров для пуша.
    period: 'week' | 'month'
    Фильтры:
      - last_activity ∈ [7,30) для week; ≥30 для month
      - не отправляли такой пуш ранее (см. bot_events)
    """
    assert period in ("week", "month")
    if period == "week":
        cond = "last_at <= NOW() - INTERVAL '7 days' AND last_at > NOW() - INTERVAL '30 days'"
        event_type = "nudge_week"
    else:
        cond = "last_at <= NOW() - INTERVAL '30 days'"
        event_type = "nudge_month"

    sql = f"""
    WITH last_msg AS (
        SELECT user_id, MAX(created_at) AS last_at
        FROM bot_messages
        GROUP BY user_id
    ),
    sent AS (
        SELECT DISTINCT user_id
        FROM bot_events
        WHERE event_type = :etype
          AND created_at >= NOW() - INTERVAL '180 days'
    )
    SELECT u.id AS user_id, u.tg_id, COALESCE(l.last_at, u.created_at) AS last_at
    FROM users u
    LEFT JOIN last_msg l ON l.user_id = u.id
    LEFT JOIN sent s ON s.user_id = u.id
    WHERE s.user_id IS NULL
      AND COALESCE(l.last_at, u.created_at) IS NOT NULL
      AND {cond}
    LIMIT 2000
    """
    async with async_session() as s:
        rows = (await s.execute(text(sql), {"etype": event_type})).mappings().all()
    return [dict(r) for r in rows]


async def _mark_sent(user_id: int, etype: str, payload: Dict[str, Any]) -> None:
    try:
        async with async_session() as s:
            await s.execute(
                text("""
                    INSERT INTO bot_events (user_id, event_type, payload, created_at)
                    VALUES (:uid, :etype, :payload::jsonb, CURRENT_TIMESTAMP)
                """),
                {"uid": user_id, "etype": etype, "payload": json_dumps(payload)},
            )
            await s.commit()
    except Exception:
        pass


def json_dumps(obj: Dict[str, Any]) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _msg_for_user(has_access: bool, period: str) -> Tuple[str, InlineKeyboardMarkup]:
    if has_access and period == "week":
        return WEEK_MSG_ACTIVE, _kb_menu()
    if has_access and period == "month":
        return MONTH_MSG_ACTIVE, _kb_menu()
    if not has_access and period == "week":
        return WEEK_MSG_NOACCESS, _kb_pay()
    # not access & month
    return MONTH_MSG_NOACCESS, _kb_pay()


@router.post("/run")
async def run_nudges(
    period: str = Query(pattern="^(week|month)$"),
    dry_run: int = Query(0, description="1 — только посчитать, не отправлять"),
    x_admin_secret: str = Header(default="", alias="X-Admin-Secret")
):
    if x_admin_secret != ADMIN_API_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is missing")

    targets = await _pick_targets(period)

    # Проверим доступ и отправим
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sent = 0
    checked = 0

    async for session in get_session():
        for row in targets:
            checked += 1
            uid = int(row["user_id"])
            tg_id = int(row["tg_id"])

            try:
                access_ok = await check_access(session, uid)
            except Exception:
                access_ok = False

            if dry_run:
                continue

            text, kb = _msg_for_user(access_ok, period)
            try:
                await bot.send_message(chat_id=tg_id, text=text, reply_markup=kb, disable_web_page_preview=True)
                sent += 1
                await _mark_sent(uid, f"nudge_{period}", {"has_access": access_ok})
            except Exception as e:
                # не стопаем массовую рассылку — просто лог
                print(f"[nudges] send error for user {uid} ({tg_id}): {e}")

    return {"period": period, "checked": checked, "sent": 0 if dry_run else sent}
