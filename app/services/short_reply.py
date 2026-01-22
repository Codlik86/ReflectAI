from __future__ import annotations

from typing import Optional
import re

_URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def is_short_reply(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _URL_RE.search(t):
        return False
    if "\n" in t:
        return False

    # Conservative heuristics: high precision over recall.
    words = [w for w in _WS_RE.split(t) if w]
    word_count = len(words)
    char_count = len(t)

    if word_count <= 2 and char_count <= 18:
        return True
    if char_count <= 12:
        return True

    # Avoid tagging standalone sentences or emphatic questions.
    if ("?" in t or "!" in t) and char_count > 6:
        return False
    if "." in t and char_count > 10:
        return False

    return False


def normalize_short_reply(text: str, last_bot_turn: Optional[str]) -> str:
    t = (text or "").strip()
    if not t:
        return t
    if not is_short_reply(t):
        return t

    last = (last_bot_turn or "").strip()
    if last:
        last = last.replace("\r", " ").replace("\n", " ")
        last = _WS_RE.sub(" ", last)
        if len(last) > 120:
            last = last[:119] + "…"
        return f"Короткий ответ пользователя на предыдущий вопрос ({last}): {t}"
    return f"Короткий ответ пользователя на предыдущий вопрос: {t}"


__all__ = ["is_short_reply", "normalize_short_reply"]
