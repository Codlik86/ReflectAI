# app/db/__init__.py
# Совместимость со старым кодом (memory_schema и т.п.)
from .core import engine as _engine
from .core import async_session as db_session
from .core import get_session

__all__ = ["_engine", "db_session", "get_session"]
