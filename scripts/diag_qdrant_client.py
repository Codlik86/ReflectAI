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
    detect_vector_name,
    normalize_points,
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
            mode, vname = detect_vector_name(cli, col)
            branch_holder: dict[str, str] = {}
            res = qdrant_query(
                cli,
                collection_name=col,
                query_vector=vec or [0.0],
                limit=1,
                with_payload=True,
                vector_name=vname,
                branch_out=branch_holder,
            )
            branch = branch_holder.get("branch", "unknown")
            first_type = type(res[0]) if res else None
            print(f"{col}: mode={mode} vector_name={vname} branch={branch} ok={bool(res)} res_type={type(res)} first={first_type}")
        except Exception as e:
            print(f"{col}: error {e}")

    # scroll smoke
    try:
        sc = cli.scroll(collection_name=QDRANT_COLLECTION, limit=1, with_payload=True)
        sc_norm = normalize_points(sc)
        first = sc_norm[0] if sc_norm else None
        print(f"scroll: raw_type={type(sc)} norm_len={len(sc_norm)} first_type={type(first)}")
    except Exception as e:
        print(f"scroll error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
