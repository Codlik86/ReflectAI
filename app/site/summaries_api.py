# app/site/summaries_api.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import text as sql
from app.db.core import async_session
from app.memory_summarizer import make_daily, rollup_weekly, rollup_monthly
from app.rag_summaries import delete_user_summaries

router = APIRouter(prefix="/api/admin/summaries", tags=["summaries"])

# --- единый секрет для всех cron-вызовов
_ADMIN_API_SECRET = os.getenv("ADMIN_API_SECRET", "").strip()

def _check_secret(request: Request, secret_param: Optional[str]) -> None:
    """
    Разрешаем, если:
      - переменная окружения пустая (гард выключен), ИЛИ
      - в заголовке ADMIN_API_SECRET / X-Admin-Secret пришло верное значение, ИЛИ
      - query-параметр ?secret совпал.
    Иначе — 403.
    """
    if not _ADMIN_API_SECRET:
        return

    header_secret = (
        request.headers.get("ADMIN_API_SECRET")
        or request.headers.get("X-Admin-Secret")
        or request.headers.get("x-admin-secret")
    )

    if header_secret == _ADMIN_API_SECRET or secret_param == _ADMIN_API_SECRET:
        return

    raise HTTPException(status_code=403, detail="forbidden")

async def _all_user_ids() -> List[int]:
    async with async_session() as s:
        rows = (await s.execute(sql("SELECT id FROM users ORDER BY id ASC"))).scalars().all()
    return [int(x) for x in rows]

def _parse_date_yyyy_mm_dd(s: Optional[str], *, default: datetime) -> datetime:
    if not s:
        return default
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="bad date, expected YYYY-MM-DD")

def _prev_monday_utc(today_utc: datetime) -> datetime:
    dow = today_utc.weekday()  # 0..6 (0=понедельник)
    return datetime(today_utc.year, today_utc.month, today_utc.day, tzinfo=timezone.utc) - timedelta(days=dow + 7)

def _month_start_utc(today_utc: datetime) -> datetime:
    y, m = today_utc.year, today_utc.month
    if m == 1:
        return datetime(y - 1, 12, 1, tzinfo=timezone.utc)
    return datetime(y, m - 1, 1, tzinfo=timezone.utc)

# --- DAILY --------------------------------------------------------------------

@router.post("/daily")
async def run_daily(
    request: Request,
    secret: Optional[str] = Query(default=None),
    day: Optional[str] = Query(default=None, description="YYYY-MM-DD, по умолчанию вчера (UTC)"),
    user_id: Optional[int] = Query(default=None),
):
    """
    Строит DAILY саммари за указанный день (UTC). Если user_id не задан — для всех пользователей.
    """
    _check_secret(request, secret)

    now_utc = datetime.now(timezone.utc)
    target_day = _parse_date_yyyy_mm_dd(day, default=(now_utc - timedelta(days=1)))
    target_day = datetime(target_day.year, target_day.month, target_day.day, tzinfo=timezone.utc)

    ids = [user_id] if user_id else await _all_user_ids()
    ok, fail = 0, 0
    for uid in ids:
        try:
            await make_daily(uid, target_day)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"[summaries/daily] user={uid} day={target_day.date()} ERROR: {e}")

    return {
        "status": "ok",
        "kind": "daily",
        "day": target_day.date().isoformat(),
        "processed": ok + fail,
        "ok": ok,
        "fail": fail,
    }

# --- WEEKLY -------------------------------------------------------------------

@router.post("/weekly")
async def run_weekly(
    request: Request,
    secret: Optional[str] = Query(default=None),
    monday: Optional[str] = Query(default=None, description="YYYY-MM-DD (понедельник недели), по умолчанию прошлый понедельник (UTC)"),
    user_id: Optional[int] = Query(default=None),
):
    """
    Собирает WEEKLY из daily за 7 дней, начиная с указанного понедельника (UTC).
    """
    _check_secret(request, secret)

    now_utc = datetime.now(timezone.utc)
    monday_dt = _parse_date_yyyy_mm_dd(monday, default=_prev_monday_utc(now_utc))

    ids = [user_id] if user_id else await _all_user_ids()
    ok, fail = 0, 0
    for uid in ids:
        try:
            await rollup_weekly(uid, monday_dt)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"[summaries/weekly] user={uid} monday={monday_dt.date()} ERROR: {e}")

    return {
        "status": "ok",
        "kind": "weekly",
        "monday": monday_dt.date().isoformat(),
        "processed": ok + fail,
        "ok": ok,
        "fail": fail,
    }

# --- MONTHLY (topic) ----------------------------------------------------------

@router.post("/monthly")
async def run_monthly(
    request: Request,
    secret: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None, description="YYYY-MM (первое число месяца), по умолчанию прошлый месяц (UTC)"),
    user_id: Optional[int] = Query(default=None),
):
    """
    Делает тематическую MONTHLY (kind='topic') за прошлый месяц либо за month=YYYY-MM.
    """
    _check_secret(request, secret)

    now_utc = datetime.now(timezone.utc)
    if month:
        try:
            dt = datetime.strptime(month, "%Y-%m")
            month_start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
        except Exception:
            raise HTTPException(status_code=400, detail="bad month, expected YYYY-MM")
    else:
        month_start = _month_start_utc(now_utc)

    ids = [user_id] if user_id else await _all_user_ids()
    ok, fail = 0, 0
    for uid in ids:
        try:
            await rollup_monthly(uid, month_start)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"[summaries/monthly] user={uid} month={month_start.date()} ERROR: {e}")

    return {
        "status": "ok",
        "kind": "topic",
        "month": month_start.strftime("%Y-%m"),
        "processed": ok + fail,
        "ok": ok,
        "fail": fail,
    }

# --- Сервисные (перестроить/очистить) ----------------------------------------

@router.post("/rebuild")
async def rebuild_for_user(
    request: Request,
    secret: Optional[str] = Query(default=None),
    user_id: int = Query(...),
    days: int = Query(30, ge=1, le=180),
):
    """
    Удаляет саммари пользователя из Qdrant и пересобирает:
      - daily за N дней назад,
      - weekly (8 недель),
      - monthly (3 месяца).
    """
    _check_secret(request, secret)

    await delete_user_summaries(user_id)

    now_utc = datetime.now(timezone.utc)
    # DAILY
    for d in range(1, days + 1):
        day = now_utc - timedelta(days=d)
        day = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        try:
            await make_daily(user_id, day)
        except Exception as e:
            print(f"[rebuild/daily] user={user_id} day={day.date()} ERROR: {e}")

    # WEEKLY
    monday = _prev_monday_utc(now_utc)
    for w in range(1, 9):
        start = monday - timedelta(days=7 * w)
        try:
            await rollup_weekly(user_id, start)
        except Exception as e:
            print(f"[rebuild/weekly] user={user_id} monday={start.date()} ERROR: {e}")

    # MONTHLY
    base = _month_start_utc(now_utc)
    for m in range(3):
        y, mo = base.year, base.month - m
        while mo <= 0:
            y -= 1
            mo += 12
        ms = datetime(y, mo, 1, tzinfo=timezone.utc)
        try:
            await rollup_monthly(user_id, ms)
        except Exception as e:
            print(f"[rebuild/monthly] user={user_id} month={ms.date()} ERROR: {e}")

    return {"status": "ok", "user_id": user_id}
