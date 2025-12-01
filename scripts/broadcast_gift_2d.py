# scripts/broadcast_gift_2d.py
import os
import asyncio
from dotenv import load_dotenv

# --- Автозагрузка .env ---
load_dotenv()

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy import text


# --- Переменные из .env ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # так называется у тебя
DATABASE_URL = os.getenv("DATABASE_URL")      # так называется у тебя

GIFT_DAYS = 2
GIFT_PLAN = "gift2d"
GIFT_TIER = "gift"
GIFT_STATUS = "active"

MESSAGE = (
    "Привет! Несколько дней бот был недоступен. "
    f"Дарю всем **+{GIFT_DAYS} дня доступа** сверху — без условий и автосписаний. "
    "Спасибо, что остаёшься ❤️"
)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty — TELEGRAM_BOT_TOKEN не найден")

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is empty — DATABASE_URL не найден")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # --- 1) Получаем пользователей ---
    async with Session() as session:
        rows = (
            await session.execute(
                text("SELECT id, tg_id FROM users WHERE tg_id IS NOT NULL")
            )
        ).all()

        user_ids = [r[0] for r in rows]
        chat_ids = [r[1] for r in rows]

        if not user_ids:
            print("Нет пользователей с tg_id — завершено.")
            return

        print(f"Найдено пользователей: {len(user_ids)}")

        # --- 2) Продлеваем существующие подписки (+2 дня) ---
        await session.execute(
            text(
                """
                UPDATE subscriptions AS s
                SET subscription_until =
                    GREATEST(COALESCE(s.subscription_until, NOW()), NOW())
                    + (:gift_days * INTERVAL '1 day')
                WHERE s.user_id = ANY(:user_ids)
                """
            ),
            {"gift_days": GIFT_DAYS, "user_ids": user_ids},
        )

        # --- 3) Создаем подарочную подписку тем, у кого её нет ---
        # Важно: в таблице subscriptions есть NOT NULL column tier,
        # поэтому обязательно задаём её (например, 'gift'),
        # а также ставим status='active'.
        await session.execute(
            text(
                """
                INSERT INTO subscriptions (
                    user_id,
                    tier,
                    plan,
                    status,
                    is_auto_renew,
                    subscription_until
                )
                SELECT u.id,
                       :gift_tier,
                       :gift_plan,
                       :gift_status,
                       FALSE,
                       NOW() + (:gift_days * INTERVAL '1 day')
                FROM users u
                WHERE u.id = ANY(:user_ids)
                  AND NOT EXISTS (
                      SELECT 1 FROM subscriptions s WHERE s.user_id = u.id
                  )
                """
            ),
            {
                "gift_days": GIFT_DAYS,
                "gift_plan": GIFT_PLAN,
                "gift_tier": GIFT_TIER,
                "gift_status": GIFT_STATUS,
                "user_ids": user_ids,
            },
        )

        # --- 4) Обновляем статус users.subscription_status ---
        await session.execute(
            text(
                "UPDATE users SET subscription_status = 'active' WHERE id = ANY(:user_ids)"
            ),
            {"user_ids": user_ids},
        )

        await session.commit()

    print("Подписки обновлены. Начинаю рассылку...")

    # --- 5) Рассылка сообщений ---
    sem = asyncio.Semaphore(25)

    async def send_safe(chat_id: int):
        async with sem:
            for attempt in range(5):
                try:
                    await bot.send_message(
                        chat_id,
                        MESSAGE,
                        disable_web_page_preview=True,
                    )
                    return
                except Exception as e:
                    try:
                        from aiogram.exceptions import TelegramRetryAfter
                        if isinstance(e, TelegramRetryAfter):
                            await asyncio.sleep(e.retry_after + 0.5)
                            continue
                    except Exception:
                        pass
                    await asyncio.sleep(1 + attempt * 2)
                    if attempt == 4:
                        print(f"Пропущен {chat_id}: {e!r}")

    BATCH = 1000
    for i in range(0, len(chat_ids), BATCH):
        batch = chat_ids[i:i + BATCH]
        tasks = [asyncio.create_task(send_safe(cid)) for cid in batch]
        await asyncio.gather(*tasks)
        print(
            f"Отправлено {min(i + BATCH, len(chat_ids))}/{len(chat_ids)}"
        )

    await bot.session.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
