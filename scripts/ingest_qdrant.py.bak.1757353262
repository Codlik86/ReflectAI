# scripts/ingest_qdrant.py
import os, json, glob, re, uuid, asyncio
from typing import List, Dict, Any, Iterable
import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointStruct
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

CHUNK_SIZE = 800      # размер куска символов
CHUNK_OVERLAP = 200   # перекрытие
BATCH = 64            # батч для аплоада

def split_text(text: str, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i : i + size]
        chunks.append(chunk)
        i += size - overlap
        if i < 0:
            break
    return chunks

async def embed_many(texts: List[str]) -> List[List[float]]:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    payload = {"model": EMBED_MODEL, "input": texts}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OPENAI_BASE}/embeddings", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return [item["embedding"] for item in data["data"]]

def iter_corpus() -> Iterable[Dict[str, Any]]:
    """
    Источники:
      1) Все .txt из data/ → режем на чанки
      2) embeddings_index.json (если есть) → используем готовые эмбеддинги или текст
         Поддерживает 2 формата:
           - {"items": [ ... ]}
           - [ ... ]
         Допустимые поля для элемента:
           text|content, embedding|vector, title|name, source|url|path|file
    """
    # 1) TXT-файлы
    for path in glob.glob("data/**/*.txt", recursive=True) + glob.glob("data/*.txt"):
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        title = os.path.basename(path)
        for part in split_text(txt):
            yield {"text": part, "title": title, "source": path}

    # 2) embeddings_index.json (если есть)
    if os.path.exists("embeddings_index.json"):
        with open("embeddings_index.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        # Нормализуем корневую структуру
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        for raw in items:
            # Нормализация полей
            text = raw.get("text") if isinstance(raw, dict) else None
            if not text and isinstance(raw, dict):
                text = raw.get("content")

            vec = None
            if isinstance(raw, dict):
                vec = raw.get("embedding")
                if vec is None:
                    vec = raw.get("vector")

            title = None
            if isinstance(raw, dict):
                title = raw.get("title") or raw.get("name")

            source = None
            if isinstance(raw, dict):
                source = raw.get("source") or raw.get("url") or raw.get("path") or raw.get("file")

            # Отдаём:
            # - если есть готовый вектор — грузим без перерасчёта
            # - если вектора нет, но есть текст — посчитаем эмбеддинг в общем потоке
            if isinstance(vec, list) and text:
                yield {"text": text, "title": title, "source": source, "embedding": vec}
            elif isinstance(text, str) and text.strip():
                yield {"text": text, "title": title, "source": source}
            else:
                # пропускаем некорректный элемент
                continue

async def main():
    from app.qdrant_client import get_client, ensure_collection
    ensure_collection()
    client: QdrantClient = get_client()

    buffer_texts: List[str] = []
    buffer_payloads: List[Dict[str, Any]] = []
    points_batch: List[PointStruct] = []

    async def flush_with_embeddings():
        nonlocal buffer_texts, buffer_payloads, points_batch
        if not buffer_texts:
            return
        vectors = await embed_many(buffer_texts)
        for vec, payload in zip(vectors, buffer_payloads):
            pid = str(uuid.uuid4())
            points_batch.append(PointStruct(
                id=pid, vector=vec, payload=payload
            ))
        buffer_texts, buffer_payloads = [], []

        if len(points_batch) >= BATCH:
            client.upsert(collection_name=QDRANT_COLLECTION, points=points_batch)
            points_batch = []

    async def flush_points_only():
        nonlocal points_batch
        if points_batch:
            client.upsert(collection_name=QDRANT_COLLECTION, points=points_batch)
            points_batch = []

    # Идём по корпусу: для частей без готовых векторов — считаем; с готовыми — грузим сразу
    for doc in iter_corpus():
        payload = {"text": doc["text"], "title": doc.get("title"), "source": doc.get("source")}
        if "embedding" in doc and isinstance(doc["embedding"], list):
            points_batch.append(PointStruct(id=str(uuid.uuid4()), vector=doc["embedding"], payload=payload))
            if len(points_batch) >= BATCH:
                client.upsert(collection_name=QDRANT_COLLECTION, points=points_batch)
                points_batch = []
        else:
            buffer_texts.append(doc["text"])
            buffer_payloads.append(payload)
            if len(buffer_texts) >= 128:  # крупнее батч на /embeddings = меньше overhead
                await flush_with_embeddings()

    await flush_with_embeddings()
    await flush_points_only()
    print("Ingest finished ✅")

if __name__ == "__main__":
    asyncio.run(main())
