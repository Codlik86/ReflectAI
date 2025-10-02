# app/rag_summaries.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import os

# Используем универсальные модели Qdrant через единый неймспейс
from qdrant_client.http import models as qm  # type: ignore

from app.qdrant_client import get_client
from app.rag_qdrant import embed  # тот же эмбеддер, что в основном RAG

SUMMARIES_COLLECTION = os.getenv("QDRANT_SUMMARIES_COLLECTION", "dialog_summaries_v1")
_EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))

def _ensure_collection() -> None:
    client = get_client()
    try:
        client.get_collection(SUMMARIES_COLLECTION)
    except Exception:
        client.recreate_collection(
            collection_name=SUMMARIES_COLLECTION,
            vectors_config={"text": {"size": _EMBED_DIM, "distance": "Cosine"}},
        )

async def upsert_summary_point(
    *, summary_id: int, user_id: int, kind: str, text: str,
    period_start: datetime, period_end: datetime
) -> None:
    _ensure_collection()
    client = get_client()
    vec = await embed(text)
    payload = {
        "user_id": user_id,
        "kind": kind,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "len": len(text),
    }
    pt = qm.PointStruct(id=summary_id, vector={"text": vec}, payload=payload)
    client.upsert(collection_name=SUMMARIES_COLLECTION, points=[pt])

async def delete_user_summaries(user_id: int) -> None:
    from qdrant_client.http import models as qm
    _ensure_collection()
    client = get_client()
    f = qm.Filter(must=[qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id))])
    try:
        selector = qm.FilterSelector(filter=f)   # новые клиенты
    except Exception:
        selector = {"filter": f}                 # старые клиенты
    client.delete(collection_name=SUMMARIES_COLLECTION, points_selector=selector)

async def search_summaries(user_id: int, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
    _ensure_collection()
    client = get_client()
    vec = await embed(query)
    f = qm.Filter(must=[qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id))])
    res = client.search(
        collection_name=SUMMARIES_COLLECTION,
        query_vector=("text", vec),
        query_filter=f,
        limit=top_k,
        with_payload=True,
        with_vectors=False,
    )
    out: List[Dict[str, Any]] = []
    for r in res:
        p = r.payload or {}
        out.append({
            "summary_id": int(r.id) if r.id is not None else None,
            "score": float(r.score) if r.score is not None else 0.0,
            "kind": p.get("kind"),
            "period_start": p.get("period_start"),
            "period_end": p.get("period_end"),
        })
    return out
