# app/meditations.py
import os
from typing import Dict, List, Optional, Tuple

# Категории: сон / тревога / восстановление
MEDITATIONS: Dict[str, dict] = {
    "sleep": {
        "title": "Сон",
        "emoji": "😴",
        "items": {
            "soft_sleep": {
                "title": "Мягкое засыпание",
                "duration": "04:00",
                "url": "https://storage.yandexcloud.net/reflectai-audio/sleep.soft_sleep.mp3",
            },
            "478_breath": {
                "title": "Дыхание 4–7–8",
                "duration": "03:59",
                "url": "https://storage.yandexcloud.net/reflectai-audio/sleep.478_breath.mp3",
            },
        },
    },
    "anxiety": {
        "title": "Тревога",
        "emoji": "😟",
        "items": {
            "panic_support": {
                "title": "Кризисная поддержка (Паническая атака)",
                "duration": "02:43",   # поставь свою фактическую длительность
                "url": "https://storage.yandexcloud.net/reflectai-audio/panic.attack.mp3",
            },
            "ground_54321": {
                "title": "Заземление 5-4-3-2-1",
                "duration": "03:30",
                "url": "https://storage.yandexcloud.net/reflectai-audio/breath54321.mp3",
            },
            "box_breath": {
                "title": "Квадратное дыхание",
                "duration": "03:16",
                "url": "https://storage.yandexcloud.net/reflectai-audio/breath4444.mp3",
            },
        },
    },
    "recovery": {
        "title": "Восстановление",
        "emoji": "🌿",
        "items": {
            "body_scan": {
                "title": "Скан тела",
                "duration": "03:01",
                "url": "https://storage.yandexcloud.net/reflectai-audio/recovery.body_scan.mp3",
            },
            "mini_pause": {
                "title": "Микро-пауза",
                "duration": "02:31",
                "url": "https://storage.yandexcloud.net/reflectai-audio/recovery.mini_pause.mp3",
            },
        },
    },
}

# --- ENV overrides (оставляют дефолты, если переменная не задана) ---
_ENV_MAP = {
    # sleep
    "sleep.soft_sleep": "MEDIT_SLEEP_SOFT_URL",
    "sleep.478_breath": "MEDIT_SLEEP_478_URL",
    # anxiety
    "anxiety.ground_54321": "MEDIT_ANX_GROUND_URL",
    "anxiety.box_breath": "MEDIT_ANX_BOX_URL",
    "anxiety.panic_support": "MEDIT_ANX_PANIC_URL",
    # recovery
    "recovery.body_scan": "MEDIT_RECOVERY_BODYSCAN_URL",
    "recovery.mini_pause": "MEDIT_RECOVERY_MINIPAUSE_URL",
}

def _apply_env_overrides() -> None:
    for dotted_key, env_name in _ENV_MAP.items():
        cat_id, item_id = dotted_key.split(".", 1)
        val = os.getenv(env_name, "").strip()
        if not val:
            continue
        try:
            MEDITATIONS[cat_id]["items"][item_id]["url"] = val
        except KeyError:
            # Если ключей нет — тихо пропускаем; не ломаем загрузку
            pass

_apply_env_overrides()

# --- Твой исходный контракт (оставляю как есть) ---

def get_categories() -> List[Tuple[str, str]]:
    """[(cat_id, 'emoji title')] — для кнопок."""
    out = []
    for cid, c in MEDITATIONS.items():
        out.append((cid, f"{c['emoji']} {c['title']}"))
    return out

def get_items(cat_id: str) -> List[Tuple[str, str, Optional[str]]]:
    """[(item_id, 'title • duration', url_or_None)]."""
    cat = MEDITATIONS.get(cat_id, {})
    items = cat.get("items", {})
    out = []
    for iid, meta in items.items():
        label = f"{meta['title']} • {meta['duration']}"
        out.append((iid, label, meta.get("url")))
    return out

def get_item(cat_id: str, item_id: str) -> Optional[dict]:
    return MEDITATIONS.get(cat_id, {}).get("items", {}).get(item_id)

# --- Доп. контракт (v2): словари, как в exercises.py ---

def get_categories_dict() -> List[Dict[str, str]]:
    """[{id,title,emoji}]"""
    out: List[Dict[str, str]] = []
    for cid, c in MEDITATIONS.items():
        out.append({"id": cid, "title": c.get("title", cid), "emoji": c.get("emoji", "")})
    return out

def get_items_dict(cat_id: str) -> List[Dict[str, Optional[str]]]:
    """[{id,title,duration,url}]"""
    cat = MEDITATIONS.get(cat_id, {})
    items = cat.get("items", {})
    out: List[Dict[str, Optional[str]]] = []
    for iid, meta in items.items():
        out.append({
            "id": iid,
            "title": meta.get("title", iid),
            "duration": meta.get("duration"),
            "url": meta.get("url"),
        })
    return out

def get_item_full(cat_id: str, item_id: str) -> Optional[Dict[str, Optional[str]]]:
    """Полная карточка трека: {id,title,duration,url}"""
    meta = get_item(cat_id, item_id)
    if not meta:
        return None
    return {
        "id": item_id,
        "title": meta.get("title", item_id),
        "duration": meta.get("duration"),
        "url": meta.get("url"),
    }

__all__ = [
    "MEDITATIONS",
    "get_categories", "get_items", "get_item",
    "get_categories_dict", "get_items_dict", "get_item_full",
]
