# app/rag_qdrant.py
# -*- coding: utf-8 -*-
"""
RAG via Qdrant: тихое подмешивание контекста.
Публичный API (стабильный для bot.py):
- async def search(query: str, k: int = 6, max_chars: int = 1200, lang: str|None = None) -> str
- async def search_with_meta(query: str, k: int = 6, max_chars: int = 1200, lang: str|None = None) -> tuple[str, list[dict]]
"""
from __future__ import annotations

import os, math, asyncio
from typing import Any, Dict, List, Optional, Tuple

# --- Конфиг из окружения
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus_v2")

EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "openai").lower()  # openai | sbert
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # поддержка прокси
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
SBERT_MODEL = os.getenv("SBERT_MODEL", "intfloat/multilingual-e5-small")

RAG_COMPRESS = os.getenv("RAG_COMPRESS", "0") == "1"
RAG_COMPRESS_MODEL = os.getenv("RAG_COMPRESS_MODEL", "gpt-4o-mini")
RAG_MAX_CHARS = int(os.getenv("RAG_MAX_CHARS", "1200"))
RAG_TRACE = os.getenv("RAG_TRACE", "0") == "1"

# --- Qdrant client (локальный грузовичок)
try:
    from app.qdrant_client import get_client  # type: ignore
except Exception:
    from qdrant_client import QdrantClient  # type: ignore
    def get_client() -> "QdrantClient":  # type: ignore
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)  # type: ignore

# --- Embeddings
def _get_embedder():
    # 1) OpenAI
    if EMBED_PROVIDER == "openai":
        try:
            from openai import OpenAI  # type: ignore
            client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)
            model = EMBED_MODEL

            def _emb(texts: List[str]) -> List[List[float]]:
                res = client.embeddings.create(model=model, input=texts)
                return [d.embedding for d in res.data]

            return _emb
        except Exception:
            pass
    # 2) sentence-transformers (SBERT)
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        m = SentenceTransformer(SBERT_MODEL)

        def _emb(texts: List[str]) -> List[List[float]]:
            return m.encode(texts, normalize_embeddings=False).tolist()

        return _emb
    except Exception:
        raise RuntimeError(
            "No embedding provider available. "
            "Укажи OPENAI_API_KEY (и OPENAI_BASE_URL если прокси) или установи sentence-transformers."
        )

_EMBED = _get_embedder()

async def embed(text: str) -> List[float]:
    # На случай тяжёлых провайдеров выполняем в пуле потоков
    return (await asyncio.to_thread(_EMBED, [text]))[0]

# --- Утилиты
def _title(payload: Dict[str, Any]) -> str:
    for k in ("title",):
        if payload.get(k): return str(payload[k])
    return ""

def _source(payload: Dict[str, Any]) -> str:
    for k in ("source","file"):
        if payload.get(k): return str(payload[k])
    return ""

def _chunk(payload: Dict[str, Any]) -> str:
    # при индексации мы кладём в 'text'
    for k in ("text", "chunk", "content"):
        if payload.get(k): return str(payload[k])
    return ""

def _split_ru_sents(text: str) -> List[str]:
    import re
    parts = re.split(r'(?<=[\.!\?])\s+(?=[А-ЯA-ZЁ])', text.strip())
    return [p.strip() for p in parts if p.strip()]

def _dot(a, b): return sum(x*y for x, y in zip(a, b))
def _norm(a): return math.sqrt(sum(x*x for x in a)) or 1.0
def _cos(a, b): return _dot(a,b) / (_norm(a)*_norm(b))

# --- Вспомогательные обёртки над синхронным Qdrant-клиентом
async def _qdrant_search_async(client, **kwargs):
    return await asyncio.to_thread(client.search, **kwargs)

# --- MMR контекст
async def build_context_mmr(
    query: str,
    *,
    initial_limit: int = 24,
    select: int = 6,
    max_chars: int = 1200,
    lang: Optional[str] = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    1) ищем initial_limit кандидатов
    2) считаем эмбеддинги их текстов (локально)
    3) забираем select штук MMR-логикой
    4) собираем до max_chars с обрезкой по предложениям
    """
    if not (query or "").strip():
        return "", []

    from qdrant_client.http import models as qm  # type: ignore

    client = get_client()
    qvec = await embed(query)

    qfilter = None
    if lang:
        qfilter = qm.Filter(must=[qm.FieldCondition(key="lang", match=qm.MatchValue(value=lang))])  # type: ignore

    # допускаем старый search (он проще), но можно заменить на query_points
    try:
        hits = await _qdrant_search_async(
            client,
            collection_name=QDRANT_COLLECTION,
            query_vector=qvec,  # type: ignore[arg-type]
            limit=initial_limit,
            with_payload=True,
            query_filter=qfilter,
        )
    except Exception:
        # если что — без фильтра (на случай отсутствия индекса по lang)
        hits = await _qdrant_search_async(
            client,
            collection_name=QDRANT_COLLECTION,
            query_vector=qvec,  # type: ignore[arg-type]
            limit=initial_limit,
            with_payload=True,
        )

    cand: List[Dict[str, Any]] = []
    texts: List[str] = []
    for h in hits or []:
        payload = getattr(h, "payload", {}) or {}
        t = _chunk(payload)
        if not t:
            continue
        cand.append({
            "text": t,
            "title": _title(payload),
            "src": _source(payload),
            "payload": payload,
            "score": getattr(h, "score", 0.0),
        })
        texts.append(t)

    if not cand:
        return "", []

    # Локальные эмбеддинги кандидатов (для диверсификации) — в пуле потоков
    vecs = await asyncio.to_thread(_EMBED, texts)

    # Похожесть запроса к каждому кандидату (пересчёт устойчивее)
    sims_q = [_cos(qvec, v) for v in vecs]

    selected_idx: List[int] = []
    used_sources: set[str] = set()

    for _ in range(min(select, len(cand))):
        best_i, best_score = -1, -1.0
        for i, item in enumerate(cand):
            if i in selected_idx:
                continue
            # penalty за один и тот же source
            if item["src"] in used_sources:
                continue
            penalty = 0.0
            for j in selected_idx:
                penalty = max(penalty, _cos(vecs[i], vecs[j]))
            score = sims_q[i] - 0.6 * penalty
            if score > best_score:
                best_score = score
                best_i = i
        if best_i == -1:
            # если всё отфильтровали по источникам — снимем ограничение
            for i in range(len(cand)):
                if i in selected_idx:
                    continue
                penalty = 0.0
                for j in selected_idx:
                    penalty = max(penalty, _cos(vecs[i], vecs[j]))
                score = sims_q[i] - 0.6 * penalty
                if score > best_score:
                    best_score = score
                    best_i = i
        if best_i == -1:
            break
        selected_idx.append(best_i)
        used_sources.add(cand[best_i]["src"])

    pieces: List[str] = []
    total = 0
    for i in selected_idx:
        chunk = cand[i]["text"]
        need = max_chars - total
        if need <= 0:
            break
        if len(chunk) <= need:
            pieces.append(chunk)
            total += len(chunk)
        else:
            # обрезаем по предложениям
            sents = _split_ru_sents(chunk)
            acc, cur = [], 0
            for s in sents:
                if cur + len(s) + 1 > need:
                    break
                acc.append(s)
                cur += len(s) + 1
            if acc:
                pieces.append(" ".join(acc).strip() + "…")
                total += cur

    ctx = "\n\n---\n\n".join(pieces).strip()
    meta = [{"source": cand[i]["src"], "title": cand[i]["title"], "score": sims_q[i], "payload": cand[i]["payload"]} for i in selected_idx]

    if RAG_TRACE:
        print(f"[RAG] query='{query[:80]}', pieces={len(pieces)}, chars={len(ctx)}")

    return ctx, meta

# --- Сжатие контекста (опционально)
async def compress_context(ctx: str, query: str, *, max_chars: int) -> str:
    if not ctx or not RAG_COMPRESS:
        return ctx
    try:
        from openai import OpenAI  # type: ignore
        cli = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)
        sys = "Ты лаконично сжимаешь русские выдержки по психологии и КПТ, сохраняя факты и практические шаги."
        usr = (
            "Запрос пользователя:\н"
            f"{query}\n\n"
            "Ниже выдержки из статей/руководств (может быть несколько фрагментов):\n"
            f"{ctx}\n\n"
            "Сожми мысли в один-два абзаца понятным русским языком, без источников и ссылок. "
            "Если есть короткие практические шаги — оставь их."
        )
        r = cli.chat.completions.create(
            model=RAG_COMPRESS_MODEL,
            temperature=0.2,
            messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
        )
        out = (r.choices[0].message.content or "").strip()
        if not out:
            return ctx
        return (out[:max_chars].rstrip()+"…") if len(out) > max_chars else out
    except Exception:
        return ctx

# --- Публичный API
async def search_with_meta(query: str, k: int = 6, max_chars: int = 1200, lang: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
    ctx, meta = await build_context_mmr(query, initial_limit=max(16, k*4), select=k, max_chars=max_chars, lang=lang)
    if RAG_COMPRESS:
        limit = min(max_chars, RAG_MAX_CHARS)
        ctx = await compress_context(ctx, query, max_chars=limit)
    return ctx, meta

async def search(query: str, k: int = 6, max_chars: int = 1200, lang: Optional[str] = None) -> str:
    ctx, _ = await search_with_meta(query, k=k, max_chars=max_chars, lang=lang)
    return ctx

__all__ = ["search", "search_with_meta", "embed"]
