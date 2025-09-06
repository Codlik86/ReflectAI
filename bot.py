# bot.py — версия под новый OpenAI SDK (>=1.x) и модель gpt-4o-mini.
# Что внутри:
# - Новый клиент: from openai import OpenAI
# - Быстрая команда /ping
# - Таймауты и ретраи при вызове OpenAI
# - Flask health endpoint (Render требует открытый порт)
# - Telegram polling в отдельном потоке
# - (Опционально) простая RAG-подстановка, если есть embeddings_index.json

import os
import time
import json
import threading
import logging
from datetime import datetime

from flask import Flask, jsonify
from dotenv import load_dotenv
import telebot
import numpy as np

from openai import OpenAI
from openai import APIError, RateLimitError, APITimeoutError  # для логики ретраев

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", "8080"))

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN и OPENAI_API_KEY должны быть заданы в переменных окружения")

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reflect-bot")

# --- OpenAI клиент (новый SDK) ---
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Telegram-бот ---
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# --- Кризисные триггеры (минимальный набор) ---
CRISIS_KEYWORDS = [
    "суицид", "убью", "хочу умереть", "убить себя", "покончу", "не хочу жить",
    "suicide", "kill myself"
]

def is_crisis(text: str) -> bool:
    txt = (text or "").lower()
    return any(kw in txt for kw in CRISIS_KEYWORDS)

# --- Косинусная близость (без scikit-learn) ---
def cosine_similarity_np(a, b):
    """
    a: ndarray shape (n, d)
    b: ndarray shape (m, d)
    -> returns (n, m) косинусных схожестей
    """
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    a /= (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b /= (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return a @ b.T

# --- RAG: загрузка локального индекса (если он есть) ---
EMBEDDINGS_FILE = "embeddings_index.json"
EMB_CACHE = []

def load_embeddings_index():
    global EMB_CACHE
    if os.path.exists(EMBEDDINGS_FILE):
        with open(EMBEDDINGS_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        for it in items:
            it["embedding"] = np.array(it["embedding"], dtype=np.float32)
        EMB_CACHE = items
        logger.info(f"RAG: загружено {len(EMB_CACHE)} документов")
    else:
        logger.info("RAG: локальный индекс не найден (embeddings_index.json) — работаем без контекста")

def embed_text_newsdk(text: str):
    # Новый SDK: client.embeddings.create
    r = client.embeddings.create(model="text-embedding-3-small", input=text)
    return np.array(r.data[0].embedding, dtype=np.float32)

def rag_retrieve(question: str, top_k: int = 3):
    if not EMB_CACHE:
        return []
    q_vec = embed_text_newsdk(question)
    mat = np.vstack([it["embedding"] for it in EMB_CACHE])
    sims = cosine_similarity_np(np.asarray([q_vec]), mat)[0]
    idxs = np.argsort(-sims)[:top_k]
    out = []
    for i in idxs:
        out.append({"score": float(sims[i]), "text": EMB_CACHE[int(i)]["text"]})
    return out

# --- Вызов OpenAI с ретраями и логированием времени ---
def call_openai_chat(messages, model="gpt-4o-mini", max_tokens=350, temperature=0.7, timeout=25, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            dt = time.time() - t0
            logger.info(f"OpenAI latency: {dt:.2f}s (attempt={attempt})")
            return resp.choices[0].message.content.strip()
        except (APITimeoutError, RateLimitError, APIError) as e:
            last_err = e
            logger.warning(f"OpenAI error ({type(e).__name__}): {e}. Retry in {(attempt+1)*1.5:.1f}s")
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = e
            logger.exception("OpenAI unknown error")
            break
    raise last_err

def build_messages(user_text: str, rag_snippets=None):
    system_prompt = (
        "You are a compassionate reflective assistant named Reflect AI. "
        "Use active listening and short evidence-based exercises (CBT/ACT/DBT). "
        "Do not provide medical diagnoses. Keep answers short (3–6 sentences) and kind."
    )
    messages = [{"role": "system", "content": system_prompt}]
    if rag_snippets:
        ctx = "\n\n".join([f"Context {i+1}: {c['text']}" for i, c in enumerate(rag_snippets)])
        messages.append({"role": "system", "content": f"Use this context when helpful:\n{ctx}"})
    messages.append({"role": "user", "content": user_text})
    return messages

# --- Telegram handlers ---
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    bot.reply_to(
        message,
        "Привет! Я Reflect AI — бот для рефлексии и мягкой поддержки.\n"
        "Напиши, что тревожит, и я помогу разложить мысли и предложу короткое упражнение.\n"
        "Команды: /ping — проверить скорость."
    )

@bot.message_handler(commands=['ping'])
def handle_ping(message):
    bot.reply_to(message, "pong")

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

    # (опционально) RAG-поиск
    try:
        contexts = rag_retrieve(text, top_k=3)
    except Exception:
        contexts = []

    try:
        reply = call_openai_chat(
            build_messages(text, rag_snippets=contexts),
            model="gpt-4o-mini",
            max_tokens=320,
            temperature=0.7,
            timeout=25,
            retries=2
        )
    except Exception:
        reply = "Похоже, внешняя модель сейчас занята. Давай попробуем ещё раз через минуту."

    bot.reply_to(message, reply)

# --- Flask health endpoint (Render Web Service должен слушать порт) ---
app = Flask(__name__)

@app.route("/")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat(), "model": "gpt-4o-mini"})

def run_polling():
    # Более стабильные таймауты для long-poll
    bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True, allowed_updates=['message'])

if __name__ == "__main__":
    # Загружаем локальный RAG-индекс (если есть)
    load_embeddings_index()

    # Запускаем Telegram polling в фоне
    t = threading.Thread(target=run_polling, daemon=True)
    t.start()

    # Поднимаем Flask, чтобы Render «видел» открытый порт
    app.run(host="0.0.0.0", port=PORT)
