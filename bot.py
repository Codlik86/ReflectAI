# bot.py
import os
import threading
import time
import sqlite3
import json
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv
import telebot
import numpy as np
from openai import OpenAI

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
RAG_FOLDER = os.getenv("RAG_FOLDER", "data/corpus")
PORT = int(os.getenv("PORT", 8080))
PROXY_BASE_URL = os.getenv("OPENAI_PROXY_URL", "https://api.proxyapi.ru/openai/v1")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise Exception("TELEGRAM_TOKEN and OPENAI_API_KEY must be set in .env")

# --- Инициализация OpenAI через прокси ---
client = OpenAI(api_key=OPENAI_API_KEY, base_url=PROXY_BASE_URL)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# --- Simple local storage for insights (sqlite) ---
DB_FILE = "insights.db"
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS insights
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, created_at TEXT)''')
    conn.commit()
    conn.close()

def save_insight(user_id, text):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO insights (user_id, text, created_at) VALUES (?, ?, ?)", (user_id, text, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

# --- Simple crisis detection ---
CRISIS_KEYWORDS = ["суицид", "убью", "хочу умереть", "убить себя", "покончу", "не хочу жить", "kill myself", "suicide"]
def check_crisis(text):
    txt = text.lower()
    for kw in CRISIS_KEYWORDS:
        if kw in txt:
            return True
    return False

# --- Load embeddings from disk ---
EMBEDDINGS_FILE = "embeddings_index.json"

def load_embeddings_index(path=EMBEDDINGS_FILE):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    embeddings_index = {}
    for filename, emb_list in data.items():
        embeddings_index[filename] = np.array(emb_list, dtype=np.float32)
    return embeddings_index

EMBEDDINGS_INDEX = load_embeddings_index()

# --- Embedding a single text ---
def embed_text(text):
    resp = openai.Embedding.create(
        model="text-embedding-3-small",
        input=text
    )
    vec = resp["data"][0]["embedding"]
    return np.array(vec, dtype=np.float32)

# --- Cosine similarity ---
def cosine_similarity(vec1, vec2):
    dot_product = np.dot(vec1, vec2)
    norm_vec1 = np.linalg.norm(vec1)
    norm_vec2 = np.linalg.norm(vec2)
    return dot_product / (norm_vec1 * norm_vec2)

# --- RAG retrieval ---
def rag_retrieve(user_question, top_k=3):
    if not EMBEDDINGS_INDEX:
        return []
    q_vec = embed_text(user_question)
    
    similarities = []
    for filename, emb in EMBEDDINGS_INDEX.items():
        sim = cosine_similarity(q_vec, emb)
        similarities.append((filename, sim))
    
    # Сортировка и выбор top_k
    similarities.sort(key=lambda x: x[1], reverse=True)
    results = []
    for filename, score in similarities[:top_k]:
        results.append({"score": float(score), "text": open(os.path.join(RAG_FOLDER, filename), "r", encoding="utf-8").read()})
    return results

# --- Chat with OpenAI using optional RAG context ---
def chat_with_openai(user_text, rag_contexts=None):
    system_prompt = (
        "You are a compassionate reflective assistant named Reflect AI. "
        "You are trained to help with reflective journaling, active listening, and "
        "provide short evidence-based exercises (based on CBT/ACT/DBT). "
        "You must never provide medical diagnoses. If the user expresses imminent danger or suicidal intent, "
        "give a crisis response and provide local emergency contacts. Keep answers short (3-6 sentences) and kind."
    )
    messages = [{"role": "system", "content": system_prompt}]
    if rag_contexts:
        context_text = "\n\n".join([f"Context snippet ({i+1}): {c['text']}" for i, c in enumerate(rag_contexts)])
        context_message = f"Use the following retrieved documents to inform your response when helpful:\n{context_text}"
        messages.append({"role": "system", "content": context_message})
    messages.append({"role": "user", "content": user_text})

    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=450,
        temperature=0.7,
    )
    reply = resp["choices"][0]["message"]["content"].strip()
    return reply

# --- Telegram handlers ---
@bot.message_handler(commands=['start','help'])
def handle_start(message):
    text = ("Привет! Я Reflect AI — бот для рефлексии. "
            "Напиши о том, что тебя беспокоит, и я помогу разложить мысли и предложу короткие упражнения. "
            "Команды: /save — сохранить текущее сообщение как инсайт, /help — помощь.")
    bot.reply_to(message, text)

@bot.message_handler(commands=['save'])
def handle_save(message):
    user_id = message.from_user.id
    bot.reply_to(message, "Отправь текст, который нужно сохранить как инсайт (просто пришли сообщение).")

@bot.message_handler(func=lambda m: True)
def handle_all(message):
    user_text = message.text or ""
    user_id = message.from_user.id

    if check_crisis(user_text):
        bot.reply_to(message,
            "Мне очень жаль, что так тяжело. Если ты в опасности — пожалуйста, обратись в экстренные службы. "
            "Российская горячая линия: 8-800-200-122 (если не в РФ, напиши страну, я подскажу локальные номера). "
            "Если хочешь, я могу связать тебя с профессионалом или дать пошаговую технику стабилизации.")
        return

    if user_text.lower().startswith("save:") or user_text.lower().startswith("сохранить:"):
        cleaned = user_text.split(":",1)[1].strip() if ":" in user_text else user_text
        save_insight(user_id, cleaned)
        bot.reply_to(message, "Инсайт сохранён ✅")
        return

    try:
        contexts = rag_retrieve(user_text, top_k=3)
    except Exception as e:
        contexts = []

    try:
        reply = chat_with_openai(user_text, rag_contexts=contexts)
    except Exception as e:
        reply = "Произошла ошибка с внешней моделью. Попробуй чуть позже."

    bot.reply_to(message, reply)

# --- Flask health endpoint ---
@app.route("/")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})

def start_bot_polling():
    print("Starting Telegram polling thread...")
    bot.infinity_polling()

def start_background():
    init_db()
    t = threading.Thread(target=start_bot_polling, daemon=True)
    t.start()

start_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
