# app/maintenance.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

import click
from sqlalchemy import text as sql

from app.db.core import async_session
from app.memory_summarizer import make_daily, rollup_weekly, rollup_monthly
from app.rag_summaries import delete_user_summaries


# ------------------------
# Вспомогательные утилиты
# ------------------------

async def _all_user_ids() -> List[int]:
    async with async_session() as s:
        return list((await s.execute(sql("SELECT id FROM users ORDER BY id"))).scalars().all())


# ------------------------
# Подписки: истечение срока
# ------------------------

async def expire_subscriptions_now() -> int:
    """
    Переводит в expired все активные подписки, у которых срок истёк (или не указан).
    Возвращает кол-во затронутых строк.
    """
    async with async_session() as s:
        res = await s.execute(sql("""
            UPDATE subscriptions
               SET status = 'expired',
                   updated_at = CURRENT_TIMESTAMP
             WHERE status = 'active'
               AND (subscription_until IS NULL OR subscription_until < CURRENT_TIMESTAMP)
        """))
        await s.commit()
        try:
            return int(getattr(res, "rowcount", 0) or 0)
        except Exception:
            return 0


# ------------------------
# Реализации задач (async)
# ------------------------

async def _run_daily(yesterday: bool) -> None:
    # за «вчера» по UTC — как в админ-эндпоинте
    day = (datetime.now(timezone.utc) - timedelta(days=1)) if yesterday else datetime.now(timezone.utc)
    uids = await _all_user_ids()

    for uid in uids:
        # privacy guard
        async with async_session() as s:
            pr = (await s.execute(sql("SELECT privacy_level FROM users WHERE id=:uid"), {"uid": uid})).scalar_one_or_none()
        if pr == "none":
            continue
        await make_daily(uid, day)


async def _run_weekly(week_start_utc: str | None) -> None:
    if week_start_utc:
        start = datetime.fromisoformat(week_start_utc).replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    uids = await _all_user_ids()
    # один сеанс на батч
    async with async_session() as s:
        for uid in uids:
            # ВАЖНО: передаём session вторым аргументом
            await rollup_weekly(uid, s, start)


async def _run_monthly(month_start_utc: str | None) -> None:
    if month_start_utc:
        start = datetime.fromisoformat(month_start_utc).replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    uids = await _all_user_ids()
    async with async_session() as s:
        for uid in uids:
            # ВАЖНО: передаём session вторым аргументом
            await rollup_monthly(uid, s, start)


async def _run_purge_user(user_id: int) -> None:
    async with async_session() as s:
        await s.execute(sql("DELETE FROM dialog_summaries WHERE user_id=:uid"), {"uid": user_id})
        await s.commit()
    await delete_user_summaries(user_id)


# ------------------------
# CLI (sync-обёртки для click)
# ------------------------

@click.group()
def cli() -> None:
    """Утилиты обслуживания: саммари/роллапы/очистка/подписки."""
    pass


@cli.command("daily")
@click.option("--yesterday", is_flag=True, default=False, help="Сводка за вчера (UTC)")
def cmd_daily(yesterday: bool) -> None:
    """Сделать daily-саммари для всех пользователей."""
    asyncio.run(_run_daily(yesterday))


@cli.command("weekly")
@click.option("--week-start-utc", required=False, help="YYYY-MM-DD (понедельник UTC)")
def cmd_weekly(week_start_utc: str | None) -> None:
    """Собрать weekly-саммари (роллап из daily) для всех пользователей."""
    asyncio.run(_run_weekly(week_start_utc))


@cli.command("topic")
@click.option("--month-start-utc", required=False, help="YYYY-MM-01 UTC")
def cmd_topic(month_start_utc: str | None) -> None:
    """Собрать topic (месячные тематические) саммари для всех пользователей."""
    asyncio.run(_run_monthly(month_start_utc))


@cli.command("purge-user")
@click.argument("user_id", type=int)
def cmd_purge_user(user_id: int) -> None:
    """Удалить ВСЕ саммари пользователя (БД + векторы Qdrant)."""
    asyncio.run(_run_purge_user(user_id))


@cli.command("expire-subscriptions")
def cmd_expire_subscriptions() -> None:
    """Перевести просроченные активные подписки в expired."""
    n = asyncio.run(expire_subscriptions_now())
    print(f"Expired {n} subscriptions")


def main() -> None:
    # click-приложение само синхронное
    cli()


if __name__ == "__main__":
    main()
