# app/qdrant_client.py
from __future__ import annotations

import os
from urllib.parse import urlparse

# В локалке читаем .env (на Render переменные уже в окружении)
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

from qdrant_client import QdrantClient

# Векторные типы берём через общий неймспейс — так стабильнее для разных версий
from qdrant_client.http import models as qm  # type: ignore

QDRANT_URL = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip() or None

# Основная продуктовая коллекция (RAG-корпус)
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus_v2").strip()

# Коллекция для саммарей (daily/weekly/monthly) — с именованным вектором "text"
QDRANT_SUMMARIES_COLLECTION = os.getenv("QDRANT_SUMMARIES_COLLECTION", "dialog_summaries_v1").strip()

# 1536 для text-embedding-3-small
EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))

# Позволим управлять предпочтением gRPC через env
PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "1").strip() not in {"0", "false", "False", ""}
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334") or "6334")

_client: QdrantClient | None = None


def _build_client() -> QdrantClient:
    """
    Создаём клиента, максимально доверяя библиотеке разбор URL.
    Поддерживает https://host:6333, http://host:6333 и т.д.
    """
    if not QDRANT_URL:
        raise RuntimeError("QDRANT_URL is not set")

    # Сначала пробуем «умный» вариант: url=...
    try:
        return QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            prefer_grpc=PREFER_GRPC,
            grpc_port=QDRANT_GRPC_PORT,
            timeout=60.0,
        )
    except Exception:
        # Фолбэк на ручную сборку host/port (на случай очень «сырых» URL)
        from urllib.parse import urlparse
        u = urlparse(QDRANT_URL if "://" in QDRANT_URL else "http://" + QDRANT_URL)
        host = u.hostname or QDRANT_URL
        port = u.port or (443 if u.scheme == "https" else 6333)
        https = (u.scheme == "https")
        return QdrantClient(
            host=host,
            port=port,
            grpc_port=QDRANT_GRPC_PORT,
            api_key=QDRANT_API_KEY,
            https=https,
            ssl=https,
            prefer_grpc=PREFER_GRPC,
            timeout=60.0,
        )


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def close_client() -> None:
    global _client
    _client = None


def _collection_exists_safe(client: QdrantClient, name: str) -> bool:
    """
    Унифицированная проверка существования коллекции, совместимая с разными версиями клиента.
    """
    try:
        # Новый API
        return bool(client.collection_exists(name))
    except Exception:
        pass
    try:
        # Старый/универсальный способ — пробуем получить метаданные
        client.get_collection(name)
        return True
    except Exception:
        return False


def get_collection_name() -> str:
    return QDRANT_COLLECTION


def get_summaries_collection_name() -> str:
    return QDRANT_SUMMARIES_COLLECTION


def ensure_collection() -> bool:
    """
    Гарантирует, что основная RAG-коллекция создана (с одиночным вектором).
    Возвращает True при успехе, False при ошибке.
    """
    try:
        client = get_client()
        if not _collection_exists_safe(client, QDRANT_COLLECTION):
            # Обычный одинарный вектор без имени
            vectors_cfg = qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE)
            client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=vectors_cfg,
            )
        return True
    except Exception as e:
        print(f"[qdrant] ensure_collection WARNING: {e}")
        return False


def ensure_summaries_collection() -> bool:
    """
    Гарантирует, что коллекция саммарей создана. Используем именованный вектор "text".
    Возвращает True при успехе, False при ошибке.
    """
    try:
        client = get_client()
        if not _collection_exists_safe(client, QDRANT_SUMMARIES_COLLECTION):
            # Именованный вектор: {"text": VectorParams(...)}
            named_cfg = {"text": qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE)}
            client.create_collection(
                collection_name=QDRANT_SUMMARIES_COLLECTION,
                vectors_config=named_cfg,
            )
        return True
    except Exception as e:
        print(f"[qdrant] ensure_summaries_collection WARNING: {e}")
        return False


def ping_qdrant() -> bool:
    """
    Быстрая проверка доступности кластера (для кронов и health-check).
    """
    try:
        client = get_client()
        _ = client.get_collections()
        return True
    except Exception as e:
        print(f"[qdrant] ping failed: {e}")
        return False


__all__ = [
    "get_client", "ensure_collection", "ensure_summaries_collection", "close_client",
    "get_collection_name", "get_summaries_collection_name",
    "QDRANT_COLLECTION", "QDRANT_SUMMARIES_COLLECTION",
    "QDRANT_URL", "QDRANT_API_KEY", "EMBED_DIM", "ping_qdrant"
]