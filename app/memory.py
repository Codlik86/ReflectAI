# app/memory.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime

from sqlalchemy import text
from app.db import db_session

# ===== Настройки окна памяти =====
# Сколько последних реплик подтягиваем в контекст LLM
DEFAULT_MEMORY_LIMIT = 60

# ---------------------------------------------------------------------------
# ВНУТРЕННИЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (не экспортируем)
# ---------------------------------------------------------------------------

def _get_user_id_by_tg(tg_id: str | int) -> Optional[int]:
    """Вернуть users.id по tg_id, если есть."""
    with db_session() as s:
        return s.execute(
            text("SELECT id FROM users WHERE tg_id = :tg"),
            {"tg": str(tg_id)}
        ).scalar()

def _ensure_user_and_get_id(tg_id: str | int) -> int:
    """
    Гарантированно вернуть users.id по tg_id.
    Создаёт запись в users, если её ещё нет.
    """
    with db_session() as s:
        uid = s.execute(
            text("SELECT id FROM users WHERE tg_id = :tg"),
            {"tg": str(tg_id)}
        ).scalar()

        if uid is None:
            # privacy_level по умолчанию — 'insights' (как и раньше)
            s.execute(
                text("INSERT INTO users (tg_id, privacy_level) VALUES (:tg, 'insights')"),
                {"tg": str(tg_id)}
            )
            uid = s.execute(
                text("SELECT id FROM users WHERE tg_id = :tg"),
                {"tg": str(tg_id)}
            ).scalar()
        s.commit()
        return int(uid)

# ---------------------------------------------------------------------------
# ПУБЛИЧНОЕ API ДЛЯ bot.py  (Единственный каноничный интерфейс)
# ---------------------------------------------------------------------------

def save_user_message(tg_id: str | int, text_value: str) -> None:
    """
    Сохранить входящую реплику пользователя в bot_messages.
    """
    if not text_value:
        return
    uid = _ensure_user_and_get_id(tg_id)
    with db_session() as s:
        s.execute(
            text("""
                INSERT INTO bot_messages (user_id, role, text, created_at)
                VALUES (:uid, 'user', :txt, CURRENT_TIMESTAMP)
            """),
            {"uid": uid, "txt": text_value}
        )
        s.commit()

def save_bot_message(tg_id: str | int, text_value: str) -> None:
    """
    Сохранить исходящую реплику бота в bot_messages.
    """
    if not text_value:
        return
    uid = _ensure_user_and_get_id(tg_id)
    with db_session() as s:
        s.execute(
            text("""
                INSERT INTO bot_messages (user_id, role, text, created_at)
                VALUES (:uid, 'bot', :txt, CURRENT_TIMESTAMP)
            """),
            {"uid": uid, "txt": text_value}
        )
        s.commit()

def get_recent_messages(tg_id: str | int, limit: int = DEFAULT_MEMORY_LIMIT) -> List[Dict[str, str]]:
    """
    Вернуть последние N сообщений диалога пользователя в формате:
    [
      {"role": "user"|"bot", "text": "..."},
      ...
    ]
    Порядок — от старых к новым (хронологический), чтобы удобно подмешивать в промпт.
    """
    uid = _ensure_user_and_get_id(tg_id)
    with db_session() as s:
        rows = s.execute(
            text("""
                SELECT role, text
                FROM bot_messages
                WHERE user_id = :uid
                ORDER BY created_at DESC, id DESC
                LIMIT :lim
            """),
            {"uid": uid, "lim": int(limit)}
        ).fetchall()

    # Разворачиваем: хотим хронологию (старые → новые)
    out = [{"role": r[0], "text": r[1]} for r in rows][::-1]
    return out

# Явно укажем, что экспортируем наружу
__all__ = [
    "save_user_message",
    "save_bot_message",
    "get_recent_messages",
]
