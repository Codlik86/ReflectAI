# app/site/summaries_api.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
import os
import asyncio
import logging
import time
import uuid
import random

from sqlalchemy import text as sql
from app.db.core import async_session
from app.memory_summarizer import make_daily, rollup_weekly, rollup_monthly
from app.rag_summaries import delete_user_summaries

router = APIRouter(prefix="/api/admin/summaries", tags=["summaries"])
logger = logging.getLogger(__name__)

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


def _env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name, "")
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name, "")
        return float(v) if v else float(default)
    except Exception:
        return float(default)


DAILY_BATCH_SIZE = _env_int("DAILY_SUMMARIES_BATCH_SIZE", 50)
DAILY_MAX_RUNTIME_SEC = _env_float("DAILY_SUMMARIES_MAX_RUNTIME_SEC", 900.0)

DAILY_JOBS: Dict[str, Dict[str, Any]] = {}
DAILY_LOCK_KEY = "summaries_daily"


def _is_rate_limit_error(err: Exception) -> bool:
    s = str(err).lower()
    return "429" in s or "too many requests" in s or "rate limit" in s


async def _try_advisory_lock(session, key: str) -> bool:
    res = await session.execute(sql("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key})
    return bool(res.scalar())


async def _advisory_unlock(session, key: str) -> None:
    await session.execute(sql("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})


async def _iter_user_batches(batch_size: int):
    last_id = 0
    while True:
        async with async_session() as s:
            rows = (
                await s.execute(
                    sql(
                        "SELECT id FROM users WHERE id > :last ORDER BY id ASC LIMIT :lim"
                    ),
                    {"last": int(last_id), "lim": int(batch_size)},
                )
            ).scalars().all()
        if not rows:
            break
        ids = [int(x) for x in rows]
        last_id = ids[-1]
        yield ids


async def _run_daily_job(
    lock_session,
    *,
    job_id: str,
    target_day: datetime,
    user_id: Optional[int],
    user_ids: Optional[List[int]],
    batch_size: int,
    max_runtime_sec: float,
):
    started = time.monotonic()
    counters = {
        "checked_users": 0,
        "processed_users": 0,
        "summaries_written": 0,
        "embedding_ok": 0,
        "embedding_429": 0,
        "skipped": 0,
        "errors": 0,
    }
    status = "running"
    remaining = None

    DAILY_JOBS[job_id] = {
        "status": status,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "counters": counters,
        "target_day": target_day.date().isoformat(),
        "user_id": user_id,
        "user_ids_count": len(user_ids) if user_ids is not None else None,
    }

    logger.info(
        "[summaries/daily] job start job_id=%s day=%s user_id=%s batch_size=%s max_runtime_sec=%s",
        job_id,
        target_day.date().isoformat(),
        user_id,
        len(user_ids) if user_ids is not None else None,
        batch_size,
        max_runtime_sec,
    )

    try:
        if user_ids is not None:
            processed_list = 0
            total_list = len(user_ids)
            for i in range(0, total_list, int(batch_size)):
                ids = [int(x) for x in user_ids[i : i + int(batch_size)]]
                for uid in ids:
                    counters["checked_users"] += 1
                    processed_list += 1
                    if time.monotonic() - started > max_runtime_sec:
                        status = "partial"
                        remaining = max(0, total_list - processed_list)
                        logger.warning(
                            "[summaries/daily] job budget exceeded job_id=%s remaining=%s",
                            job_id,
                            remaining,
                        )
                        raise asyncio.CancelledError()

                    try:
                        await make_daily(int(uid), target_day)
                        counters["processed_users"] += 1
                        counters["summaries_written"] += 1
                        counters["embedding_ok"] += 1
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            counters["embedding_429"] += 1
                            counters["skipped"] += 1
                            logger.warning(
                                "[summaries/daily] rate_limit user=%s day=%s err=%s",
                                uid,
                                target_day.date().isoformat(),
                                repr(e),
                            )
                            await asyncio.sleep(0.5 + random.random())
                            continue
                        counters["errors"] += 1
                        logger.exception(
                            "[summaries/daily] user error user=%s day=%s",
                            uid,
                            target_day.date().isoformat(),
                        )
        elif user_id:
            ids_batches = [ [int(user_id)] ]
            for ids in ids_batches:
                for uid in ids:
                    counters["checked_users"] += 1
                    if time.monotonic() - started > max_runtime_sec:
                        status = "partial"
                        remaining = 0
                        logger.warning(
                            "[summaries/daily] job budget exceeded job_id=%s remaining=%s",
                            job_id,
                            remaining,
                        )
                        raise asyncio.CancelledError()

                    try:
                        await make_daily(int(uid), target_day)
                        counters["processed_users"] += 1
                        counters["summaries_written"] += 1
                        counters["embedding_ok"] += 1
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            counters["embedding_429"] += 1
                            counters["skipped"] += 1
                            logger.warning(
                                "[summaries/daily] rate_limit user=%s day=%s err=%s",
                                uid,
                                target_day.date().isoformat(),
                                repr(e),
                            )
                            await asyncio.sleep(0.5 + random.random())
                            continue
                        counters["errors"] += 1
                        logger.exception(
                            "[summaries/daily] user error user=%s day=%s",
                            uid,
                            target_day.date().isoformat(),
                        )
        else:
            async for ids in _iter_user_batches(batch_size):
                for uid in ids:
                    counters["checked_users"] += 1
                    if time.monotonic() - started > max_runtime_sec:
                        status = "partial"
                        async with async_session() as s:
                            remaining = (
                                await s.execute(
                                    sql("SELECT COUNT(*) FROM users WHERE id > :last"),
                                    {"last": int(uid)},
                                )
                            ).scalar()
                        logger.warning(
                            "[summaries/daily] job budget exceeded job_id=%s remaining=%s",
                            job_id,
                            remaining,
                        )
                        raise asyncio.CancelledError()

                    try:
                        await make_daily(int(uid), target_day)
                        counters["processed_users"] += 1
                        counters["summaries_written"] += 1
                        counters["embedding_ok"] += 1
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            counters["embedding_429"] += 1
                            counters["skipped"] += 1
                            logger.warning(
                                "[summaries/daily] rate_limit user=%s day=%s err=%s",
                                uid,
                                target_day.date().isoformat(),
                                repr(e),
                            )
                            await asyncio.sleep(0.5 + random.random())
                            continue
                        counters["errors"] += 1
                        logger.exception(
                            "[summaries/daily] user error user=%s day=%s",
                            uid,
                            target_day.date().isoformat(),
                        )
    except asyncio.CancelledError:
        pass
    except Exception:
        status = "failed"
        counters["errors"] += 1
        logger.exception("[summaries/daily] job failed job_id=%s", job_id)
    finally:
        try:
            await _advisory_unlock(lock_session, DAILY_LOCK_KEY)
        except Exception:
            logger.exception("[summaries/daily] unlock failed job_id=%s", job_id)
        try:
            await lock_session.close()
        except Exception:
            logger.exception("[summaries/daily] lock session close failed job_id=%s", job_id)

        if status == "running":
            status = "ok"

        DAILY_JOBS[job_id].update(
            {
                "status": status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "remaining": remaining,
            }
        )
        logger.info(
            "[summaries/daily] job done job_id=%s status=%s counters=%s remaining=%s",
            job_id,
            status,
            counters,
            remaining,
        )

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

    logger.info(
        "[summaries/daily] request method=%s day=%s user_id=%s",
        request.method,
        target_day.date().isoformat(),
        user_id,
    )

    lock_session = async_session()
    try:
        got_lock = await _try_advisory_lock(lock_session, DAILY_LOCK_KEY)
        if not got_lock:
            await lock_session.close()
            return {
                "status": "already_running",
                "kind": "daily",
                "day": target_day.date().isoformat(),
            }
    except Exception:
        await lock_session.close()
        logger.exception("[summaries/daily] lock check failed")
        raise HTTPException(status_code=500, detail="lock_failed")

    job_id = f"daily-{uuid.uuid4().hex[:12]}"
    DAILY_JOBS[job_id] = {"status": "queued", "queued_at": datetime.now(timezone.utc).isoformat()}
    asyncio.create_task(
        _run_daily_job(
            lock_session,
            job_id=job_id,
            target_day=target_day,
            user_id=user_id,
            user_ids=None,
            batch_size=DAILY_BATCH_SIZE,
            max_runtime_sec=DAILY_MAX_RUNTIME_SEC,
        )
    )

    return {
        "status": "queued",
        "kind": "daily",
        "day": target_day.date().isoformat(),
        "job_id": job_id,
    }


@router.get("/status")
async def summaries_status(
    request: Request,
    secret: Optional[str] = Query(default=None),
    job_id: Optional[str] = Query(default=None),
):
    _check_secret(request, secret)
    if not job_id:
        return {"status": "bad_request", "error": "job_id is required"}
    return {"job_id": job_id, "data": DAILY_JOBS.get(job_id)}

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
