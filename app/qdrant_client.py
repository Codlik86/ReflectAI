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
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus_v2").strip()

# 1536 для text-embedding-3-small
EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))

# Позволим управлять предпочтением gRPC через env
PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "1").strip() not in {"0", "false", "False", ""}
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334") or "6334")

_client: QdrantClient | None = None


def _build_client() -> QdrantClient:
    """
    Создаём клиента с умным парсингом QDRANT_URL.
    Поддерживаем:
      - url=http(s)://host:6333 (REST) + prob gRPC на 6334
      - prefer_grpc=True: включаем gRPC, если доступен; иначе используем REST
    """
    if not QDRANT_URL:
        raise RuntimeError("QDRANT_URL is not set")

    u = urlparse(QDRANT_URL)
    if not u.scheme:
        u = urlparse("http://" + QDRANT_URL)

    host = u.hostname or QDRANT_URL
    port = u.port or (443 if u.scheme == "https" else 6333)
    https = (u.scheme == "https")
    grpc_port = QDRANT_GRPC_PORT

    if PREFER_GRPC:
        try:
            return QdrantClient(
                host=host,
                port=port,
                grpc_port=grpc_port,
                api_key=QDRANT_API_KEY,
                https=https,   # REST
                ssl=https,     # gRPC
                prefer_grpc=True,
                timeout=60.0,
            )
        except Exception:
            pass

    return QdrantClient(
        host=host,
        port=port,
        api_key=QDRANT_API_KEY,
        https=https,
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


def get_collection_name() -> str:
    return QDRANT_COLLECTION


def ensure_collection() -> bool:
    """
    Гарантирует, что коллекция создана. Если нет — создаёт.
    Возвращает True при успехе, False при ошибке.
    """
    try:
        client = get_client()
        exists = client.collection_exists(QDRANT_COLLECTION)
        if not exists:
            client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE),
            )
        return True
    except Exception as e:
        print(f"[qdrant] ensure_collection WARNING: {e}")
        return False


__all__ = [
    "get_client", "ensure_collection", "close_client", "get_collection_name",
    "QDRANT_COLLECTION", "QDRANT_URL", "QDRANT_API_KEY", "EMBED_DIM"
]
