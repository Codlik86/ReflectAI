"""
Диагностика Qdrant-клиента: печатает тип/версию/методы и делает тестовый запрос limit=1 в обе коллекции.
Запуск: python3 scripts/diag_qdrant_client.py
"""

import asyncio
from app.qdrant_client import (
    get_client,
    QDRANT_COLLECTION,
    QDRANT_SUMMARIES_COLLECTION,
    qdrant_query,
)
from app.rag_qdrant import embed


async def main():
    cli = get_client()
    import importlib.metadata as md

    version = "unknown"
    try:
        version = md.version("qdrant-client")
    except Exception:
        pass

    info = {
        "type": type(cli),
        "module": getattr(cli, "__module__", None),
        "class": getattr(cli, "__class__", None),
        "has_search": hasattr(cli, "search"),
        "has_search_points": hasattr(cli, "search_points"),
        "has_query_points": hasattr(cli, "query_points"),
        "version": version,
    }
    print("client info:", info)

    try:
        vec = await embed("ping")
        print("embed type:", type(vec))
    except Exception as e:
        print("embed error:", e)
        vec = None

    for col in (QDRANT_COLLECTION, QDRANT_SUMMARIES_COLLECTION):
        try:
            branch = "query_points" if hasattr(cli, "query_points") else "search_points" if hasattr(cli, "search_points") else "search" if hasattr(cli, "search") else "none"
            res = qdrant_query(
                cli,
                collection_name=col,
                query_vector=vec or [0.0],
                limit=1,
                with_payload=True,
            )
            print(f"{col}: branch={branch} ok={bool(res)}")
        except Exception as e:
            print(f"{col}: error {e}")


if __name__ == "__main__":
    asyncio.run(main())
