# app/db/core.py
import os
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from collections.abc import AsyncIterator  # или: from typing import AsyncGenerator

def _clean_env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v.strip().strip('"').strip("'") if isinstance(v, str) else default


DATABASE_URL = _clean_env("DATABASE_URL")  # postgresql+asyncpg://...&sslmode=require
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is empty")

engine = create_async_engine(
    DATABASE_URL,  # твой DSN вида postgresql+psycopg://...
    echo=False,
    future=True,
    pool_pre_ping=True,         # <— добавь это
    pool_size=5, max_overflow=10  # можно оставить дефолты, но так стабильнее на Render
)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# FastAPI-совместимый провайдер с yield
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as s:
        yield s
