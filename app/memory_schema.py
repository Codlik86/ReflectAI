# app/memory_schema.py
# -*- coding: utf-8 -*-
"""
Автосоздание таблиц памяти (bot_messages, bot_daily_summaries).
Работает и с SQLite, и с PostgreSQL.
"""

from sqlalchemy import text
from app.db import db_session, _engine


def _is_sqlite() -> bool:
    try:
        return _engine.url.get_backend_name() == "sqlite"
    except Exception:
        return str(_engine.url).startswith("sqlite")


SQL_SQLITE = """
-- Сообщения диалога
CREATE TABLE IF NOT EXISTS bot_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       TEXT    NOT NULL CHECK (role IN ('user','bot')),
  text       TEXT    NOT NULL,
  created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE INDEX IF NOT EXISTS bot_messages_user_time_idx
  ON bot_messages (user_id, created_at);

-- Итоги дня
CREATE TABLE IF NOT EXISTS bot_daily_summaries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day        DATE    NOT NULL,
  summary    TEXT    NOT NULL,
  created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  UNIQUE(user_id, day)
);
"""

SQL_POSTGRES = """
-- Сообщения диалога
CREATE TABLE IF NOT EXISTS bot_messages (
  id         bigserial PRIMARY KEY,
  user_id    bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       text   NOT NULL CHECK (role IN ('user','bot')),
  text       text   NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bot_messages_user_time_idx
  ON bot_messages (user_id, created_at DESC);

-- Итоги дня
CREATE TABLE IF NOT EXISTS bot_daily_summaries (
  id         bigserial PRIMARY KEY,
  user_id    bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day        date   NOT NULL,
  summary    text   NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, day)
);
"""


def ensure_memory_schema() -> None:
    sql = SQL_SQLITE if _is_sqlite() else SQL_POSTGRES
    with db_session() as s:
        s.execute(text(sql))
        s.commit()
    print("[memory] schema ensured (bot_messages, bot_daily_summaries)")
