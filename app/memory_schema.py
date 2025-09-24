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
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with db_session() as s:
        for stmt in statements:
            s.execute(text(stmt))
        s.commit()


def _exec_sqlite_script(sql: str) -> None:
    """Выполняем весь скрипт одним вызовом executescript (SQLite)."""
    raw_conn = _engine.raw_connection()
    try:
        raw_conn.executescript(sql)
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_memory_schema() -> None:
    """Создаёт (если нет) таблицы bot_messages и bot_daily_summaries."""
    if _is_sqlite(_engine):
        _exec_sqlite_script(SQL_SQLITE)
    else:
        _exec_postgres_multistmt(SQL_POSTGRES)
    print("[memory] schema ensured (bot_messages, bot_daily_summaries)")


def _users_has_column_sqlite(column: str) -> bool:
    with db_session() as s:
        cols = s.execute(text("PRAGMA table_info(users)")).fetchall()
    colnames = {c[1] for c in cols}  # c[1] = name
    return column in colnames


def _users_has_column_postgres(column: str) -> bool:
    q = text("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = :col
        LIMIT 1
    """)
    with db_session() as s:
        return s.execute(q, {"col": column}).scalar() is not None


def ensure_users_policy_column() -> None:
    """
    Гарантируем наличие столбцов в users:
      - policy_accepted_at (DATETIME/timestamptz)
      - privacy_level (TEXT, default 'insights')
    Совместимо и с SQLite, и с Postgres. Любые ошибки — мягко логируем.
    """
    try:
        if _is_sqlite(_engine):
            # SQLite
            need_alter = False
            if not _users_has_column_sqlite("policy_accepted_at"):
                with db_session() as s:
                    s.execute(text("ALTER TABLE users ADD COLUMN policy_accepted_at DATETIME"))
                    s.commit()
                print("[memory] users.policy_accepted_at added (sqlite)")
                need_alter = True

            if not _users_has_column_sqlite("privacy_level"):
                with db_session() as s:
                    s.execute(text("ALTER TABLE users ADD COLUMN privacy_level TEXT DEFAULT 'insights'"))
                    s.commit()
                print("[memory] users.privacy_level added (sqlite)")

        else:
            # Postgres
            # Проверяем и добавляем по отдельности
            if not _users_has_column_postgres("policy_accepted_at"):
                with db_session() as s:
                    s.execute(text("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS policy_accepted_at timestamptz"))
                    s.commit()
                print("[memory] users.policy_accepted_at added (postgres)")

            if not _users_has_column_postgres("privacy_level"):
                with db_session() as s:
                    s.execute(text("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS privacy_level text DEFAULT 'insights'"))
                    s.commit()
                print("[memory] users.privacy_level added (postgres)")

    except Exception as e:
        # Важно не ронять процесс из-за миграции в рантайме
        print("[memory] ensure_users_policy_column WARNING:", repr(e))

def ensure_users_created_at_column():
    """
    Гарантируем наличие users.created_at с дефолтом CURRENT_TIMESTAMP.
    Работает для SQLite (через PRAGMA table_info) и не роняет процесс.
    """
    try:
        with db_session() as s:
            cols = s.execute(text("PRAGMA table_info(users)")).fetchall()
            colnames = {c[1] for c in cols}  # name
            if "created_at" not in colnames:
                s.execute(text(
                    "ALTER TABLE users ADD COLUMN created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP)"
                ))
                s.commit()
    except Exception:
        # Для Postgres можно позже сделать отдельную миграцию через Alembic,
        # но тут просто не роняем, если столбец уже есть
        pass