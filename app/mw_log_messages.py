from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy import text as sql
from datetime import datetime, timezone
from app.db.core import async_session

class LogUserMessagesMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        # Пишем только входящие текстовые сообщения пользователей
        if isinstance(event, Message) and event.text and event.from_user:
            tg_id = event.from_user.id
            text  = event.text
            async with async_session() as s:
                row = (await s.execute(
                    sql("SELECT id, privacy_level FROM users WHERE tg_id=:tg"),
                    {"tg": tg_id}
                )).mappings().first()
                if row and row["privacy_level"] != "none":
                    await s.execute(sql("""
                        INSERT INTO bot_messages (user_id, role, text, created_at)
                        VALUES (:uid, 'user', :txt, :ts)
                    """), {
                        "uid": row["id"],
                        "txt": text,
                        "ts": datetime.now(timezone.utc)
                    })
                    await s.commit()
        return await handler(event, data)
