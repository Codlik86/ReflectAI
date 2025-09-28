from __future__ import annotations
from sqlalchemy import text
from app.db.core import async_session

async def expire_subscriptions_now() -> int:
    """
    Переводит в expired все активные подписки, у которых срок истёк (или не указан).
    Возвращает кол-во затронутых строк.
    """
    async with async_session() as s:
        res = await s.execute(text("""
            UPDATE subscriptions
               SET status = 'expired',
                   updated_at = CURRENT_TIMESTAMP
             WHERE status = 'active'
               AND (subscription_until IS NULL OR subscription_until < CURRENT_TIMESTAMP)
        """))
        await s.commit()
        # psycopg3 + SQLA: rowcount местами может быть -1; нормализуем
        try:
            return int(getattr(res, "rowcount", 0) or 0)
        except Exception:
            return 0
