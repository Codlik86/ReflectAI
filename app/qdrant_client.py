# app/qdrant_client.py
import os
from dotenv import load_dotenv
load_dotenv()  # 👈 Подхватываем .env при любом импорте
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")

# text-embedding-3-small -> 1536
EMBED_DIM = 1536

_client = None

def get_client() -> QdrantClient:
    global _client
    if _client:
        return _client
    if not QDRANT_URL:
        raise RuntimeError("QDRANT_URL is not set")
    _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=60.0)
    return _client

def ensure_collection():
    client = get_client()
    exists = client.collection_exists(QDRANT_COLLECTION)
    if not exists:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
    return True
