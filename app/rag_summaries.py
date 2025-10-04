# app/rag_summaries.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
import os
import inspect

# Универсальные модели Qdrant
from qdrant_client.http import models as qm  # type: ignore

from app.qdrant_client import get_client
from app.rag_qdrant import embed  # тот же эмбеддер, что в основном RAG

# === Конфиги ===
SUMMARIES_COLLECTION = os.getenv("QDRANT_SUMMARIES_COLLECTION", "dialog_summaries_v1")
_EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))

# Внутренний флаг: как создана коллекция (single-vector или named "text")
# Определим лениво при первой попытке апсерта/поиска.
_named_mode_detected: Optional[bool] = None


def _safe_print(*args: Any) -> None:
    try:
        print(*args)
    except Exception:
        pass


def _ensure_collection() -> None:
    """
    Создаёт коллекцию, если её ещё нет.
    Пытаемся сначала single-vector (VectorParams), при ошибке — named-vectors {"text": VectorParams}.
    """
    client = get_client()
    try:
        client.get_collection(SUMMARIES_COLLECTION)
        return  # уже есть
    except Exception:
        pass

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
            vectors_config={"text": qm.VectorParams(size=_EMBED_DIM, distance=qm.Distance.COSINE)},  # type: ignore
            optimizers_config=qm.OptimizersConfigDiff(default_segment_number=2),
        )
        _safe_print(f"[summaries] created collection '{SUMMARIES_COLLECTION}' (named-vector 'text')")
    except Exception as e:
        # Пусть ошибка уйдёт наверх — так её будет видно в логе крона
        raise RuntimeError(f"Failed to create summaries collection '{SUMMARIES_COLLECTION}': {e}") from e


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


def _detect_named_mode_once(client) -> bool:
    """
    Пробуем понять, как устроена коллекция: single-vector или named "text".
    Сохраняем результат в модульный флаг.
    """
    global _named_mode_detected
    if _named_mode_detected is not None:
        return _named_mode_detected

    try:
        info = client.get_collection(SUMMARIES_COLLECTION)
        # У разных версий клиента структура отличается; проверим оба варианта.
        vectors_config = getattr(info, "vectors_count", None)
        # Если есть "vectors_count" и это int, это скорее single-vector (>=1).
        # Попробуем более надёжно глянуть на атрибуты:
        if hasattr(info, "config") and getattr(info.config, "params", None) is not None:
            params = info.config.params
            if hasattr(params, "vectors") and isinstance(params.vectors, dict):
                # named
                _named_mode_detected = True
                return True
            # если не dict — скорее single
            _named_mode_detected = False
            return False

        # Если не смогли достоверно — пробуем поискать поле "vectors" как dict
        if hasattr(info, "vectors") and isinstance(getattr(info, "vectors"), dict):
            _named_mode_detected = True
            return True

        _named_mode_detected = False
        return False
    except Exception:
        # Если не получилось узнать — по умолчанию считаем single-vector,
        # а при апсерте/поиске сделаем фолбэк.
        _named_mode_detected = False
        return False


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

    # Сначала пробуем по «детектированной» схеме
    prefer_named = _detect_named_mode_once(client)
    try:
        if prefer_named:
            pt = qm.PointStruct(id=int(summary_id), vector={"text": vec}, payload=payload)  # type: ignore
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
            pt = qm.PointStruct(id=int(summary_id), vector={"text": vec}, payload=payload)  # type: ignore
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

    # Сначала пробуем single-vector, затем named ("text")
    try:
        res = client.search(
            collection_name=SUMMARIES_COLLECTION,
            query_vector=vec,
            query_filter=f,
            limit=int(top_k),
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e_first:
        _safe_print(f"[summaries] search single-vector failed, fallback to named: {e_first!r}")
        res = client.search(
            collection_name=SUMMARIES_COLLECTION,
            query_vector=("text", vec),
            query_filter=f,
            limit=int(top_k),
            with_payload=True,
            with_vectors=False,
        )

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
