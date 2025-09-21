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
                "duration": "12:00",
                "url": "https://reflectai-audio.storage.yandexcloud.net/sleep_soft_sleep.mp3",
            },
            "478_breath": {
                "title": "Дыхание 4–7–8",
                "duration": "03:30",
                "url": "https://reflectai-audio.storage.yandexcloud.net/sleep_breath_4_7_8.mp3",
            },
        },
    },
    "anxiety": {
        "title": "Тревога",
        "emoji": "😟",
        "items": {
            "ground_54321": {
                "title": "Заземление 5-4-3-2-1",
                "duration": "04:00",
                "url": "https://storage.yandexcloud.net/reflectai-audio/1)%20trevoga54321.mp3",
            },
            "box_breath": {
                "title": "Квадратное дыхание",
                "duration": "05:00",
                "url": "https://storage.yandexcloud.net/reflectai-audio/2)%20dyhanie4444.mp3",
            },
        },
    },
    "recovery": {
        "title": "Восстановление",
        "emoji": "🌿",
        "items": {
            "body_scan": {
                "title": "Скан тела",
                "duration": "08:00",
                "url": "https://reflectai-audio.storage.yandexcloud.net/recovery_body_scan.mp3",
            },
            "mini_pause": {
                "title": "Микро-пауза",
                "duration": "02:00",
                "url": "https://reflectai-audio.storage.yandexcloud.net/recovery_mini_pause.mp3",
            },
        },
    },
}

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
