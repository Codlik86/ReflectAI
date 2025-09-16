# -*- coding: utf-8 -*-
"""
RAG (Qdrant) helper: silent context building with MMR reranking + optional compression.

Public API:
- async def search(query: str, k: int = 4, max_chars: int = 1400) -> str
- async def search_with_meta(query: str, k: int = 4, max_chars: int = 1400) -> (str, list[dict])
"""
from __future__ import annotations

import os
RAG_LANG = os.getenv('RAG_LANG') or None

def _filter_lang(hits, lang):
    """Оставляет только документы с payload['lang']==lang. Если lang пустой — возвращает как есть."""
    if not lang:
        return hits
    out = []
    for h in (hits or []):
        payload = None
        # qdrant-client может отдавать hit как объект с .payload или как dict
        if hasattr(h, "payload"):
            payload = getattr(h, "payload", None)
        elif isinstance(h, dict):
            payload = h.get("payload")
        if isinstance(payload, dict) and payload.get("lang") == lang:
            out.append(h)
    return out

import math
from typing import Any, Dict, List, Optional, Tuple

# --- Qdrant client -----------------------------------------------------------
try:
    from app.qdrant_client import get_client  # type: ignore
except Exception:
    from qdrant_client import QdrantClient  # type: ignore
    def get_client() -> "QdrantClient":  # type: ignore
        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        api_key = os.getenv("QDRANT_API_KEY")
        return QdrantClient(url=url, api_key=api_key)  # type: ignore

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus")
RAG_TRACE = os.getenv("RAG_TRACE", "0") == "1"

# --- Embeddings --------------------------------------------------------------
def _get_embedder():
    """
    Returns callable embed(texts: List[str]) -> List[List[float]].
    Prefers OpenAI (respects OPENAI_BASE_URL), falls back to sentence-transformers if installed.
    """
    prov = os.getenv("EMBED_PROVIDER", "openai").lower()
    if prov == "openai":
        try:
            from openai import OpenAI  # type: ignore
            client = OpenAI(
                base_url=os.getenv("OPENAI_BASE_URL") or None,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
            model = os.getenv("EMBED_MODEL", "text-embedding-3-small")
            def _emb(texts: List[str]) -> List[List[float]]:
                res = client.embeddings.create(model=model, input=texts)
                return [d.embedding for d in res.data]
            return _emb
        except Exception:
            pass
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        mname = os.getenv("SBERT_MODEL", "intfloat/multilingual-e5-small")
        model = SentenceTransformer(mname)
        def _emb(texts: List[str]) -> List[List[float]]:
            return model.encode(texts).tolist()
        return _emb
    except Exception as e:
        raise RuntimeError(
            "No embedding provider available. Set OPENAI_API_KEY (and OPENAI_BASE_URL if proxy) "
            "or install sentence-transformers."
        ) from e

_EMBED = None
async def embed(text_or_texts: str | List[str]) -> List[float] | List[List[float]]:
    global _EMBED
    if _EMBED is None:
        _EMBED = _get_embedder()
    if isinstance(text_or_texts, list):
        return _EMBED(text_or_texts)
    return _EMBED([text_or_texts])[0]

# --- helpers -----------------------------------------------------------------
def _text_from_payload(pl: Dict[str, Any]) -> str:
    t = pl.get("text")
    if t:
        return str(t)
    for k in ("page_content", "chunk", "content"):
        if pl.get(k):
            return str(pl[k])
    return ""

def _split_sents_ru(text: str) -> List[str]:
    import re
    parts = re.split(r'(?<=[\.!\?])\s+(?=[А-ЯA-ZЁ])', text.strip())
    return [p.strip() for p in parts if p.strip()]

def _dot(a, b): return sum((x*y) for x, y in zip(a, b))
def _norm(a): return math.sqrt(sum((x*x) for x in a)) or 1.0
def _cosine(a, b): return _dot(a, b) / (_norm(a) * _norm(b))

# --- core: MMR ---------------------------------------------------------------
async def build_context_mmr(query: str, *, initial_limit: int = 24, select: int = 8, max_chars: int = 1400, lambda_mult: float = 0.6, filter_by: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict[str, Any]]]:
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue  # type: ignore

    client = get_client()
    qvec = await embed(query)

    qfilter = None
    if filter_by:
        must = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filter_by.items()]  # type: ignore
        qfilter = Filter(must=must)  # type: ignore

    hits = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=qvec,  # type: ignore[arg-type]
        limit=initial_limit,
        query_filter=qfilter,
        with_payload=True,
        with_vectors=False,
    )

    cand = []
    for h in hits:
        pl = h.payload or {}
        txt = _text_from_payload(pl).strip()
        if not txt:
            continue
        cand.append({
            "text": txt,
            "src": pl.get("source") or pl.get("file") or "",
            "title": pl.get("title") or "",
            "hit_score": float(getattr(h, "score", 0.0)),
        })
    if not cand:
        return "", []

    texts = [c["text"] for c in cand]
    vecs = await embed(texts)  # type: ignore[assignment]
    sims_q = [_cosine(qvec, v) for v in vecs]  # type: ignore[arg-type]

    selected_idx: List[int] = []
    used_sources = set()

    while len(selected_idx) < min(select, len(cand)):
        best_i, best_val = None, -1e9
        for i, v in enumerate(vecs):
            if i in selected_idx:
                continue
            max_sim_to_sel = max((_cosine(v, vecs[j]) for j in selected_idx), default=0.0)  # type: ignore[arg-type]
            src_penalty = 0.05 if cand[i]["src"] in used_sources else 0.0
            mmr = lambda_mult * sims_q[i] - (1.0 - lambda_mult) * max_sim_to_sel - src_penalty
            if mmr > best_val:
                best_val, best_i = mmr, i
        if best_i is None:
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
            sents = _split_sents_ru(chunk)
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
    meta = [{"source": cand[i]["src"], "title": cand[i]["title"], "score": sims_q[i]} for i in selected_idx]

    if RAG_TRACE:
        try:
            print("[RAG.mmr] query=", query[:60].replace("\n", " "),
                  "ctx_len=", len(ctx),
                  "picked=", [m["source"] for m in meta])
        except Exception:
            pass
    return ctx, meta

# --- compression flags (optional) ---
RAG_COMPRESS = os.getenv("RAG_COMPRESS","0") == "1"
RAG_COMPRESS_MODEL = os.getenv("RAG_COMPRESS_MODEL","gpt-4o-mini")
RAG_MAX_CHARS = int(os.getenv("RAG_MAX_CHARS","1400"))

def _get_chat_client():
    from openai import OpenAI  # type: ignore
    return OpenAI(
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=os.getenv("OPENAI_API_KEY"),
    )

async def compress_context(ctx: str, query: str, *, max_chars: int = 1200) -> str:
    """
    Сжимает многоисточниковый контекст в один компактный связный кусок на русском.
    Не добавляет ссылки/источники. Тёплый нейтральный тон.
    """
    if not ctx or len(ctx) <= max_chars:
        return ctx
    try:
        client = _get_chat_client()
        prompt_sys = (
            "Ты помогаешь эмоциональной поддержке на русском. "
            "Дано сырьё из нескольких фрагментов (из статей/руководств). "
            "Сожми это в один связный фрагмент, релевантный запросу. "
            "Пиши просто и тепло, без категоричности и диагнозов. "
            "Не упоминай источники/ссылки. Без ссылочных меток. "
            f"До ~{max_chars} символов. Только текст."
        )
        prompt_usr = (
            "Запрос пользователя:\\n"
            f"{query}\\n\\n"
            "Сырой контекст (фрагменты):\\n"
            f"{ctx}\\n\\n"
            "Сожми и переформулируй в один понятный абзац или два, сохранив ключевые идеи и мягкие практические шаги."
        )
        resp = client.chat.completions.create(
            model=RAG_COMPRESS_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": prompt_sys},
                {"role": "user", "content": prompt_usr},
            ],
        )
        txt = (resp.choices[0].message.content or "").strip()
        if not txt:
            return ctx
        return (txt[:max_chars].rstrip() + "…") if len(txt) > max_chars else txt
    except Exception:
        return ctx

# --- public API --------------------------------------------------------------
async def search_with_meta(query: str, k: int = 4, max_chars: int = 1400, **kwargs) -> Tuple[str, List[Dict[str, Any]]]:
    return await build_context_mmr(
        query,
        initial_limit=max(16, k * 4),
        select=k,
        max_chars=max_chars,
    )

async def search(query: str, k: int = 4, max_chars: int = 1400, **kwargs) -> str:
    """
    Возвращает строку-контекст. Внутри: MMR-сборка -> (опционально) компрессор.
    """
    ctx, _ = await search_with_meta(query, k=k, max_chars=max_chars)
    if RAG_COMPRESS:
        limit = min(max_chars, RAG_MAX_CHARS)
        if limit < 600:
            limit = int(max_chars * 0.85)
        ctx = await compress_context(ctx, query, max_chars=limit)
    return ctx