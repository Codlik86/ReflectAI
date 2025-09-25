# scripts/qdrant_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from pathlib import Path
from collections import Counter
from typing import Any, Dict
import argparse
import csv

# --- ensure project root on sys.path ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

# --- Qdrant client: try local helper, else build directly ---
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "reflectai_corpus_v2").strip()
QDRANT_URL = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip() or None

try:
    from app.qdrant_client import get_client  # our helper
    _get_client = get_client
except Exception:
    from qdrant_client import QdrantClient

    def _get_client() -> "QdrantClient":
        if not QDRANT_URL:
            raise RuntimeError("QDRANT_URL is not set")
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60.0)

# --- scrolling ---
def scroll_points(collection: str, batch: int = 1000):
    cli = _get_client()
    offset = None
    while True:
        points, offset = cli.scroll(
            collection_name=collection,
            with_payload=True,
            with_vectors=False,
            offset=offset,
            limit=batch,
        )
        for p in points:
            yield p
        if not offset:
            break

def collect_stats(collection: str):
    cli = _get_client()

    # collection info (SDK fields may differ by version)
    info = cli.get_collection(collection)
    status = getattr(info, "status", None)
    optimizer_status = getattr(info, "optimizer_status", None)
    try:
        indexed_vectors_count = getattr(info, "indexed_vectors_count", None)
    except Exception:
        indexed_vectors_count = None
    segments_count = getattr(info, "segments_count", None)

    cnt_by_source, cnt_by_lang, cnt_by_tag = Counter(), Counter(), Counter()
    orphan_payloads = 0
    total_points = 0

    for p in scroll_points(collection):
        total_points += 1
        payload: Dict[str, Any] = p.payload or {}

        src = payload.get("source") or "unknown"
        cnt_by_source[src] += 1

        lang = payload.get("lang") or "unknown"
        cnt_by_lang[lang] += 1

        tags = payload.get("tags") or []
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t.strip():
                    cnt_by_tag[t.strip()] += 1

        if not payload.get("text"):
            orphan_payloads += 1

    return {
        "status": status,
        "optimizer_status": optimizer_status,
        "indexed_vectors_count": indexed_vectors_count,
        "segments_count": segments_count,
        "total_points": total_points,
        "cnt_by_source": cnt_by_source,
        "cnt_by_lang": cnt_by_lang,
        "cnt_by_tag": cnt_by_tag,
        "orphan_payloads": orphan_payloads,
    }

def print_top(counter: Counter, title: str, top_n: int):
    print(f"\n{title} (top {top_n}):")
    for k, v in counter.most_common(top_n):
        print(f"{v:>6}  {k}")

def main():
    ap = argparse.ArgumentParser(description="Qdrant collection stats")
    ap.add_argument("--collection", default=QDRANT_COLLECTION,
                    help="Collection name (default from env QDRANT_COLLECTION)")
    ap.add_argument("--top", type=int, default=20, help="How many top items to show (default 20)")
    ap.add_argument("--csv", type=str, default="", help="Optional path to export per-source counts as CSV")
    args = ap.parse_args()

    stats = collect_stats(args.collection)

    print("=== Qdrant Collection Stats ===")
    print(f"collection             : {args.collection}")
    print(f"status / optimizer     : {stats['status']} / {stats['optimizer_status']}")
    if stats["indexed_vectors_count"] is not None:
        print(f"points / indexed       : {stats['total_points']} / {stats['indexed_vectors_count']}")
    else:
        print(f"points                 : {stats['total_points']}")
    if stats["segments_count"] is not None:
        print(f"segments               : {stats['segments_count']}")
    print(f"orphan payloads (no 'text'): {stats['orphan_payloads']}")

    print_top(stats["cnt_by_source"], "Sources", args.top)
    print_top(stats["cnt_by_lang"], "Languages", args.top)
    print_top(stats["cnt_by_tag"], "Tags", args.top)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["source", "count"])
            for src, n in stats["cnt_by_source"].most_common():
                w.writerow([src, n])
        print(f"\nCSV saved: {args.csv}")

if __name__ == "__main__":
    main()
