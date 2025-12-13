# app/rag_summaries.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
import os
import inspect

# Универсальные модели Qdrant
from qdrant_client.http import models as qm  # type: ignore

from app.qdrant_client import get_client, detect_vector_name, QDRANT_VECTOR_NAME, qdrant_query, normalize_points
from app.rag_qdrant import embed  # тот же эмбеддер, что в основном RAG

# === Конфиги ===
SUMMARIES_COLLECTION = os.getenv("QDRANT_SUMMARIES_COLLECTION", "dialog_summaries_v1")
_EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))

def _safe_print(*args: Any) -> None:
    try:
        print(*args)
    except Exception:
        pass


def _ensure_collection() -> None:
    """
    Создаёт коллекцию, если её ещё нет.
    Пытаемся сначала single-vector (VectorParams), при ошибке — named-vectors с именем из env/дефолт.
    """
    client = get_client()
    try:
        client.get_collection(SUMMARIES_COLLECTION)
        return  # уже есть
    except Exception:
        pass

    vec_name = QDRANT_VECTOR_NAME or "default"

    # Попытка single-vector
    try:
        client.recreate_collection(
            collection_name=SUMMARIES_COLLECTION,
            vectors_config=qm.VectorParams(size=_EMBED_DIM, distance=qm.Distance.COSINE),
            optimizers_config=qm.OptimizersConfigDiff(default_segment_number=2),
        )
        _safe_print(f"[summaries] created collection '{SUMMARIES_COLLECTION}' (single-vector)")
        return
    except Exception as e:
        _safe_print(f"[summaries] single-vector create failed, try named: {e!r}")

    # Фолбэк: named-vectors
    try:
        client.recreate_collection(
            collection_name=SUMMARIES_COLLECTION,
            vectors_config={vec_name: qm.VectorParams(size=_EMBED_DIM, distance=qm.Distance.COSINE)},  # type: ignore
            optimizers_config=qm.OptimizersConfigDiff(default_segment_number=2),
        )
        _safe_print(f"[summaries] created collection '{SUMMARIES_COLLECTION}' (named-vector '{vec_name}')")
    except Exception as e:
        # Пусть ошибка уйдёт наверх — так её будет видно в логе крона
        raise RuntimeError(f"Failed to create summaries collection '{SUMMARIES_COLLECTION}': {e}") from e


_LOGGED_SUMMARY_ONCE = False


async def _maybe_embed(text: str) -> List[float]:
    """
    Поддержка и sync, и async embed() реализаций.
    """
    try:
        res = embed(text)
        if inspect.isawaitable(res):
            return await res  # type: ignore
        return res  # type: ignore
    except Exception as e:
        raise RuntimeError(f"embeddings failed: {e}") from e


def _call_search(
    client,
    *,
    vector,
    flt,
    limit: int,
    use_named: bool,
    vector_name: Optional[str],
):
    """
    Унифицированный вызов поиска через qdrant_query (query_points/search_points/search).
    """
    return qdrant_query(
        client,
        collection_name=SUMMARIES_COLLECTION,
        query_vector=vector,
        query_filter=flt,
        limit=int(limit),
        with_payload=True,
        vector_name=(vector_name or "default") if use_named else vector_name,
    )


async def upsert_summary_point(
    *,
    summary_id: int,
    user_id: int,
    kind: str,                 # "daily" | "weekly" | "monthly"
    text: str,
    period_start: datetime,
    period_end: datetime,
    tags: Optional[List[str]] = None,
) -> None:
    """
    Асинхронная сигнатура (для совместимости с твоими крон-джобами), внутри — синхронные вызовы клиента ок.
    Делает автосоздание коллекции, выбирает корректный формат вектора (named/single) с фолбэком.
    """
    _ensure_collection()

    vec = await _maybe_embed(text)
    payload = {
        "user_id": int(user_id),
        "kind": str(kind),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "len": len(text or ""),
        "tags": tags or [],
        "raw": text,
    }

    client = get_client()
    mode, vname = detect_vector_name(client, SUMMARIES_COLLECTION)
    prefer_named = mode == "named"

    # Сначала пробуем по «детектированной» схеме
    try:
        if prefer_named:
            pt = qm.PointStruct(id=int(summary_id), vector={vname: vec}, payload=payload)  # type: ignore
        else:
            pt = qm.PointStruct(id=int(summary_id), vector=vec, payload=payload)
        client.upsert(collection_name=SUMMARIES_COLLECTION, points=[pt])
        return
    except Exception as e_first:
        _safe_print(f"[summaries] upsert primary mode failed, fallback: {e_first!r}")

    # Фолбэк: переключаемся на другой вариант (named <-> single)
    try:
        if prefer_named:
            pt = qm.PointStruct(id=int(summary_id), vector=vec, payload=payload)
        else:
            pt = qm.PointStruct(id=int(summary_id), vector={vname: vec}, payload=payload)  # type: ignore
        client.upsert(collection_name=SUMMARIES_COLLECTION, points=[pt])
    except Exception as e:
        raise RuntimeError(f"Qdrant upsert failed for summary_id={summary_id}: {e}") from e


async def delete_user_summaries(user_id: int) -> None:
    """
    Удаляет ВСЕ саммари пользователя из коллекции.
    """
    _ensure_collection()
    client = get_client()
    f = qm.Filter(must=[qm.FieldCondition(key="user_id", match=qm.MatchValue(value=int(user_id)))])

    # Новые клиенты ожидают FilterSelector, старые — словарь/ключ "filter"
    try:
        selector = qm.FilterSelector(filter=f)   # новый API
        client.delete(collection_name=SUMMARIES_COLLECTION, points_selector=selector)
    except Exception:
        client.delete(collection_name=SUMMARIES_COLLECTION, points_selector={"filter": f})  # type: ignore


async def search_summaries(
    *,
    user_id: int,
    query: str,
    top_k: int = 4,
    kinds: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Поиск релевантных саммарей пользователя. Можно сузить по видам (daily/weekly/monthly).
    Возвращает: summary_id, score, kind, period_start, period_end.
    """
    _ensure_collection()
    client = get_client()
    vec = await _maybe_embed(query)

    must_filters: List[qm.FieldCondition] = [
        qm.FieldCondition(key="user_id", match=qm.MatchValue(value=int(user_id)))
    ]
    if kinds:
        try:
            must_filters.append(qm.FieldCondition(key="kind", match=qm.MatchAny(any=[str(k) for k in kinds])))
        except Exception:
            # На старых клиентах MatchAny может отсутствовать — в таком случае обойдёмся OR'ом позже.
            pass

    f = qm.Filter(must=must_filters)

    mode, vname = detect_vector_name(client, SUMMARIES_COLLECTION)
    prefer_named = mode == "named"

    # Сначала пробуем primary режим, затем fallback
    try:
        res = _call_search(client, vector=vec, flt=f, limit=int(top_k), use_named=prefer_named, vector_name=vname)
    except Exception as e_first:
        _safe_print(f"[summaries] search primary failed, fallback: {e_first!r}")
        res = _call_search(client, vector=vec, flt=f, limit=int(top_k), use_named=not prefer_named, vector_name=vname)

    raw_type = type(res)
    res = normalize_points(res)
    global _LOGGED_SUMMARY_ONCE
    if not _LOGGED_SUMMARY_ONCE:
        try:
            import importlib.metadata as md
            ver = md.version("qdrant-client")
        except Exception:
            ver = "unknown"
        _safe_print(f"[summaries] qdrant-client={ver} raw_type={raw_type} norm_type={type(res)} len={len(res) if isinstance(res, list) else 'na'}")
        _LOGGED_SUMMARY_ONCE = True

    out: List[Dict[str, Any]] = []
    for r in res or []:
        p = r.payload or {}
        out.append(
            {
                "summary_id": int(r.id) if r.id is not None else None,
                "score": float(r.score) if r.score is not None else 0.0,
                "kind": p.get("kind"),
                "period_start": p.get("period_start"),
                "period_end": p.get("period_end"),
            }
        )
    return out
