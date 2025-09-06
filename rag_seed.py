# rag_seed.py — создаёт локальный индекс embeddings_index.json из текстов в data/corpus
# Использует новый SDK OpenAI (>=1.x)

import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CORPUS_FOLDER = os.getenv("RAG_FOLDER", "data/corpus")
OUT_FILE = "embeddings_index.json"

client = OpenAI(api_key=OPENAI_API_KEY)

def get_texts(folder: str):
    items = []
    if not os.path.exists(folder):
        print(f"Создай папку {folder} и положи туда .txt файлы")
        return items
    for fname in os.listdir(folder):
        if not fname.lower().endswith(".txt"):
            continue
        path = os.path.join(folder, fname)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            items.append({"id": fname, "text": text})
    return items

def embed_texts(items):
    out = []
    for it in items:
        resp = client.embeddings.create(model="text-embedding-3-small", input=it["text"])
        vec = resp.data[0].embedding
        out.append({"id": it["id"], "text": it["text"], "embedding": vec})
    return out

if __name__ == "__main__":
    docs = get_texts(CORPUS_FOLDER)
    print(f"Найдено файлов: {len(docs)}")
    if not docs:
        exit(1)

    idx = embed_texts(docs)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    print(f"✅ Индекс сохранён в {OUT_FILE}")
