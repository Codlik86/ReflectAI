# migrations/env.py
from __future__ import annotations

import os
import asyncio
from logging.config import fileConfig

from dotenv import load_dotenv
from alembic import context

from app.db.core import engine
from app.db.models import Base

load_dotenv()  # подхватит .env из корня проекта

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _clean_env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    if not isinstance(v, str):
        return default
    return v.strip().strip('"').strip("'")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _clean_env("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode (async engine)."""
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)


def main() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())


main()
