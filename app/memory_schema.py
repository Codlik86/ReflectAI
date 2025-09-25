# app/memory_schema.py
"""
Совместимость со старым кодом.
Теперь схему БД ведём миграциями Alembic, поэтому эти функции — no-op.
Оставлены синхронные и асинхронные версии для обратной совместимости.
"""

# --- async версии, вызываются из FastAPI startup ---
async def ensure_memory_schema_async() -> None:
    return

async def ensure_users_policy_column_async() -> None:
    return

async def ensure_users_created_at_column_async() -> None:
    return

# --- старые синхронные обёртки (на случай, если где-то ещё вызываются) ---
def ensure_memory_schema() -> None:
    return

def ensure_users_policy_column() -> None:
    return

def ensure_users_created_at_column() -> None:
    return
