# app/rag_qdrant.py
import os
from typing import List, Dict, Any, Optional, Set
import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from app.qdrant_client import get_client

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# Корзины, чтобы ответы были разнообразными (когнитивное/поведенческое/психообразование)
PREFERRED_BUCKETS = [
    {
        "name": "cognitive",
        "tags": {
            "cognitive_restructuring",
            "socratic_questioning",
            "cognitive_model",
            "defusion",
        },
    },
    {
        "name": "behavioral",
        "tags": {
            "behavioural_activation",  # британское написание
            "behavioral_experiments",
            "exposure",
            "problem_solving",
            "values",
        },
    },
    {
        "name": "psychoeducation",
        "tags": {
            "psychoeducation",
            "expectations",
            "homework",
            "session_structure",
            "relapse_prevention",
            "self_help",
        },
    },
]

# Не запрещаем, но мягко снижаем приоритет, если только что предлагали
DEPRIORITIZE: Set[str] = {"breathing"}


async def embed(text: str) -> List[float]:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    payload = {"model": EMBED_MODEL, "input": text}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{OPENAI_BASE}/embeddings", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data["data"][0]["embedding"]


def _bucket_of(tagset: Set[str]) -> Optional[str]:
    for b in PREFERRED_BUCKETS:
        if tagset & b["tags"]:
            return b["name"]
    return None


def _diversify(scored_points, k: int, last_suggested_tag: Optional[str] = None):
    """
    Вход: результаты Qdrant (объекты со свойствами .payload и .score)
    Выход: k элементов с разнообразием по корзинам и мягким анти-повтором.
    """
    pool = []
    for p in scored_points:
        payload = (getattr(p, "payload", None) or {})
        tags = set(payload.get("tags") or [])
        pool.append(
            {
                "p": p,
                "tags": tags,
                "bucket": _bucket_of(tags),
                "score": float(getattr(p, "score", 0.0) or 0.0),
            }
        )

    # 1) Если в прошлом ответе уже предлагали дыхание — по возможности убираем его из кандидатов
    if last_suggested_tag == "breathing":
        filtered = [x for x in pool if "breathing" not in x["tags"]]
        pool = filtered or pool  # если всё исчезло — откатимся

    picked = []

    # 2) Сначала закрываем ключевые корзины по одному элементу
    for bucket_name in ["cognitive", "behavioral", "psychoeducation"]:
        cand = [x for x in pool if x["bucket"] == bucket_name]
        if cand:
            best = sorted(cand, key=lambda x: x["score"], reverse=True)[0]
            picked.append(best)
            pool.remove(best)
            if len(picked) >= k:
                break

    # 3) Добираем оставшиеся лучшие, избегая перенасыщения одними и теми же тегами
    seen_tags = set().union(*[x["tags"] for x in picked]) if picked else set()
    for x in sorted(pool, key=lambda x: x["score"], reverse=True):
        if len(picked) >= k:
            break
        if (x["tags"] & seen_tags) and (x["tags"] & DEPRIORITIZE):
            continue
        picked.append(x)
        seen_tags |= x["tags"]

    return [x["p"] for x in picked]


def _text_from_payload(payload: Dict[str, Any]) -> str:
    # Основной путь — payload["text"]; фоллбеки на случай старых точек
    return (
        (payload or {}).get("text")
        or (payload or {}).get("content")
        or (payload or {}).get("document")
        or ""
    ).strip()


async def search(
    query: str,
    k: int = 4,
    max_chars: int = 1400,
    filter_by: Dict[str, Any] | None = None,
    last_suggested_tag: Optional[str] = None,
) -> str:
    """
    Возвращает СТРОКУ контекста (склеенные выдержки) для подсказки LLM.
    — Берём limit=16 лучших кандидатов из Qdrant.
    — Отбираем k (по умолчанию 4) с диверсификацией по корзинам.
    — Мягко снижаем повтор дыхания, если недавно советовали.
    """
    client: QdrantClient = get_client()
    qvec = await embed(query)

    qfilter = None
    if filter_by:
        conds = []
        for kf, val in filter_by.items():
            conds.append(FieldCondition(key=kf, match=MatchValue(value=val)))
        qfilter = Filter(must=conds)

    hits = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=qvec,
        limit=16,  # берём больше кандидатов, потом диверсифицируем
        query_filter=qfilter,
        with_payload=True,
        with_vectors=False,
    )

    if not hits:
        return ""

    selected = _diversify(hits, k=k, last_suggested_tag=last_suggested_tag)

    # Склеиваем тексты; разделяем маркером для читаемости
    pieces: List[str] = []
    total = 0
    for p in selected:
        t = _text_from_payload(getattr(p, "payload", {}) or {})
        if not t:
            continue
        # обрезка по лимиту символов
        take = max_chars - total
        if take <= 0:
            break
        if len(t) > take:
            t = t[:take].rstrip() + "…"
        pieces.append(t)
        total += len(t)
        if total >= max_chars:
            break

    return ("\n\n---\n\n").join(pieces)
