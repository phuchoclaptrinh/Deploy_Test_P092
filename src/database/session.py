"""Synchronous SQLAlchemy engine and session factory."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings

settings = get_settings()


def get_database_url() -> str:
    """Return a SQLAlchemy URL compatible with the installed psycopg driver."""
    database_url = settings.require_database_url()
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


database_url = get_database_url()
engine_options: dict[str, object] = {}
if database_url.startswith("postgresql"):
    # Supabase session pooler has a small shared quota; keep this process bounded.
    engine_options.update(
        pool_size=2,
        max_overflow=1,
        pool_timeout=5,
        pool_recycle=300,
        pool_pre_ping=True,
    )

engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session]:
    """Yield a database session and close it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
