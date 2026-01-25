# app/api/nudges.py
from __future__ import annotations

import os
from typing import List, Dict, Any, Tuple, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.exceptions import TelegramForbiddenError

import logging

from sqlalchemy import text as sa_text
from app.db.core import async_session, get_session
from app.billing.service import check_access
from app.services.tg_blocked import mark_user_blocked

router = APIRouter(prefix="/api/admin/nudges", tags=["admin-nudges"])
logger = logging.getLogger(__name__)

def _env_clean(*names: str, default: str = "") -> str:
    for n in names:
        if not n:
            continue
        v = os.getenv(n)
        if v:
            return v.strip().strip('"').strip("'")
    return default

ADMIN_API_SECRET = _env_clean("ADMIN_API_SECRET")
BOT_TOKEN = _env_clean("BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
MINIAPP_URL = _env_clean("MINIAPP_URL")

if not BOT_TOKEN:
    # не бросаем исключение при импортe, но в рантайме проверим
    pass


# ---- Тексты уведомлений ----

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

ONB_INCOMPLETE_TEXT = (
    "Если захочется продолжить знакомство — я рядом. Можно спокойно вернуться, когда будет время."
)

DORMANT_AFTER_ONB_TEXT = (
    "Можно продолжить — я рядом. Открой меню или мини-приложение, когда будет удобно."
)

TRIAL_3DAYS_LEFT_TEXT = (
    "Осталось 3 дня до окончания пробного периода. "
)

TRIAL_EXPIRED_3D_TEXT = (
    "Пробный период завершен. Если захочется вернуться к практике и поддержке — я рядом. ✨"
)

TRIAL_EXPIRED_12D_TEXT = (
    "Давно не виделись. Если захочется вернуться — я на месте."
)

# ---- Клавиатуры ----

def _kb_pay_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оформить подписку 💳", callback_data="pay:plans")]
    ])

def _kb_onb_cta() -> InlineKeyboardMarkup:
    if MINIAPP_URL:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть мини-приложение", web_app=WebAppInfo(url=MINIAPP_URL))],
                [InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть меню", callback_data="menu:main")]]
    )


# ---- Вспомогательные функции ----

def _normalize_kind(kind: str) -> str:
    if kind in ("week", "month"):
        return f"nudge_{kind}"
    return kind


def _is_bot_blocked_error(err: Exception) -> bool:
    if not isinstance(err, TelegramForbiddenError):
        return False
    msg = str(err).lower()
    return (
        "bot was blocked by the user" in msg
        or "user is deactivated" in msg
    )


def _dt_to_iso(val: Any) -> Optional[str]:
    try:
        return val.isoformat() if val else None
    except Exception:
        return None


def _check_admin_secret(x_admin_secret: str, secret: Optional[str]) -> None:
    if not ADMIN_API_SECRET:
        return
    if x_admin_secret:
        if x_admin_secret != ADMIN_API_SECRET:
            raise HTTPException(status_code=403, detail="forbidden")
        return
    if secret == ADMIN_API_SECRET:
        return
    raise HTTPException(status_code=403, detail="forbidden")


async def _was_sent_recently(session, user_id: int, kind: str) -> bool:
    res = await session.execute(
        sa_text("""
            SELECT 1
            FROM public.nudges
            WHERE user_id = :uid
              AND kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
            LIMIT 1
        """),
        {"uid": user_id, "kind": kind},
    )
    return res.scalar() is not None


async def _log_nudge(user_id: int, tg_id: int, kind: str, payload: Dict[str, Any]) -> bool:
    try:
        async with async_session() as s:
            await s.execute(
                sa_text(_NUDGE_INSERT_SQL),
                {"uid": user_id, "tg": tg_id, "kind": kind, "payload": json_dumps(payload)},
            )
            await s.commit()
            return True
    except Exception:
        logger.exception("[nudges-db] insert failed kind=%s user_id=%s tg_id=%s", kind, user_id, tg_id)
        return False


async def _pick_targets(kind: str, min_days: int | None = None, max_days: int | None = None) -> List[Dict[str, Any]]:
    """
    Возвращает список юзеров для пуша.
    kind: 'week' | 'month' | 'onb_12h' | 'onb_48h' | 'dormant_12h' | 'dormant_48h'
          | 'trial_3days_left_inactive' | 'trial_expired_3d' | 'trial_expired_12d'
    min_days/max_days — переопределяют стандартные пороги (week: 7..30, month: >=30)
    """
    assert kind in (
        "week",
        "month",
        "onb_12h",
        "onb_48h",
        "dormant_12h",
        "dormant_48h",
        "trial_3days_left_inactive",
        "trial_expired_3d",
        "trial_expired_12d",
    )

    dedupe_kind = _normalize_kind(kind)

    if kind in ("week", "month"):
        if kind == "week":
            min_d = min_days if (min_days is not None) else 7
            max_d = max_days if (max_days is not None) else 30
            cond = f"last_at <= NOW() - INTERVAL '{int(min_d)} days' AND last_at > NOW() - INTERVAL '{int(max_d)} days'"
        else:
            min_d = min_days if (min_days is not None) else 30
            cond = f"last_at <= NOW() - INTERVAL '{int(min_d)} days'"

        sql = f"""
        WITH last_msg AS (
            SELECT user_id, MAX(created_at) AS last_at
            FROM public.bot_messages
            GROUP BY user_id
        ),
        sent AS (
            SELECT DISTINCT user_id
            FROM public.nudges
            WHERE kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
        )
        SELECT u.id AS user_id, u.tg_id, COALESCE(l.last_at, u.created_at) AS last_at
        FROM public.users u
        LEFT JOIN last_msg l ON l.user_id = u.id
        LEFT JOIN sent s ON s.user_id = u.id
        WHERE s.user_id IS NULL
          AND u.tg_is_blocked IS NOT TRUE
          AND COALESCE(l.last_at, u.created_at) IS NOT NULL
          AND {cond}
        ORDER BY COALESCE(l.last_at, u.created_at) DESC NULLS LAST
        LIMIT 2000
        """
        async with async_session() as s:
            rows = (await s.execute(sa_text(sql), {"kind": dedupe_kind})).mappings().all()
        return [dict(r) for r in rows]

    if kind in ("onb_12h", "onb_48h"):
        if kind == "onb_12h":
            cond = (
                "start_at <= NOW() - INTERVAL '12 hours' "
                "AND start_at > NOW() - INTERVAL '24 hours'"
            )
        else:
            cond = (
                "start_at <= NOW() - INTERVAL '48 hours' "
                "AND start_at > NOW() - INTERVAL '72 hours'"
            )

        sql = f"""
        WITH ad_start AS (
            SELECT tg_user_id, MIN(created_at) AS ad_start_at
            FROM public.ad_starts
            WHERE tg_user_id IS NOT NULL
            GROUP BY tg_user_id
        ),
        sent AS (
            SELECT DISTINCT user_id
            FROM public.nudges
            WHERE kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
        ),
        base AS (
            SELECT u.id AS user_id, u.tg_id, COALESCE(a.ad_start_at, u.created_at) AS start_at
            FROM public.users u
            LEFT JOIN ad_start a ON a.tg_user_id = u.tg_id
            LEFT JOIN sent s ON s.user_id = u.id
            WHERE u.policy_accepted_at IS NULL
              AND u.tg_id IS NOT NULL
              AND u.tg_is_blocked IS NOT TRUE
              AND s.user_id IS NULL
              AND COALESCE(a.ad_start_at, u.created_at) IS NOT NULL
        )
        SELECT * FROM base
        WHERE {cond}
        LIMIT 2000
        """
        async with async_session() as s:
            rows = (await s.execute(sa_text(sql), {"kind": dedupe_kind})).mappings().all()
        return [dict(r) for r in rows]

    if kind in ("dormant_12h", "dormant_48h"):
        if kind == "dormant_12h":
            cond = (
                "u.policy_accepted_at <= NOW() - INTERVAL '12 hours' "
                "AND u.policy_accepted_at > NOW() - INTERVAL '24 hours'"
            )
        else:
            cond = (
                "u.policy_accepted_at <= NOW() - INTERVAL '48 hours' "
                "AND u.policy_accepted_at > NOW() - INTERVAL '72 hours'"
            )

        sql = f"""
        WITH sent AS (
            SELECT DISTINCT user_id
            FROM public.nudges
            WHERE kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
        )
        SELECT u.id AS user_id, u.tg_id, u.policy_accepted_at
        FROM public.users u
        LEFT JOIN sent s ON s.user_id = u.id
        WHERE u.policy_accepted_at IS NOT NULL
          AND u.tg_id IS NOT NULL
          AND u.tg_is_blocked IS NOT TRUE
          AND s.user_id IS NULL
          AND {cond}
          AND NOT EXISTS (
              SELECT 1
              FROM public.bot_messages bm
              WHERE bm.user_id = u.id
                AND bm.role = 'user'
                AND bm.created_at > u.policy_accepted_at
          )
        LIMIT 2000
        """
        async with async_session() as s:
            rows = (await s.execute(sa_text(sql), {"kind": dedupe_kind})).mappings().all()
        return [dict(r) for r in rows]

    if kind == "trial_3days_left_inactive":
        sql = """
        WITH sent AS (
            SELECT DISTINCT user_id
            FROM public.nudges
            WHERE kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
        )
        SELECT u.id AS user_id, u.tg_id, u.trial_started_at, u.trial_expires_at
        FROM public.users u
        LEFT JOIN sent s ON s.user_id = u.id
        WHERE u.trial_started_at IS NOT NULL
          AND u.trial_expires_at IS NOT NULL
          AND u.tg_id IS NOT NULL
          AND u.tg_is_blocked IS NOT TRUE
          AND s.user_id IS NULL
          AND u.trial_expires_at > NOW() + INTERVAL '2 days'
          AND u.trial_expires_at <= NOW() + INTERVAL '3 days'
          AND NOT EXISTS (
              SELECT 1
              FROM public.bot_messages bm
              WHERE bm.user_id = u.id
                AND bm.role = 'user'
                AND bm.created_at > u.trial_started_at
          )
        LIMIT 2000
        """
        async with async_session() as s:
            rows = (await s.execute(sa_text(sql), {"kind": dedupe_kind})).mappings().all()
        return [dict(r) for r in rows]

    if kind == "trial_expired_3d":
        sql = """
        WITH sent AS (
            SELECT DISTINCT user_id
            FROM public.nudges
            WHERE kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
        )
        SELECT u.id AS user_id, u.tg_id, u.trial_expires_at
        FROM public.users u
        LEFT JOIN sent s ON s.user_id = u.id
        WHERE u.trial_expires_at IS NOT NULL
          AND u.tg_id IS NOT NULL
          AND u.tg_is_blocked IS NOT TRUE
          AND s.user_id IS NULL
          AND u.trial_expires_at <= NOW() - INTERVAL '3 days'
          AND u.trial_expires_at > NOW() - INTERVAL '4 days'
        LIMIT 2000
        """
        async with async_session() as s:
            rows = (await s.execute(sa_text(sql), {"kind": dedupe_kind})).mappings().all()
        return [dict(r) for r in rows]

    if kind == "trial_expired_12d":
        sql = """
        WITH sent AS (
            SELECT DISTINCT user_id
            FROM public.nudges
            WHERE kind = :kind
              AND created_at >= NOW() - INTERVAL '180 days'
        )
        SELECT u.id AS user_id, u.tg_id, u.trial_expires_at
        FROM public.users u
        LEFT JOIN sent s ON s.user_id = u.id
        WHERE u.trial_expires_at IS NOT NULL
          AND u.tg_id IS NOT NULL
          AND u.tg_is_blocked IS NOT TRUE
          AND s.user_id IS NULL
          AND u.trial_expires_at <= NOW() - INTERVAL '12 days'
          AND u.trial_expires_at > NOW() - INTERVAL '13 days'
        LIMIT 2000
        """
        async with async_session() as s:
            rows = (await s.execute(sa_text(sql), {"kind": dedupe_kind})).mappings().all()
        return [dict(r) for r in rows]

    return []


def _msg_for_kind(kind: str, has_access: Optional[bool] = None) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    if kind in ("week", "month"):
        if has_access is None:
            has_access = False
        return _msg_for_user(has_access, kind)
    if kind in ("onb_12h", "onb_48h"):
        return ONB_INCOMPLETE_TEXT, None
    if kind in ("dormant_12h", "dormant_48h"):
        return DORMANT_AFTER_ONB_TEXT, _kb_onb_cta()
    if kind == "trial_3days_left_inactive":
        return TRIAL_3DAYS_LEFT_TEXT, None
    if kind == "trial_expired_3d":
        return TRIAL_EXPIRED_3D_TEXT, _kb_onb_cta()
    if kind == "trial_expired_12d":
        return TRIAL_EXPIRED_12D_TEXT, _kb_onb_cta()
    return "", None


def json_dumps(obj: Dict[str, Any]) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


_NUDGE_INSERT_SQL = """
    INSERT INTO public.nudges (user_id, tg_id, kind, payload, created_at)
    VALUES (:uid, :tg, :kind, CAST(:payload AS jsonb), CURRENT_TIMESTAMP)
"""


def _msg_for_user(has_access: bool, period: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    if has_access and period == "week":
        return WEEK_MSG_ACTIVE, None
    if has_access and period == "month":
        return MONTH_MSG_ACTIVE, None
    if not has_access and period == "week":
        return WEEK_MSG_NOACCESS, _kb_pay_only()
    # not access & month
    return MONTH_MSG_NOACCESS, _kb_pay_only()


# ---- Эндпоинты ----

@router.post("/run")
async def run_nudges(
    period: Optional[str] = Query(default=None, pattern="^(week|month)$"),
    kind: Optional[str] = Query(
        default=None,
        pattern="^(week|month|onb_12h|onb_48h|dormant_12h|dormant_48h|trial_3days_left_inactive|trial_expired_3d|trial_expired_12d|all)$",
    ),
    dry_run: int = Query(0, description="1 — только посчитать, не отправлять"),
    min_days: int | None = Query(None),
    max_days: int | None = Query(None),
    debug: int = Query(0, description="1 — подробные логи для первых debug_limit пользователей"),
    debug_limit: int = Query(30, description="сколько пользователей подробно логировать"),
    max_send: int | None = Query(None, description="лимит реальных успешных отправок"),
    x_admin_secret: str = Header(default="", alias="X-Admin-Secret"),
    secret: Optional[str] = Query(default=None),
):
    _check_admin_secret(x_admin_secret, secret)

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is missing")

    run_kind = kind or period
    if not run_kind:
        raise HTTPException(status_code=400, detail="kind or period is required")

    if run_kind == "all":
        kinds = [
            "onb_12h",
            "onb_48h",
            "dormant_12h",
            "dormant_48h",
            "trial_3days_left_inactive",
            "trial_expired_3d",
            "trial_expired_12d",
            "week",
            "month",
        ]
    else:
        kinds = [run_kind]

    logger.info(
        "[nudges] start kind=%s period=%s dry_run=%s min_days=%s max_days=%s debug=%s debug_limit=%s max_send=%s",
        kind,
        period,
        int(dry_run),
        min_days,
        max_days,
        int(debug),
        debug_limit,
        max_send,
    )

    # Проверим доступ и отправим
    total_sent = 0
    total_checked = 0
    results: list[dict[str, Any]] = []
    total_counters: Dict[str, int] = {}
    db_preflight: Dict[str, Any] = {"ok": False}

    def _init_counters() -> Dict[str, int]:
        return {
            "targets": 0,
            "checked": 0,
            "skipped_already_sent": 0,
            "skipped_blocked": 0,
            "skipped_no_tg_id": 0,
            "skipped_dry_run": 0,
            "skipped_no_text": 0,
            "skipped_max_send": 0,
            "send_ok": 0,
            "send_blocked": 0,
            "send_chat_not_found": 0,
            "send_other_error": 0,
            "db_log_ok": 0,
            "db_log_error": 0,
        }

    def _add_counters(dst: Dict[str, int], src: Dict[str, int]) -> None:
        for k, v in src.items():
            dst[k] = int(dst.get(k, 0)) + int(v)

    # корректно открываем и закрываем aiohttp-сессию бота
    async with Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
        async for session in get_session():
            try:
                res = await session.execute(
                    sa_text("SELECT current_database() AS db, inet_server_addr() AS addr, now() AS now")
                )
                row = res.mappings().first() or {}
                res2 = await session.execute(
                    sa_text("SELECT to_regclass('public.nudges') AS nudges_table")
                )
                row2 = res2.mappings().first() or {}
                db_preflight = {
                    "ok": True,
                    "database": row.get("db"),
                    "server_addr": row.get("addr"),
                    "now": row.get("now"),
                    "nudges_table": row2.get("nudges_table"),
                }
                logger.info(
                    "[nudges] db_preflight ok db=%s addr=%s now=%s nudges_table=%s",
                    db_preflight.get("database"),
                    db_preflight.get("server_addr"),
                    db_preflight.get("now"),
                    db_preflight.get("nudges_table"),
                )
            except Exception as e:
                db_preflight = {"ok": False, "error": repr(e)}
                logger.exception("[nudges] db_preflight failed")

            for current_kind in kinds:
                counters = _init_counters()
                targets = await _pick_targets(current_kind, min_days=min_days, max_days=max_days)
                sent = 0
                checked = 0
                nudge_kind = _normalize_kind(current_kind)
                counters["targets"] = len(targets)

                logger.info(
                    "[nudges] kind=%s targets=%s dry_run=%s",
                    current_kind,
                    len(targets),
                    int(dry_run),
                )
                if debug and debug_limit > 0:
                    sample = [
                        {"user_id": int(r.get("user_id")), "tg_id": r.get("tg_id")}
                        for r in targets[: min(5, len(targets))]
                    ]
                    logger.info("[nudges] kind=%s sample_targets=%s", current_kind, sample)

                for row in targets:
                    checked += 1
                    counters["checked"] += 1
                    uid = int(row["user_id"])
                    tg_id = row.get("tg_id")
                    if tg_id is None:
                        counters["skipped_no_tg_id"] += 1
                        logger.warning("[nudges] kind=%s skip no_tg_id user_id=%s", current_kind, uid)
                        continue
                    tg_id = int(tg_id)

                    if row.get("tg_is_blocked") is True:
                        counters["skipped_blocked"] += 1
                        logger.info("[nudges] kind=%s skip blocked user_id=%s tg_id=%s", current_kind, uid, tg_id)
                        continue

                    try:
                        if await _was_sent_recently(session, uid, nudge_kind):
                            counters["skipped_already_sent"] += 1
                            logger.info(
                                "[nudges] kind=%s skip already_sent user_id=%s tg_id=%s",
                                current_kind,
                                uid,
                                tg_id,
                            )
                            continue
                    except Exception:
                        logger.exception(
                            "[nudges] kind=%s error checking sent_recently user_id=%s tg_id=%s",
                            current_kind,
                            uid,
                            tg_id,
                        )

                    access_ok = None
                    if current_kind in ("week", "month"):
                        try:
                            access_ok = await check_access(session, uid)
                        except Exception:
                            logger.exception(
                                "[nudges] kind=%s access check failed user_id=%s",
                                current_kind,
                                uid,
                            )
                            access_ok = False

                    if dry_run:
                        counters["skipped_dry_run"] += 1
                        logger.info("[nudges] kind=%s skip dry_run user_id=%s tg_id=%s", current_kind, uid, tg_id)
                        continue

                    if max_send is not None and total_sent >= int(max_send):
                        counters["skipped_max_send"] += 1
                        logger.info(
                            "[nudges] kind=%s skip max_send user_id=%s tg_id=%s max_send=%s",
                            current_kind,
                            uid,
                            tg_id,
                            max_send,
                        )
                        continue

                    nudge_text, kb = _msg_for_kind(current_kind, has_access=access_ok)
                    if not nudge_text:
                        counters["skipped_no_text"] += 1
                        logger.warning("[nudges] kind=%s skip no_text user_id=%s tg_id=%s", current_kind, uid, tg_id)
                        continue

                    payload = {
                        "kind": nudge_kind,
                        "source": "cron",
                        "segment": current_kind,
                        "has_access": access_ok,
                        "start_at": _dt_to_iso(row.get("start_at")),
                        "policy_accepted_at": _dt_to_iso(row.get("policy_accepted_at")),
                        "trial_started_at": _dt_to_iso(row.get("trial_started_at")),
                        "trial_expires_at": _dt_to_iso(row.get("trial_expires_at")),
                        "last_at": _dt_to_iso(row.get("last_at")),
                    }

                    logger.info("[nudges] kind=%s send attempt user_id=%s tg_id=%s", current_kind, uid, tg_id)
                    if debug and debug_limit > 0 and checked <= int(debug_limit):
                        logger.info(
                            "[nudges] kind=%s debug payload user_id=%s tg_id=%s payload=%s",
                            current_kind,
                            uid,
                            tg_id,
                            payload,
                        )

                    try:
                        if kb:
                            await bot.send_message(chat_id=tg_id, text=nudge_text, reply_markup=kb, disable_web_page_preview=True)
                        else:
                            await bot.send_message(chat_id=tg_id, text=nudge_text, disable_web_page_preview=True)
                        sent += 1
                        total_sent += 1
                        counters["send_ok"] += 1
                        logger.info("[nudges] kind=%s send ok user_id=%s tg_id=%s", current_kind, uid, tg_id)
                        ok = await _log_nudge(uid, tg_id, nudge_kind, payload)
                        if ok:
                            counters["db_log_ok"] += 1
                        else:
                            counters["db_log_error"] += 1
                    except Exception as e:
                        if _is_bot_blocked_error(e):
                            counters["send_blocked"] += 1
                            try:
                                await mark_user_blocked(session, uid, tg_id)
                            except Exception:
                                logger.exception("[nudges] mark blocked failed user_id=%s tg_id=%s", uid, tg_id)
                            logger.warning(
                                "[nudges] kind=%s send blocked user_id=%s tg_id=%s",
                                current_kind,
                                uid,
                                tg_id,
                            )
                            continue
                        if "chat not found" in str(e).lower():
                            counters["send_chat_not_found"] += 1
                            logger.warning(
                                "[nudges] kind=%s send chat_not_found user_id=%s tg_id=%s",
                                current_kind,
                                uid,
                                tg_id,
                            )
                            continue
                        counters["send_other_error"] += 1
                        logger.exception(
                            "[nudges] kind=%s send error user_id=%s tg_id=%s",
                            current_kind,
                            uid,
                            tg_id,
                        )
                        # не стопаем массовую рассылку — просто лог

                total_checked += checked
                _add_counters(total_counters, counters)
                results.append(
                    {
                        "kind": current_kind,
                        "checked": checked,
                        "sent": 0 if dry_run else sent,
                        "counters": counters,
                    }
                )

    if run_kind == "all":
        return {
            "kind": "all",
            "checked": total_checked,
            "sent": 0 if dry_run else total_sent,
            "counters": total_counters,
            "db_preflight": db_preflight,
            "results": results,
        }
    return {
        "kind": run_kind,
        "checked": total_checked,
        "sent": 0 if dry_run else total_sent,
        "counters": total_counters,
        "db_preflight": db_preflight,
        "results": results,
    }


@router.post("/diag")
async def nudges_diag(
    x_admin_secret: str = Header(default="", alias="X-Admin-Secret"),
    secret: Optional[str] = Query(default=None),
):
    _check_admin_secret(x_admin_secret, secret)
    out: Dict[str, Any] = {"ok": False, "preflight": None, "insert_ok": False}

    async with async_session() as s:
        try:
            res = await s.execute(
                sa_text("SELECT current_database() AS db, inet_server_addr() AS addr, now() AS now")
            )
            row = res.mappings().first() or {}
            res2 = await s.execute(
                sa_text("SELECT to_regclass('public.nudges') AS nudges_table")
            )
            row2 = res2.mappings().first() or {}
            out["preflight"] = {
                "database": row.get("db"),
                "server_addr": row.get("addr"),
                "now": row.get("now"),
                "nudges_table": row2.get("nudges_table"),
            }
            logger.info(
                "[nudges] diag preflight db=%s addr=%s now=%s nudges_table=%s",
                out["preflight"].get("database"),
                out["preflight"].get("server_addr"),
                out["preflight"].get("now"),
                out["preflight"].get("nudges_table"),
            )
        except Exception:
            logger.exception("[nudges] diag preflight failed")
            return out

        try:
            rowu = await s.execute(
                sa_text("SELECT id, tg_id FROM public.users ORDER BY id ASC LIMIT 1")
            )
            user_row = rowu.mappings().first()
            if not user_row:
                out["ok"] = True
                out["insert_ok"] = False
                out["insert_error"] = "no users found"
                return out

            uid = int(user_row["id"])
            tg_id = int(user_row["tg_id"])
            payload = json_dumps({"diag": True})

            await s.execute(
                sa_text(_NUDGE_INSERT_SQL),
                {"uid": uid, "tg": tg_id, "kind": "diag", "payload": payload},
            )
            await s.rollback()
            out["insert_ok"] = True
        except Exception as e:
            out["insert_ok"] = False
            out["insert_error"] = repr(e)
            logger.exception("[nudges] diag insert failed")
            try:
                await s.rollback()
            except Exception:
                logger.exception("[nudges] diag rollback failed")

    out["ok"] = True
    return out


@router.post("/send_one")
async def send_one(
    tg_id: int,
    kind: str = Query(pattern="^(week|month)$"),
    has_access: int = Query(1, description="1 — как будто подписка активна; 0 — как будто нет"),
    x_admin_secret: str = Header(default="", alias="X-Admin-Secret")
):
    if x_admin_secret != ADMIN_API_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is missing")

    nudge_text, kb = _msg_for_user(bool(has_access), "week" if kind == "week" else "month")

    # тут тоже контекстный менеджер — чтобы не оставалась открытая сессия
    async with Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
        try:
            if kb:
                await bot.send_message(chat_id=tg_id, text=nudge_text, reply_markup=kb, disable_web_page_preview=True)
            else:
                await bot.send_message(chat_id=tg_id, text=nudge_text, disable_web_page_preview=True)
            return {"ok": True}
        except Exception as e:
            if _is_bot_blocked_error(e):
                try:
                    async with async_session() as session:
                        r = await session.execute(
                            sa_text("SELECT id FROM public.users WHERE tg_id = :tg"),
                            {"tg": int(tg_id)},
                        )
                        uid = r.scalar()
                        if uid:
                            await mark_user_blocked(session, int(uid), int(tg_id))
                            logger.warning(
                                "[nudges] send_one blocked marked user_id=%s tg_id=%s",
                                uid,
                                tg_id,
                            )
                except Exception:
                    logger.exception("[nudges] send_one mark blocked failed tg_id=%s", tg_id)
                return {"ok": False, "error": "blocked"}
            return {"ok": False, "error": str(e)}
