# app/diag.py
import os
from fastapi import APIRouter
import httpx

router = APIRouter()

def _mask(s: str, keep: int = 24) -> str:
    if not s:
        return ""
    return s if len(s) <= keep else s[:keep] + "…"

@router.get("/qdrant")
async def qdrant_diag():
    url = os.getenv("QDRANT_URL", "").strip()
    api_key = os.getenv("QDRANT_API_KEY", "").strip()
    coll = os.getenv("QDRANT_COLLECTION", "").strip() or "reflectai_corpus"

    data = {
        "env": {
            "QDRANT_URL_set": bool(url),
            "QDRANT_URL_masked": _mask(url),
            "QDRANT_API_KEY_set": bool(api_key),
            "QDRANT_COLLECTION": coll,
        },
        "readyz": {"ok": False, "error": None, "text": None},
        "collections": {"ok": False, "error": None, "names": []},
    }

    if not url:
        data["readyz"]["error"] = "QDRANT_URL is empty"
        data["collections"]["error"] = "QDRANT_URL is empty"
        return data

    headers = {"api-key": api_key} if api_key else {}
    timeout = httpx.Timeout(8.0, connect=8.0)

    # /readyz
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            r = await client.get(f"{url}/readyz", headers=headers)
            data["readyz"]["ok"] = (r.status_code == 200)
            data["readyz"]["text"] = r.text[:200]
            if not data["readyz"]["ok"]:
                data["readyz"]["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        data["readyz"]["error"] = f"{type(e).__name__}: {e}"

    # /collections
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            r = await client.get(f"{url}/collections", headers=headers)
            if r.status_code == 200:
                j = r.json()
                names = [c["name"] for c in j.get("result", {}).get("collections", [])]
                data["collections"] = {"ok": True, "error": None, "names": names}
            else:
                data["collections"]["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        data["collections"]["error"] = f"{type(e).__name__}: {e}"

    return data

@router.get("/env")
async def env_diag():
    keys = [
        "TELEGRAM_BOT_TOKEN", "WEBHOOK_BASE_URL", "WEBHOOK_SECRET",
        "QDRANT_URL", "QDRANT_COLLECTION",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY"
    ]
    out = {}
    for k in keys:
        val = os.getenv(k, "")
        out[k] = {"set": bool(val), "masked": _mask(val, keep=6)}
    return out
