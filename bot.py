# bot.py — рабочая версия для Render Web Service:
# - OpenAI SDK в стиле 0.28.1 (import openai; ChatCompletion.create/Embedding.create)
# - Flask health endpoint слушает PORT (Render требует открытый порт)
# - Telegram polling запускается в отдельном потоке

import os
import threading
from datetime import datetime

from flask import Flask, jsonify
from dotenv import load_dotenv
import telebot
import openai

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")          # обязательно задать в Render → Environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")          # обязательно задать в Render → Environment
PORT = int(os.getenv("PORT", "8080"))                 # Render сам подставит PORT

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN и OPENAI_API_KEY должны быть заданы в переменных окружения")

# Инициализация OpenAI (старый SDK 0.28.1)
openai.api_key = OPENAI_API_KEY

# Инициализация Telegram-бота
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# --- Примитивная безопасность: набор «красных фраз» (минимум) ---
CRISIS_KEYWORDS = [
    "суицид", "убью", "хочу умереть", "убить себя", "покончу", "не хочу жить",
    "suicide", "kill myself"
]

def is_crisis(text: str) -> bool:
    txt = (text or "").lower()
    return any(kw in txt for kw in CRISIS_KEYWORDS)

# --- Вызовы OpenAI (старый стиль) ---
def chat_with_openai(user_text: str) -> str:
    system_prompt = (
        "You are a compassionate reflective assistant named Reflect AI. "
        "Use active listening and short evidence-based exercises (CBT/ACT/DBT). "
        "Do not provide medical diagnoses. Keep answers short (3–6 sentences) and kind."
    )

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            max_tokens=400,
            temperature=0.7,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return "Похоже, внешняя модель недоступна. Попробуй ещё раз чуть позже."

# --- Telegram handlers ---
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    bot.reply_to(
        message,
        "Привет! Я Reflect AI — бот для рефлексии и мягкой поддержки.\n"
        "Напиши, что тревожит, и я помогу разложить мысли и предложу короткое упражнение."
    )

@bot.message_handler(func=lambda m: True)
def handle_all(message):
    text = message.text or ""
    if is_crisis(text):
        bot.reply_to(
            message,
            "Мне очень жаль, что так тяжело. Если ты в опасности — обратись в экстренные службы. "
            "Горячая линия в РФ: 8-800-200-122. Если ты в другой стране — напиши, я подскажу локальные номера."
        )
        return

    reply = chat_with_openai(text)
    bot.reply_to(message, reply)

# --- Flask health endpoint (Render Web Service должен слушать порт) ---
app = Flask(__name__)

@app.route("/")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})

def run_polling():
    # бесконечный polling телеграма в отдельном потоке
    bot.infinity_polling()

if __name__ == "__main__":
    # запускаем polling в фоне
    t = threading.Thread(target=run_polling, daemon=True)
    t.start()

    # поднимаем Flask на PORT, чтобы Render «видел» открытый порт
    # хост 0.0.0.0 ОБЯЗАТЕЛЕН на Render
    app.run(host="0.0.0.0", port=PORT)
