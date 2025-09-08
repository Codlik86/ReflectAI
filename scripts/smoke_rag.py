# scripts/smoke_rag.py (верх файла)

import os
import argparse
import asyncio
from typing import List, Dict, Any

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from qdrant_client import QdrantClient
from app.qdrant_client import get_client           # 👈 потом импортируем модуль, который читает env
from app.rag_qdrant import embed, _diversify, _bucket_of

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")

DEFAULT_QUERIES = [
    "Я зацикливаюсь на мыслях перед сном",
    "Прокрастинирую и не могу начать дела",
    "Страшно написать незнакомому человеку",
]


def _payload_text(payload: Dict[str, Any]) -> str:
    return (
        (payload or {}).get("text")
        or (payload or {}).get("content")
        or (payload or {}).get("document")
        or ""
    ).strip()


async def run_query(client: QdrantClient, query: str, k: int, limit: int, max_chars: int, last_tag: str | None):
    print("=" * 80)
    print(f"QUERY: {query!r} | k={k}, limit={limit}, last_suggested_tag={last_tag}")
    vec = await embed(query)

    hits = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=vec,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    if not hits:
        print("⚠️  No hits.")
        return

    selected = _diversify(hits, k=k, last_suggested_tag=last_tag)

    # печать короткого отчёта
    total = 0
    buckets = []
    for i, p in enumerate(selected, 1):
        pl = p.payload or {}
        tags = list(pl.get("tags") or [])
        bucket = _bucket_of(set(tags))
        buckets.append(bucket or "—")
        title = pl.get("title") or "—"
        source = pl.get("source") or "—"
        text = _payload_text(pl)
        snippet = (text[:140] + "…") if len(text) > 140 else text
        print(f"[{i}] score={getattr(p, 'score', 0):.4f} bucket={bucket or '—'} title={title}")
        print(f"     tags={tags}")
        print(f"     src={source}")
        print(f"     txt={snippet!r}")

    # соберём итоговый контекст (как в rag_qdrant.search)
    pieces: List[str] = []
    for p in selected:
        t = _payload_text(p.payload or {})
        take = max_chars - sum(len(s) for s in pieces)
        if take <= 0:
            break
        if len(t) > take:
            t = t[:take].rstrip() + "…"
        if t:
            pieces.append(t)
    context = ("\n\n---\n\n").join(pieces)
    print("-" * 80)
    print(f"Context length: {len(context)} chars | Buckets used: {buckets}")
    print("-" * 80)
    print(context[:500] + ("…" if len(context) > 500 else ""))


async def main():
    parser = argparse.ArgumentParser(description="Smoke test for RAG diversification")
    parser.add_argument("queries", nargs="*", default=DEFAULT_QUERIES, help="Test queries (ru)")
    parser.add_argument("--k", type=int, default=4, help="How many chunks to select")
    parser.add_argument("--limit", type=int, default=16, help="Initial Qdrant hits")
    parser.add_argument("--max-chars", type=int, default=1400, help="Max chars in final context")
    parser.add_argument("--last", type=str, default=None, help="last_suggested_tag (e.g., breathing)")
    args = parser.parse_args()

    client = get_client()
    for q in args.queries:
        await run_query(client, q, args.k, args.limit, args.max_chars, args.last)


if __name__ == "__main__":
    asyncio.run(main())
