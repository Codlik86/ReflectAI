# app/qdrant_client.py
from __future__ import annotations

import os
import logging
from urllib.parse import urlparse
from typing import Dict, Optional
import importlib.metadata as md

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

# Имя вектора (если коллекция named). Если не задано — возьмём первое из коллекции (чаще всего "default").
QDRANT_VECTOR_NAME = (os.getenv("QDRANT_VECTOR_NAME") or "").strip() or None

# 1536 для text-embedding-3-small
EMBED_DIM = int(os.getenv("QDRANT_EMBED_DIM", "1536"))

# Позволим управлять предпочтением gRPC через env
PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "1").strip() not in {"0", "false", "False", ""}
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334") or "6334")

_client: QdrantClient | None = None
_capabilities_logged = False


def _log_capabilities(client: QdrantClient) -> None:
    global _capabilities_logged
    if _capabilities_logged:
        return
    _capabilities_logged = True
    try:
        version = md.version("qdrant-client")
    except Exception:
        version = "unknown"
    has_search = hasattr(client, "search")
    has_search_points = hasattr(client, "search_points")
    logging.info("[qdrant] client_version=%s has_search=%s has_search_points=%s", version, has_search, has_search_points)
    if not (has_search or has_search_points):
        logging.error("[qdrant] FATAL: client has neither search nor search_points; version=%s", version)
        raise RuntimeError("Qdrant client missing search/search_points")


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
        if not isinstance(_client, QdrantClient):
            logging.error("[qdrant] FATAL: get_client returned unexpected type=%s", type(_client))
            raise RuntimeError("Qdrant client is not qdrant_client.QdrantClient")
        _log_capabilities(_client)
    return _client


def close_client() -> None:
    global _client
    _client = None


def _pick_vector_name_from_meta(info) -> Optional[str]:
    """
    Достаёт имя вектора из get_collection(...) для named-векторов.
    """
    if info is None:
        return QDRANT_VECTOR_NAME
    try:
        # Новый клиент: info.config.params.vectors может быть dict
        params = getattr(getattr(info, "config", None), "params", None)
        vecs = getattr(params, "vectors", None)
        if isinstance(vecs, dict) and vecs:
            return QDRANT_VECTOR_NAME or next(iter(vecs.keys()), None)
    except Exception:
        pass
    try:
        vecs = getattr(info, "vectors", None)
        if isinstance(vecs, dict) and vecs:
            return QDRANT_VECTOR_NAME or next(iter(vecs.keys()), None)
    except Exception:
        pass
    try:
        vecs_cfg = getattr(info, "vectors_config", None)
        if isinstance(vecs_cfg, dict) and vecs_cfg:
            return QDRANT_VECTOR_NAME or next(iter(vecs_cfg.keys()), None)
    except Exception:
        pass
    return QDRANT_VECTOR_NAME


def detect_vector_config(collection: str) -> Dict[str, Optional[str]]:
    """
    Определяет, single или named-векторы у коллекции, и возвращает имя вектора (если named).
    Возвращает {"mode": "single"|"named", "vector_name": str|None}
    """
    client = get_client()
    try:
        info = client.get_collection(collection)
    except Exception:
        return {"mode": "single", "vector_name": QDRANT_VECTOR_NAME}

    # Попробуем определить по конфигу
    try:
        params = getattr(getattr(info, "config", None), "params", None)
        vecs = getattr(params, "vectors", None)
        if isinstance(vecs, dict):
            return {"mode": "named", "vector_name": _pick_vector_name_from_meta(info)}
    except Exception:
        pass
    try:
        vecs = getattr(info, "vectors", None)
        if isinstance(vecs, dict):
            return {"mode": "named", "vector_name": _pick_vector_name_from_meta(info)}
    except Exception:
        pass
    try:
        vecs_cfg = getattr(info, "vectors_config", None)
        if isinstance(vecs_cfg, dict):
            return {"mode": "named", "vector_name": _pick_vector_name_from_meta(info)}
    except Exception:
        pass

    return {"mode": "single", "vector_name": None}


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


def _ensure_user_id_index(client: QdrantClient, collection: str) -> None:
    """
    Создаёт payload-индекс по полю user_id (integer) для заданной коллекции.
    Безопасно: не падает, если индекс уже есть или версия клиента иная.
    """
    try:
        # Попытка через enum типа
        schema_enum = getattr(qm, "PayloadSchemaType", None)
        if schema_enum is not None:
            client.create_payload_index(
                collection_name=collection,
                field_name="user_id",
                field_schema=schema_enum.INTEGER,
                wait=True,
            )
            return
        # Фолбэк: явный словарь-схема
        client.create_payload_index(
            collection_name=collection,
            field_name="user_id",
            field_schema={"type": "integer"},
            wait=True,
        )
    except Exception as e:
        # Обычно приходит AlreadyExists / BadRequest при повторном создании — игнорируем.
        msg = str(e)
        if "already exists" in msg.lower():
            return
        # Если коллекции ещё нет — её создадут выше, после чего снова вызовут этот метод.
        if "not found" in msg.lower():
            return
        # Логируем предупреждение, но не валим процесс.
        print(f"[qdrant] ensure user_id index warning: {e}")


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
    Также гарантируем payload-индекс по user_id (integer) для быстрых фильтров.
    Возвращает True при успехе, False при ошибке.
    """
    try:
        client = get_client()
        created_now = False
        if not _collection_exists_safe(client, QDRANT_SUMMARIES_COLLECTION):
            # Именованный вектор: {"text": VectorParams(...)}
            vname = QDRANT_VECTOR_NAME or "default"
            named_cfg = {vname: qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE)}
            client.create_collection(
                collection_name=QDRANT_SUMMARIES_COLLECTION,
                vectors_config=named_cfg,
            )
            created_now = True

        # В любом случае убедимся, что есть индекс по user_id
        _ensure_user_id_index(client, QDRANT_SUMMARIES_COLLECTION)
        return True
    except Exception as e:
        print(f"[qdrant] ensure_summaries_collection WARNING: {e}")
        return False


def ensure_qdrant_ready() -> bool:
    """
    Хелпер для инициализации всего нужного: обе коллекции + индекс.
    Можно вызывать из старта приложения.
    """
    ok1 = ensure_collection()
    ok2 = ensure_summaries_collection()
    return ok1 and ok2


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
    "get_client", "ensure_collection", "ensure_summaries_collection", "ensure_qdrant_ready", "close_client",
    "get_collection_name", "get_summaries_collection_name",
    "QDRANT_COLLECTION", "QDRANT_SUMMARIES_COLLECTION",
    "QDRANT_URL", "QDRANT_API_KEY", "EMBED_DIM", "ping_qdrant"
]
