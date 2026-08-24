"""Public database infrastructure exports without eager engine creation."""

from __future__ import annotations

from typing import Any

from src.database.base import Base

__all__ = ["Base", "SessionLocal", "engine", "get_db"]


def __getattr__(name: str) -> Any:
    """Load session infrastructure only when explicitly requested.

    Importing ``src.database.models`` is used by Alembic offline mode and model
    metadata tests. Keeping engine creation lazy means those operations do not
    require a PostgreSQL DBAPI or a live database connection.
    """
    if name in {"SessionLocal", "engine", "get_db"}:
        from src.database import session

        return getattr(session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
