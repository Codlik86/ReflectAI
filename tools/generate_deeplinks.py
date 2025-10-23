# tools/generate_deeplinks.py
import os
import asyncio
import csv
import json
import uuid
from datetime import datetime
from app.db.core import async_session

BOT_USERNAME = os.environ.get("BOT_USERNAME")
if not BOT_USERNAME:
    # пытаемся распарсить из PUBLIC_BOT_URL вида https://t.me/<username>
    pub = os.environ.get("PUBLIC_BOT_URL", "")
    if pub and "t.me/" in pub:
        BOT_USERNAME = pub.rstrip("/").split("t.me/")[-1]
if not BOT_USERNAME:
    raise RuntimeError("Set BOT_USERNAME or PUBLIC_BOT_URL in env")

# Список 16 креативов (name, text, image_url, suggested_channel)
CREATIVES = [
    # 8 тем × 2 варианта картинки — пример ниже (замени по вкусу)
    {"name": "A1_warm", "text": "Почувствовал(а) тяжесть дня? 'Помни' поможет сделать 1 шаг.", "image_url": "", "channel": "@meduzalive"},
    {"name": "A1_cold", "text": "Почувствовал(а) тяжесть дня? 'Помни' поможет сделать 1 шаг.", "image_url": "", "channel": "@thevillagemsk"},
    {"name": "A2_warm", "text": "Не знаешь, с чего начать? 1 микрошаг на 10–20 минут.", "image_url": "", "channel": "@psychologiesru"},
    {"name": "A2_cold", "text": "Не знаешь, с чего начать? 1 микрошаг на 10–20 минут.", "image_url": "", "channel": "@russiabeyond"},
    {"name": "A3_warm", "text": "Тревожно прямо сейчас? Дыхание и якорь — попробуем вместе.", "image_url": "", "channel": "@mbk_news"},
    {"name": "A3_cold", "text": "Тревожно прямо сейчас? Дыхание и якорь — попробуем вместе.", "image_url": "", "channel": "@thebell_io"},
    {"name": "A4_warm", "text": "Дневник проще вести, когда есть кто-то, кто слушает.", "image_url": "", "channel": "@postnauka"},
    {"name": "A4_cold", "text": "Дневник проще вести, когда есть кто-то, кто слушает.", "image_url": "", "channel": "@russian_embassy_ru"},
    {"name": "A5_warm", "text": "5 минут утром — и день становиться понятнее. Попробуй.", "image_url": "", "channel": "@russianexpats_de"},
    {"name": "A5_cold", "text": "5 минут утром — и день становиться понятнее. Попробуй.", "image_url": "", "channel": "@russian_community_US"},
    {"name": "A6_warm", "text": "Приватно по умолчанию. Всё остаётся у тебя.", "image_url": "", "channel": "@psychologicalhappiness"},
    {"name": "A6_cold", "text": "Приватно по умолчанию. Всё остаётся у тебя.", "image_url": "", "channel": "@expat_help"},
    {"name": "A7_warm", "text": "Основано на КПТ/АКТ/гештальт — практично и по-человечески.", "image_url": "", "channel": "@thepsychologist"},
    {"name": "A7_cold", "text": "Основано на КПТ/АКТ/гештальт — практично и по-человечески.", "image_url": "", "channel": "@russian_diary"},
    {"name": "A8_warm", "text": "Иногда достаточно, чтобы кто-то просто послушал. Я рядом.", "image_url": "", "channel": "@russian_in_israel"},
    {"name": "A8_cold", "text": "Иногда достаточно, чтобы кто-то просто послушал. Я рядом.", "image_url": "", "channel": "@russian_in_berlin"},
]

def make_code(i: int) -> str:
    # короткий уникальный код: tgads_<YYYYMMDD>_<n>_<shortuuid>
    short = uuid.uuid4().hex[:8]
    return f"tgads_{datetime.utcnow().strftime('%Y%m%d')}_{str(i).zfill(2)}_{short}"

async def main():
    out_rows = []
    async with async_session() as s:
        for i, c in enumerate(CREATIVES, start=1):
            code = make_code(i)
            deep = f"https://t.me/{BOT_USERNAME}?start={code}"
            # insert into ads
            row = await s.execute(
                "INSERT INTO ads(code, name, creative_text, image_url, channel_handle) VALUES (:code, :name, :text, :img, :ch) RETURNING id",
                {"code": code, "name": c["name"], "text": c["text"], "img": c["image_url"], "ch": c["channel"]}
            )
            ad_id = row.scalar()
            await s.execute(
                "INSERT INTO ad_links(ad_id, channel_handle, deep_link, note) VALUES (:ad_id, :ch, :deep, :note)",
                {"ad_id": ad_id, "ch": c["channel"], "deep": deep, "note": c["name"]}
            )
            out_rows.append({"ad_id": ad_id, "code": code, "deep_link": deep, "name": c["name"], "channel": c["channel"], "text": c["text"]})
        await s.commit()

    # dump CSV
    out_path = "out/ad_links.csv"
    os.makedirs("out", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ad_id", "code", "deep_link", "name", "channel", "text"])
        writer.writeheader()
        for r in out_rows:
            writer.writerow(r)
    print("Wrote", out_path)
    for r in out_rows:
        print(r["deep_link"])

if __name__ == "__main__":
    asyncio.run(main())
