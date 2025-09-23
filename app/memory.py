# app/memory.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from datetime import datetime, timedelta, date

from sqlalchemy import text
from app.db import db_session

# ===== Константы «окна памяти» =====
MAX_RECENT_MSGS = 24         # сколько последних реплик подмешиваем в контекст
MEMORY_BACK_DAYS = 21        # за сколько дней смотрим сообщения/саммари

# Внутренний анти-дубль (в пределах одного процесса)
_seen_message_ids: set[int] = set()

@dataclass
class MemoryChunk:
    role: str   # "user" | "bot"
    text: str
    ts: datetime

# --- вспомогалки -------------------------------------------------------------

def _get_user_id_by_tg(tg_id: int | str) -> Optional[int]:
    with db_session() as s:
        return s.execute(
            text("SELECT id FROM users WHERE tg_id = :tg"),
            {"tg": str(tg_id)}
        ).scalar()

def _ensure_user(tg_id: int | str) -> int:
    uid = _get_user_id_by_tg(tg_id)
    if uid:
        return uid
    with db_session() as s:
        s.execute(
            text("INSERT INTO users (tg_id) VALUES (:tg) ON CONFLICT DO NOTHING"),
            {"tg": str(tg_id)}
        )
        uid = s.execute(
            text("SELECT id FROM users WHERE tg_id = :tg"),
            {"tg": str(tg_id)}
        ).scalar()
        s.commit()
        return uid

# --- запись сообщений --------------------------------------------------------

def remember_user_message(tg_id: int | str, text_msg: str, *, message_id: Optional[int] = None) -> None:
    """
    Записать входящее сообщение пользователя в журнал.
    Анти-дубль по message_id (если передали).
    """
    if not text_msg:
        return
    if message_id is not None and message_id in _seen_message_ids:
        return
    uid = _ensure_user(tg_id)
    with db_session() as s:
        s.execute(
            text("""
                INSERT INTO bot_messages (user_id, role, text)
                VALUES (:uid, 'user', :txt)
            """),
            {"uid": uid, "txt": text_msg}
        )
        s.commit()
    if message_id is not None:
        _seen_message_ids.add(message_id)

def remember_bot_message(tg_id: int | str, text_msg: str) -> None:
    """Записать исходящее сообщение бота."""
    if not text_msg:
        return
    uid = _ensure_user(tg_id)
    with db_session() as s:
        s.execute(
            text("""
                INSERT INTO bot_messages (user_id, role, text)
                VALUES (:uid, 'bot', :txt)
            """),
            {"uid": uid, "txt": text_msg}
        )
        s.commit()

# --- получение контекста -----------------------------------------------------

def get_recent_dialog(tg_id: int | str,
                      max_messages: int = MAX_RECENT_MSGS,
                      back_days: int = MEMORY_BACK_DAYS) -> List[MemoryChunk]:
    """Последние реплики диалога за back_days (user+bot), максимум max_messages."""
    uid = _get_user_id_by_tg(tg_id)
    if not uid:
        return []
    since = datetime.utcnow() - timedelta(days=back_days)
    with db_session() as s:
        rows = s.execute(
            text("""
                SELECT role, text, created_at
                FROM bot_messages
                WHERE user_id = :uid AND created_at >= :since
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"uid": uid, "since": since, "lim": max_messages}
        ).fetchall()
    # возвращаем в хронологическом порядке (старые → новые)
    result = [MemoryChunk(role=r[0], text=r[1], ts=r[2]) for r in rows][::-1]
    return result

def get_latest_summary(tg_id: int | str) -> Optional[str]:
    """Последний дневной саммари (если есть) за back_days."""
    uid = _get_user_id_by_tg(tg_id)
    if not uid:
        return None
    since_day = date.today() - timedelta(days=MEMORY_BACK_DAYS)
    with db_session() as s:
        row = s.execute(
            text("""
                SELECT summary
                FROM bot_daily_summaries
                WHERE user_id = :uid AND day >= :since
                ORDER BY day DESC
                LIMIT 1
            """),
            {"uid": uid, "since": since_day}
        ).fetchone()
    return row[0] if row else None

def upsert_daily_summary(tg_id: int | str, day: date, summary_text: str) -> None:
    """Создать/обновить саммари дня (например, по кнопке «итоги дня»)."""
    uid = _ensure_user(tg_id)
    with db_session() as s:
        s.execute(
            text("""
                INSERT INTO bot_daily_summaries (user_id, day, summary)
                VALUES (:uid, :day, :txt)
                ON CONFLICT (user_id, day)
                DO UPDATE SET summary = EXCLUDED.summary,
                              created_at = now()
            """),
            {"uid": uid, "day": day, "txt": summary_text}
        )
        s.commit()

def build_memory_context(tg_id: int | str) -> Dict[str, str]:
    """
    Собирает строку контекста: последний саммари + последние реплики.
    Возвращай как dict, чтобы удобно подмешивать в промпт.
    """
    pieces: List[str] = []
    last_sum = get_latest_summary(tg_id)
    if last_sum:
        pieces.append(f"Последний дневной итог:\n{last_sum.strip()}")

    recent = get_recent_dialog(tg_id)
    if recent:
        dialog_lines = []
        for ch in recent:
            role = "Ты" if ch.role == "user" else "Помни"
            dialog_lines.append(f"{role}: {ch.text.strip()}")
        pieces.append("Недавний диалог:\n" + "\n".join(dialog_lines))

    return {"memory_context": "\n\n".join(pieces).strip()}
