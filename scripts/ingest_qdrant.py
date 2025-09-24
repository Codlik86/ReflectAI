# scripts/ingest_qdrant.py
# -*- coding: utf-8 -*-
import os, json, glob, re, uuid, asyncio
from typing import List, Dict, Any, Iterable, Tuple, Optional

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointStruct
from dotenv import load_dotenv

# ensure project root on sys.path when run as a file
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv()

# ---------- Qdrant / Embeddings config ----------
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus_v2")  # согласовано с рантаймом

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
# принимаем EMBED_MODEL (как в рантайме), либо старое имя OPENAI_EMBED_MODEL
EMBED_MODEL = os.getenv("EMBED_MODEL") or os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# ---------- Chunking / batching ----------
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1200))          # рекомендовано 900–1200
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 180))      # 150–200
BATCH = int(os.getenv("QDRANT_UPSERT_BATCH", 64))

# где лежит корпус: по умолчанию corpus/, но читаем и data/*.txt для обратной совместимости
CORPUS_DIR = os.getenv("CORPUS_DIR", "corpus")

# ---------- bilingual tags ----------
TAG_BILINGUAL: Dict[str, List[str]] = {
    "breathing": ["дыхание"],
    "cognitive_restructuring": ["когнитивная_работа", "рефрейминг"],
    "behavioural_activation": ["поведенческая_активация", "ба"],
    "problem_solving": ["решение_проблем"],
    "psychoeducation": ["психообразование", "ожидания", "структура_сессии"],
    "exposure": ["экспозиция"],
    "grounding": ["заземление"],
    "values": ["ценности"],
    "self_compassion": ["доброта_к_себе", "самосострадание"],
    "defusion": ["дефузия", "отцепиться_от_мысли"],
    "micro_practice": ["микро_практика"],
    "stress_coping": ["стресс", "стресс_копинг"],
    "homework": ["домашка"],
    "expectations": ["ожидания"],
    "session_structure": ["структура_сессии"],
    "socratic_questioning": ["сократические_вопросы"],
    "cognitive_model": ["когнитивная_модель"],
    "behavioral_experiments": ["поведенческие_эксперименты"],
    "relapse_prevention": ["профилактика_отката"],
    "self_help": ["самопомощь"],
}

def expand_tags_bilingual(raw: List[str]) -> List[str]:
    out = set()
    for t in raw or []:
        slug = t.lower().strip().replace(" ", "_")
        if not slug:
            continue
        out.add(slug)
        if slug in TAG_BILINGUAL:
            out.update(TAG_BILINGUAL[slug])
        for en, ru_list in TAG_BILINGUAL.items():
            if slug in ru_list:
                out.add(en)
    return sorted(out)

def parse_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    meta: Dict[str, str] = {}
    body_lines: List[str] = []
    in_header = True
    for line in text.splitlines():
        if in_header:
            m = re.match(r'^\s*#\s*([A-Za-z_]+)\s*:\s*(.+?)\s*$', line)
            if m:
                key, val = m.group(1).lower(), m.group(2).strip()
                meta[key] = val
                continue
            in_header = False
        body_lines.append(line)
    return meta, "\n".join(body_lines).strip()

def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Бережное чанкование текста: стараемся резать по пустой строке/строке/пробелу.
    """
    text = text.strip()
    n = len(text)
    if n == 0:
        return []
    if n <= size:
        return [text]

    chunks: List[str] = []
    i = 0
    while i < n:
        end = min(i + size, n)
        lookahead = min(n, end + 200)
        slice_ = text[i:lookahead]

        cut_rel = slice_.rfind("\n\n")
        if cut_rel == -1:
            cut_rel = slice_.rfind("\n")
        if cut_rel == -1:
            cut_rel = slice_.rfind(" ")
        cut = i + cut_rel if cut_rel != -1 and (i + cut_rel) > i + int(size * 0.5) else end

        chunk = text[i:cut].strip()
        if chunk:
            chunks.append(chunk)

        new_i = cut - overlap
        if new_i <= i:
            new_i = i + size  # защита от залипания
        i = max(0, new_i)

    return chunks

# ---------- Embeddings (unified with runtime) ----------
# 1) пробуем взять тот же embed, что использует рантайм
_RUNTIME_EMBED = None
try:
    from app.rag_qdrant import embed as _runtime_embed_single  # async (text)->vector
    async def _runtime_embed_many(texts: List[str]) -> List[List[float]]:
        # последовательный вызов (стабильнее для прокси)
        out: List[List[float]] = []
        for t in texts:
            out.append(await _runtime_embed_single(t))
        return out
    _RUNTIME_EMBED = _runtime_embed_many
except Exception:
    _RUNTIME_EMBED = None

async def _openai_embed_many(texts: List[str]) -> List[List[float]]:
    """
    Fallback-эмбеддер через OpenAI /embeddings с экспоненциальными ретраями.
    """
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing and runtime embedder is unavailable")

    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    payload = {"model": EMBED_MODEL, "input": texts}

    backoff = 1.0
    last_err: Optional[Exception] = None
    for attempt in range(6):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(f"{OPENAI_BASE.rstrip('/')}/embeddings", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                return [item["embedding"] for item in data["data"]]
        except (httpx.HTTPError, httpx.ReadError) as e:
            last_err = e
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 15.0)
        except Exception as e:
            last_err = e
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 15.0)

    raise RuntimeError(f"OpenAI embeddings failed after retries: {last_err}")

async def embed_many(texts: List[str]) -> List[List[float]]:
    if _RUNTIME_EMBED is not None:
        return await _RUNTIME_EMBED(texts)
    return await _openai_embed_many(texts)

def iter_corpus() -> Iterable[Dict[str, Any]]:
    """
    Источники:
      1) Все .txt из CORPUS_DIR/ и data/ (для совместимости)
         → парсим шапку (# key: value), режем ТОЛЬКО тело на чанки.
      2) embeddings_index.json (если есть) → используем готовые эмбеддинги или текст.
    """
    txt_paths = set()
    # новый путь
    txt_paths.update(glob.glob(f"{CORPUS_DIR}/**/*.txt", recursive=True))
    txt_paths.update(glob.glob(f"{CORPUS_DIR}/*.txt", recursive=False))
    # старый путь
    txt_paths.update(glob.glob("data/**/*.txt", recursive=True))
    txt_paths.update(glob.glob("data/*.txt", recursive=False))

    for path in sorted(txt_paths):
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()

        meta, body = parse_front_matter(raw)
        source = meta.get("source") or path
        title = meta.get("title") or os.path.basename(path)
        lang = meta.get("lang") or "ru"
        raw_tags = []
        if meta.get("tags"):
            raw_tags = [t.strip() for t in re.split(r"[;,]", meta["tags"]) if t.strip()]
        tags = expand_tags_bilingual(raw_tags)

        for part in split_text(body):
            yield {
                "text": part,
                "title": title,
                "source": source,
                "lang": lang,
                "tags": tags,
            }

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
            if not isinstance(raw, dict):
                continue

            text = raw.get("text") or raw.get("content")
            vec = raw.get("embedding") or raw.get("vector")
            title = raw.get("title") or raw.get("name") or "embeddings_index"
            source = raw.get("source") or raw.get("url") or raw.get("path") or raw.get("file") or "embeddings_index.json"

            # Вставляем дефолты, если метаданных нет
            lang = raw.get("lang") or "ru"
            tags = expand_tags_bilingual([t for t in (raw.get("tags") or [])]) if isinstance(raw.get("tags"), list) else []

            if isinstance(vec, list) and isinstance(text, str) and text.strip():
                yield {"text": text, "title": title, "source": source, "lang": lang, "tags": tags, "embedding": vec}
            elif isinstance(text, str) and text.strip():
                yield {"text": text, "title": title, "source": source, "lang": lang, "tags": tags}

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
            points_batch.append(PointStruct(id=pid, vector=vec, payload=payload))
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
        payload = {
            "text": doc["text"],
            "title": doc.get("title"),
            "source": doc.get("source"),
            "lang": doc.get("lang", "ru"),
            "tags": doc.get("tags", []),
        }

        if "embedding" in doc and isinstance(doc["embedding"], list):
            points_batch.append(PointStruct(id=str(uuid.uuid4()), vector=doc["embedding"], payload=payload))
            if len(points_batch) >= BATCH:
                client.upsert(collection_name=QDRANT_COLLECTION, points=points_batch)
                points_batch = []
        else:
            buffer_texts.append(doc["text"])
            buffer_payloads.append(payload)
            # крупнее батч на /embeddings = меньше overhead
            if len(buffer_texts) >= 128:
                await flush_with_embeddings()

    await flush_with_embeddings()
    await flush_points_only()
    print("Ingest finished ✅")

if __name__ == "__main__":
    asyncio.run(main())
