"""
Быстрая проверка Qdrant: выводит vector_name/mode и делает по одному поиску
в коллекциях reflectai_corpus_v2 и dialog_summaries_v1.
"""
import asyncio
import os

try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

from app.qdrant_client import (
    get_client,
    detect_vector_config,
    QDRANT_COLLECTION,
    QDRANT_SUMMARIES_COLLECTION,
)
from app.rag_qdrant import embed
from app.rag_summaries import search_summaries


async def main():
    client = get_client()

    def report_collection(name: str):
        conf = detect_vector_config(name)
        print(f"[collection] {name}: mode={conf.get('mode')} vector_name={conf.get('vector_name')}")
        return conf

    conf_corpus = report_collection(QDRANT_COLLECTION)
    conf_summ = report_collection(QDRANT_SUMMARIES_COLLECTION)

    # Поиск по корпусу
    try:
        vec = await embed("быстрый тест")
        res = client.search_points(
            collection_name=QDRANT_COLLECTION,
            vector=vec,
            vector_name=conf_corpus.get("vector_name"),
            limit=1,
            with_payload=True,
        ) if hasattr(client, "search_points") else client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=(conf_corpus.get("vector_name"), vec) if conf_corpus.get("mode") == "named" else vec,
            limit=1,
            with_payload=True,
        )
        print(f"[corpus] ok={bool(res)}")
    except Exception as e:
        print(f"[corpus] error: {e}")

    # Поиск по саммарям
    try:
        res = await search_summaries(user_id=1, query="проверка", top_k=1)
        print(f"[summaries] ok={bool(res)}")
    except Exception as e:
        print(f"[summaries] error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
