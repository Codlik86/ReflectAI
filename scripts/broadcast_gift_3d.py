# scripts/broadcast_gift_3d.py
import os, asyncio
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

def _env_clean(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip().strip('"').strip("'")
    return default

BOT_TOKEN = _env_clean("BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
DATABASE_URL = _env_clean("DATABASE_URL")

MESSAGE = (
    "Привет! Несколько дней бот был недоступен. "
    "Дарю всем **+3 дня доступа** сверху — без условий и автосписаний. "
    "Спасибо, что остаёшься ❤️"
)

def _is_bot_blocked_error(err: Exception) -> bool:
    if not isinstance(err, TelegramForbiddenError):
        return False
    return "bot was blocked by the user" in str(err).lower()

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is empty")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with Session() as session:
        rows = (
            await session.execute(
                text("SELECT id, tg_id FROM users WHERE tg_id IS NOT NULL AND tg_is_blocked IS NOT TRUE")
            )
        ).all()
        user_id_by_tg = {int(r[1]): int(r[0]) for r in rows}
        ids = [r[1] for r in rows]

    print(f"Sending to {len(ids)} users...")
    import asyncio
    sem = asyncio.Semaphore(25)

    async def mark_blocked(tg_id: int) -> None:
        uid = user_id_by_tg.get(int(tg_id))
        if not uid:
            return
        try:
            async with Session() as s:
                await s.execute(
                    text(
                        """
                        UPDATE users
                        SET tg_is_blocked = TRUE,
                            tg_blocked_at = NOW()
                        WHERE id = :uid AND tg_id = :tg
                        """
                    ),
                    {"uid": int(uid), "tg": int(tg_id)},
                )
                await s.commit()
            print(f"[tg] user blocked bot; marked blocked user_id={uid} tg_id={tg_id}")
        except Exception:
            pass

    async def send_safe(chat_id: int):
        async with sem:
            for attempt in range(5):
                try:
                    await bot.send_message(chat_id, MESSAGE, disable_web_page_preview=True)
                    return
                except Exception as e:
                    if _is_bot_blocked_error(e):
                        await mark_blocked(int(chat_id))
                        return
                    try:
                        from aiogram.exceptions import TelegramRetryAfter
                        if isinstance(e, TelegramRetryAfter):
                            await asyncio.sleep(e.retry_after + 0.5)
                            continue
                    except Exception:
                        pass
                    await asyncio.sleep(1 + attempt * 2)
                    if attempt == 4:
                        print(f"skip {chat_id}: {e}")

    BATCH = 1000
    for i in range(0, len(ids), BATCH):
        tasks = [asyncio.create_task(send_safe(cid)) for cid in ids[i:i+BATCH]]
        await asyncio.gather(*tasks)

    await bot.session.close()
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
