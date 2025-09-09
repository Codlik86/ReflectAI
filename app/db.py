import os
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    create_engine,
    Integer, String, Text, DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, Session, relationship
)

# Можно переключить на Postgres позже; сейчас — локальный файл insights.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./insights.db")


class Base(DeclarativeBase):
    pass


# ---------- Базовые таблицы (были в проекте) ----------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # старое поле приватности (оставляем для совместимости):
    # none | insights | all
    privacy_level: Mapped[str] = mapped_column(String(16), default="insights")

    # удобные связи (не обязательны к использованию)
    journal_entries: Mapped[List["JournalEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    settings: Mapped[Optional["UserSettings"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    memory: Mapped[Optional["UserMemory"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    subscription: Mapped[Optional["Subscription"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class Insight(Base):
    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------- Новые таблицы под «Помни» (дневник/память/настройки/подписка/логи) ----------

class JournalEntry(Base):
    """Сырые записи дневника"""
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[Optional[str]] = mapped_column(Text, default=None)  # произвольные пометки "mood:..., topic:..."
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped["User"] = relationship(back_populates="journal_entries")


class UserMemory(Base):
    """Накопительный конспект пользователя (персональная память)"""
    __tablename__ = "user_memory"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, default=None)      # конспект (склейка 4 пунктов)
    preferences: Mapped[Optional[str]] = mapped_column(Text, default=None)  # опционально (json-подобная строка)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="memory")


class UserSettings(Base):
    """Выбранный стиль/подход и приватность дневника"""
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tone: Mapped[str] = mapped_column(String(32), default="soft")            # soft|practical|concise|honest
    method: Mapped[str] = mapped_column(String(32), default="cbt")           # cbt|act|gestalt|supportive
    autosuggest_level: Mapped[str] = mapped_column(String(16), default="gentle")
    privacy_mode: Mapped[str] = mapped_column(String(16), default="ask")     # ask|auto|off
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="settings")


class Subscription(Base):
    """Флаг подписки (free/plus/pro)"""
    __tablename__ = "subscriptions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    tier: Mapped[str] = mapped_column(String(32), default="free")  # free|plus|pro
    renewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    user: Mapped["User"] = relationship(back_populates="subscription")


class BotEvent(Base):
    """Событийные логи (для метрик/аналитики)"""
    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)  # FK опционален (упрощаем для SQLite)
    event_type: Mapped[str] = mapped_column(String(64), index=True)  # diary_message|assist_reply|audio_play|...
    payload: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# ---------- Engine / session ----------

_engine = create_engine(DATABASE_URL, echo=False, future=True)
Base.metadata.create_all(_engine)  # создаст недостающие таблицы, существующие не тронет

def db_session() -> Session:
    """Возвращает сессию SQLAlchemy. Можно использовать как контекст-менеджер:
       with db_session() as s: ...
    """
    return Session(_engine)
