from __future__ import annotations
from logging.config import fileConfig
from alembic import context
import os
from app.db.core import engine
from app.db.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline():
    url = os.environ["DATABASE_URL"]
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online():
    from sqlalchemy.ext.asyncio import AsyncEngine
    connectable: AsyncEngine = engine
    async with connectable.connect() as connection:
        await connection.run_sync(lambda conn: context.configure(connection=conn, target_metadata=target_metadata))
        await connection.run_sync(do_run_migrations)

def do_run_migrations(connection):
    with context.begin_transaction():
        context.run_migrations()

def main():
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        import asyncio; asyncio.run(run_migrations_online())

main()
