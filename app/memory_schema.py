# app/memory_schema.py
# -*- coding: utf-8 -*-
"""
Автосоздание таблиц для «памяти дневничка».
Выполняется один раз при старте (CREATE TABLE IF NOT EXISTS ...).
Поддерживает PostgreSQL (Render).
"""

from sqlalchemy import text
from app.db import db_session


SQL_CREATE = """
-- сообщения диалога пользователь ↔ бот
CREATE TABLE IF NOT EXISTS bot_messages (
  id           bigserial PRIMARY KEY,
  user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role         text   NOT NULL CHECK (role IN ('user', 'bot')),
  text         text   NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bot_messages_user_time_idx
  ON bot_messages (user_id, created_at DESC);

-- дневные саммари (итоги дня) — по желанию, пригодятся позже
CREATE TABLE IF NOT EXISTS bot_daily_summaries (
  id           bigserial PRIMARY KEY,
  user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day          date   NOT NULL,
  summary      text   NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, day)
);
"""


def ensure_memory_schema() -> None:
    """
    Вызываем при старте приложения. Если таблиц нет — создаст.
    Если уже есть — просто ничего не сделает.
    """
    with db_session() as s:
        s.execute(text(SQL_CREATE))
        s.commit()
    print("[memory] schema ensured (bot_messages, bot_daily_summaries)")
