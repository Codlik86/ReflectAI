# app/memory_schema.py
# -*- coding: utf-8 -*-
"""
Автосоздание таблиц памяти (bot_messages, bot_daily_summaries).
Корректно работает и с SQLite, и с PostgreSQL:
- SQLite: выполняем скрипт через executescript (мульти-стейтменты).
- Postgres: бьём на отдельные стейтменты и исполняем по одному.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine
from app.db import db_session, _engine


def _is_sqlite(engine: Engine) -> bool:
    try:
        return engine.url.get_backend_name() == "sqlite"
    except Exception:
        return str(engine.url).startswith("sqlite")


SQL_SQLITE = """
PRAGMA foreign_keys = ON;

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


def _exec_postgres_multistmt(sql: str) -> None:
    """Бьём скрипт на одиночные выражения и выполняем по одному (Postgres)."""
    # Грубо, но надёжно: делим по ';' и исполняем непустые куски.
    # (Если у тебя когда-то появятся ';' внутри функций/процедур — лучше перейти на Alembic.)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with db_session() as s:
        for stmt in statements:
            s.execute(text(stmt))
        s.commit()


def _exec_sqlite_script(sql: str) -> None:
    """Выполняем весь скрипт одним вызовом executescript (SQLite)."""
    # Берём сырой коннектор sqlite3 из движка SQLAlchemy и шлём executescript
    raw_conn = _engine.raw_connection()
    try:
        raw_conn.executescript(sql)
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_memory_schema() -> None:
    if _is_sqlite(_engine):
        _exec_sqlite_script(SQL_SQLITE)
    else:
        _exec_postgres_multistmt(SQL_POSTGRES)
    print("[memory] schema ensured (bot_messages, bot_daily_summaries)")
