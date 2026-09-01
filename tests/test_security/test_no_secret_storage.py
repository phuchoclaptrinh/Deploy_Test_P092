"""Tests that schema/source files do not introduce secret storage columns."""

from pathlib import Path

import src.database.models  # noqa: F401
from src.database.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_COLUMN_TERMS = ("password", "token", "otp", "api_key", "authorization_header", "service_role")


def test_no_database_table_stores_secret_columns():
    for table in Base.metadata.tables.values():
        for column in table.c:
            assert not any(term in column.name.lower() for term in FORBIDDEN_COLUMN_TERMS)


def test_migrations_do_not_call_create_all_or_import_fastapi_startup():
    for migration_file in (PROJECT_ROOT / "alembic" / "versions").glob("*.py"):
        text = migration_file.read_text(encoding="utf-8").lower()
        assert "base.metadata.create_all" not in text
        assert "create_all(" not in text
        assert "fastapi" not in text
