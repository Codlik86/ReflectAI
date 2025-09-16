# -*- coding: utf-8 -*-
"""
Самопроверка RAG без влияния на UX.
Печать в консоль: наличие коллекции, количество точек, превью контекста и имена файлов.
"""
import os, asyncio
from importlib import import_module

QCOLL = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")

async def main():
    qc = import_module("app.qdrant_client")
    client = qc.get_client()
    exists = client.collection_exists(QCOLL)
    count  = client.count(QCOLL, exact=True).count if exists else 0
    print(f"[RAG] collection={QCOLL} exists={exists} points={count}")

    rq = import_module("app.rag_qdrant")
    query = "что такое когнитивно-поведенческая терапия?"
    # используем search_with_meta, если есть; иначе — search
    if hasattr(rq, "search_with_meta"):
        ctx, meta = await rq.search_with_meta(query, k=3, max_chars=1200)
        print(f"[RAG] ctx_len={len(ctx)} preview='{ctx[:160].replace('\\n',' ')}{'...' if len(ctx)>160 else ''}'")
        print("[RAG] sources:", ", ".join((d.get("source") or "?") for d in (meta or [])))
    else:
        ctx = await rq.search(query, k=3, max_chars=1200)
        print(f"[RAG] ctx_len={len(ctx)} preview='{ctx[:160].replace('\\n',' ')}{'...' if len(ctx)>160 else ''}'")

if __name__ == "__main__":
    asyncio.run(main())
