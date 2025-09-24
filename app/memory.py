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

# В БД исторически роль бота хранилась как 'bot'. Для LLM нормализуем в 'assistant'.
_DB_ASSISTANT_ROLE = "bot"
_LLM_ASSISTANT_ROLE = "assistant"
_USER_ROLE = "user"

# Разрешённые значения приватности (для совместимости оставляем 'insights')
_ALLOWED_PRIVACY = {"ask", "auto", "off", "insights"}
_DEFAULT_PRIVACY = "insights"


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
                text("INSERT INTO users (tg_id, privacy_level, created_at) VALUES (:tg, 'insights', CURRENT_TIMESTAMP)"),
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
                VALUES (:uid, :role, :txt, CURRENT_TIMESTAMP)
            """),
            {"uid": uid, "role": _USER_ROLE, "txt": text_value}
        )
        s.commit()


def save_bot_message(tg_id: str | int, text_value: str) -> None:
    """
    Сохранить исходящую реплику бота в bot_messages.
    Исторически мы писали role='bot'. Сохраняем совместимость.
    """
    if not text_value:
        return
    uid = _ensure_user_and_get_id(tg_id)
    with db_session() as s:
        s.execute(
            text("""
                INSERT INTO bot_messages (user_id, role, text, created_at)
                VALUES (:uid, :role, :txt, CURRENT_TIMESTAMP)
            """),
            {"uid": uid, "role": _DB_ASSISTANT_ROLE, "txt": text_value}
        )
        s.commit()


def get_recent_messages(tg_id: str | int, limit: int = DEFAULT_MEMORY_LIMIT) -> List[Dict[str, str]]:
    """
    Вернуть последние N сообщений диалога пользователя в формате:
    [
      {"role": "user"|"assistant", "text": "..."},
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
    out: List[Dict[str, str]] = []
    for role, txt in rows[::-1]:
        # Нормализуем роли для LLM
        if role == _DB_ASSISTANT_ROLE:
            norm_role = _LLM_ASSISTANT_ROLE
        elif role == _USER_ROLE:
            norm_role = _USER_ROLE
        else:
            # На всякий случай всё неизвестное считаем ответом ассистента
            norm_role = _LLM_ASSISTANT_ROLE
        out.append({"role": norm_role, "text": txt})
    return out


# ---------------------------------------------------------------------------
# ДОП. API: приватность и очистка (ожидается bot.py)
# ---------------------------------------------------------------------------

def get_privacy(tg_id: str | int) -> str:
    """
    Вернуть текущий уровень приватности пользователя.
    Используется колонка users.privacy_level (совместимо со старой схемой).
    Возможные значения: 'ask' | 'auto' | 'off' | 'insights' (по умолчанию).
    """
    with db_session() as s:
        val = s.execute(
            text("SELECT privacy_level FROM users WHERE tg_id = :tg"),
            {"tg": str(tg_id)}
        ).scalar()
    return (val or _DEFAULT_PRIVACY) if (val or _DEFAULT_PRIVACY) in _ALLOWED_PRIVACY else _DEFAULT_PRIVACY


def set_privacy(tg_id: str | int, value: str) -> None:
    """
    Установить уровень приватности пользователя.
    """
    value = value if value in _ALLOWED_PRIVACY else _DEFAULT_PRIVACY
    uid = _ensure_user_and_get_id(tg_id)
    with db_session() as s:
        s.execute(
            text("UPDATE users SET privacy_level = :val WHERE id = :uid"),
            {"val": value, "uid": uid}
        )
        s.commit()


def purge_user_data(tg_id: str | int) -> int:
    """
    Удалить все сообщения пользователя из bot_messages.
    Возвращает количество удалённых строк.
    """
    uid = _ensure_user_and_get_id(tg_id)
    with db_session() as s:
        res = s.execute(
            text("DELETE FROM bot_messages WHERE user_id = :uid"),
            {"uid": uid}
        )
        s.commit()
        # rowcount может быть None на некоторых драйверах — нормализуем
        return int(getattr(res, "rowcount", 0) or 0)


# Явно укажем, что экспортируем наружу
__all__ = [
    "save_user_message",
    "save_bot_message",
    "get_recent_messages",
    "get_privacy",
    "set_privacy",
    "purge_user_data",
]
