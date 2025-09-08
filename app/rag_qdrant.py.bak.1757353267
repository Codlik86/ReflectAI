# app/rag_qdrant.py
import os, math, asyncio
from typing import List, Dict, Any, Tuple
import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from app.qdrant_client import get_client
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

async def embed(text: str) -> List[float]:
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    payload = {"model": EMBED_MODEL, "input": text}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{OPENAI_BASE}/embeddings", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data["data"][0]["embedding"]

async def search(query: str, k: int = 3, max_chars: int = 1200, filter_by: Dict[str, Any] | None = None) -> str:
    """
    Возвращает СТРОКУ контекста (склеенные выдержки) для подсказки LLM.
    Кнопок/источников пользователю НЕ показываем.
    """
    client: QdrantClient = get_client()
    qvec = await embed(query)

    qfilter = None
    if filter_by:
        # пример на будущее: {"source": "data/some.txt"}
        conds = []
        for kf, val in filter_by.items():
            conds.append(FieldCondition(key=kf, match=MatchValue(value=val)))
        qfilter = Filter(must=conds)

    res = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=qvec,
        limit=k,
        query_filter=qfilter,
        with_payload=True,
        with_vectors=False,
    )

    chunks = []
    total = 0
    for i, point in enumerate(res, 1):
        text = (point.payload or {}).get("text", "")
        text = text.strip()
        if not text:
            continue
        part = text
        if total + len(part) > max_chars:
            part = part[: max(0, max_chars - total)]
        chunks.append(f"[{i}] {part}")
        total += len(part)
        if total >= max_chars:
            break

    return "\n\n".join(chunks)
