import os
from sqlalchemy import create_engine, Integer, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from datetime import datetime

# Можно переключить на Postgres позже, сейчас — локальный файл insights.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./insights.db")

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    privacy_level: Mapped[str] = mapped_column(String(16), default="insights")  # none|insights|all

class Insight(Base):
    __tablename__ = "insights"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

_engine = create_engine(DATABASE_URL, echo=False, future=True)
Base.metadata.create_all(_engine)

def db_session():
    return Session(_engine)
