# app/stats.py
from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import text

from app.db.core import async_session
from app.services.access_state import TRIAL_DAYS

stats_router = Router()

ADMIN_TG_ID = 53652078

_TZ = ZoneInfo(os.getenv("BOT_TZ", "Europe/Moscow"))
_TRIAL_INTERVAL_SQL = f"INTERVAL '{int(TRIAL_DAYS)} days'"

_STATS_COUNT_QUERIES = {
    "all": "SELECT COUNT(*) FROM users",
    "with_tg": "SELECT COUNT(*) FROM users WHERE tg_id IS NOT NULL",
    "with_access_now": f"""
        SELECT COUNT(DISTINCT u.id)
        FROM users u
        LEFT JOIN subscriptions s
          ON s.user_id = u.id
         AND s.status = 'active'
         AND s.subscription_until > NOW()
        WHERE s.id IS NOT NULL
           OR (u.trial_started_at IS NOT NULL
               AND COALESCE(u.trial_expires_at, u.trial_started_at + {_TRIAL_INTERVAL_SQL}) > NOW())
    """,
    "trial_started": """
        SELECT COUNT(*)
        FROM users
        WHERE trial_started_at IS NOT NULL
    """,
    "trial_active": f"""
        SELECT COUNT(*)
        FROM users
        WHERE trial_started_at IS NOT NULL
          AND COALESCE(trial_expires_at, trial_started_at + {_TRIAL_INTERVAL_SQL}) > NOW()
    """,
    "trial_ended": f"""
        SELECT COUNT(*)
        FROM users
        WHERE trial_started_at IS NOT NULL
          AND COALESCE(trial_expires_at, trial_started_at + {_TRIAL_INTERVAL_SQL}) <= NOW()
    """,
    "onboarding_done_no_trial": """
        SELECT COUNT(*)
        FROM users
        WHERE policy_accepted_at IS NOT NULL
          AND trial_started_at IS NULL
    """,
    "ad_start_no_onboarding": """
        SELECT COUNT(DISTINCT u.id)
        FROM users u
        JOIN ad_starts a ON a.tg_user_id = u.tg_id
        WHERE u.policy_accepted_at IS NULL
    """,
    "paid_last_30d": """
        SELECT COUNT(DISTINCT user_id)
        FROM payments
        WHERE status = 'succeeded'
          AND created_at >= NOW() - INTERVAL '30 days'
    """,
    "active_after_trial": """
        SELECT COUNT(*)
        FROM users u
        WHERE u.trial_started_at IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM bot_messages bm
            WHERE bm.user_id = u.id
              AND bm.created_at > u.trial_started_at
          )
    """,
}


async def _fetch_count(session, sql: str, params: dict | None = None) -> int:
    res = await session.execute(text(sql), params or {})
    try:
        return int(res.scalar() or 0)
    except Exception:
        return 0


def _truncate_for_tg(text_: str, limit: int = 3800) -> str:
    if len(text_) <= limit:
        return text_
    return text_[: limit - 1] + "…"


@stats_router.message(Command("stats"))
async def cmd_stats(m: Message):
    if int(getattr(getattr(m, "from_user", None), "id", 0) or 0) != ADMIN_TG_ID:
        return

    start_of_day_local = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_local.astimezone(ZoneInfo("UTC"))
    end_of_day_utc = start_of_day_utc + timedelta(days=1)

    async with async_session() as session:
        counts = {}
        for key, sql in _STATS_COUNT_QUERIES.items():
            counts[key] = await _fetch_count(session, sql)
        counts["interactions_today"] = await _fetch_count(
            session,
            """
            SELECT COUNT(*) FROM bot_messages
            WHERE created_at >= :start_day AND created_at < :end_day
            """,
            {"start_day": start_of_day_utc, "end_day": end_of_day_utc},
        )

        counts["miniapp_opened_all"] = await _fetch_count(
            session,
            """
            SELECT COUNT(DISTINCT user_id)
            FROM bot_events
            WHERE event_type = 'miniapp_opened'
            """,
        )
        counts["miniapp_opened_today"] = await _fetch_count(
            session,
            """
            SELECT COUNT(DISTINCT user_id)
            FROM bot_events
            WHERE event_type = 'miniapp_opened'
              AND created_at >= :start_day AND created_at < :end_day
            """,
            {"start_day": start_of_day_utc, "end_day": end_of_day_utc},
        )
        counts["miniapp_acted_all"] = await _fetch_count(
            session,
            """
            SELECT COUNT(DISTINCT user_id)
            FROM bot_events
            WHERE event_type = 'miniapp_action'
              AND ((payload::jsonb)->>'action') IN ('exercise_started','meditation_started','talk_opened')
            """,
        )
        counts["miniapp_acted_today"] = await _fetch_count(
            session,
            """
            SELECT COUNT(DISTINCT user_id)
            FROM bot_events
            WHERE event_type = 'miniapp_action'
              AND ((payload::jsonb)->>'action') IN ('exercise_started','meditation_started','talk_opened')
              AND created_at >= :start_day AND created_at < :end_day
            """,
            {"start_day": start_of_day_utc, "end_day": end_of_day_utc},
        )

    lines = [
        "Статистика «Помни»:",
        f"👥 Пользователи: {counts.get('all', 0)} (tg_id: {counts.get('with_tg', 0)})",
        "",
        "🆓 Триал:",
        f"— стартовали: {counts.get('trial_started', 0)}",
        f"— активен: {counts.get('trial_active', 0)}",
        f"— закончился: {counts.get('trial_ended', 0)}",
        f"— не стартовали (после онбординга): {counts.get('onboarding_done_no_trial', 0)}",
        "",
        "📣 Реклама:",
        f"— старт по рекламе, но онбординг не пройден: {counts.get('ad_start_no_onboarding', 0)}",
        "",
        "💳 Оплаты:",
        f"— купили за 30 дней: {counts.get('paid_last_30d', 0)}",
        "",
        "⚡ Активность:",
        f"— доступ сейчас (подписка/триал): {counts.get('with_access_now', 0)}",
        f"— активные после старта триала: {counts.get('active_after_trial', 0)}",
        f"— взаимодействий сегодня: {counts.get('interactions_today', 0)}",
        "",
        "🧩 Mini App:",
        f"— открыли (всего): {counts.get('miniapp_opened_all', 0)}",
        f"— открыли (сегодня): {counts.get('miniapp_opened_today', 0)}",
        f"— сделали действие (всего): {counts.get('miniapp_acted_all', 0)}",
        f"— сделали действие (сегодня): {counts.get('miniapp_acted_today', 0)}",
    ]

    await m.answer(_truncate_for_tg("\n".join(lines)))
