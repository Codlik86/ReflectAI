# app/qdrant_client.py
import os
from urllib.parse import urlparse
from dotenv import load_dotenv, find_dotenv

# В локалке читаем .env (на Render переменные уже в окружении, это не мешает)
load_dotenv(find_dotenv(usecwd=True))

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

QDRANT_URL = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip() or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus").strip()
# 1536 для text-embedding-3-small
EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))
# Позволим управлять предпочтением gRPC через env
PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "1").strip() not in {"0", "false", "False", ""}

_client = None


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
    # Если пользователь дал host:port без схемы, urlparse не распознает. Попробуем поправить.
    if not u.scheme:
        # допустим пришло "host:6333"
        parsed = urlparse("http://" + QDRANT_URL)
        u = parsed

    host = u.hostname or QDRANT_URL
    port = u.port or (443 if u.scheme == "https" else 6333)
    https = (u.scheme == "https")

    # На Qdrant Cloud обычно: 6333 REST, 6334 gRPC
    grpc_port = 6334

    # Сначала пробуем prefer_grpc=True (если разрешено), иначе чистый REST.
    if PREFER_GRPC:
        try:
            return QdrantClient(
                host=host,
                port=port,
                grpc_port=grpc_port,
                api_key=QDRANT_API_KEY,
                https=https,   # для REST
                ssl=https,     # для gRPC
                prefer_grpc=True,
                timeout=60.0,
            )
        except Exception:
            # Фолбэк на REST по тому же хосту
            pass

    # Чистый REST клиент
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


def ensure_collection() -> bool:
    """
    Гарантирует, что коллекция создана. Если нет — создаёт.
    """
    client = get_client()
    exists = client.collection_exists(QDRANT_COLLECTION)
    if not exists:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
    return True
