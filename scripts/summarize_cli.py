# scripts/summarize_cli.py
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import asyncio
from app.db.core import async_session
from sqlalchemy import text as sql
from app.memory_summarizer import make_daily, rollup_weekly, rollup_topic_month

async def _all_user_ids():
    async with async_session() as s:
        rows = (await s.execute(sql("SELECT id FROM users ORDER BY id ASC"))).scalars().all()
    return [int(x) for x in rows]

async def _run():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily")
    d.add_argument("--day", help="YYYY-MM-DD (UTC), default=yesterday")
    d.add_argument("--user-id", type=int)

    w = sub.add_parser("weekly")
    w.add_argument("--monday", help="YYYY-MM-DD (UTC monday), default=prev_monday")
    w.add_argument("--user-id", type=int)

    m = sub.add_parser("monthly")
    m.add_argument("--month", help="YYYY-MM, default=prev month")
    m.add_argument("--user-id", type=int)

    a = p.parse_args()

    now = datetime.now(timezone.utc)

    if a.cmd == "daily":
        day = datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=timezone.utc) if a.day else (now - timedelta(days=1))
        ids = [a.user_id] if a.user_id else await _all_user_ids()
        for uid in ids:
            await make_daily(uid, day)

    elif a.cmd == "weekly":
        monday = datetime.strptime(a.monday, "%Y-%m-%d").replace(tzinfo=timezone.utc) if a.monday else (
            datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=now.weekday()+7)
        )
        ids = [a.user_id] if a.user_id else await _all_user_ids()
        for uid in ids:
            await rollup_weekly(uid, monday)

    elif a.cmd == "monthly":
        if a.month:
            dt = datetime.strptime(a.month, "%Y-%m")
            month_start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
        else:
            if now.month == 1:
                month_start = datetime(now.year-1, 12, 1, tzinfo=timezone.utc)
            else:
                month_start = datetime(now.year, now.month-1, 1, tzinfo=timezone.utc)
        ids = [a.user_id] if a.user_id else await _all_user_ids()
        for uid in ids:
            await rollup_topic_month(uid, month_start)

if __name__ == "__main__":
    asyncio.run(_run())
