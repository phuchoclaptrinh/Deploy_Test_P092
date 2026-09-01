"""Safe readiness checks for the API process."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from src.config import Settings


class ReadinessService:
    """Evaluate local dependencies without exposing configuration values."""

    def __init__(self, settings: Settings, engine: Engine, project_root: Path | None = None) -> None:
        self.settings = settings
        self.engine = engine
        self.project_root = project_root or Path(__file__).resolve().parents[2]

    def check(self) -> tuple[dict[str, Any], int]:
        checks = {
            "database": self._check_database(),
            "migration": "unknown",
            "supabase_auth": self._check_supabase_auth(),
            "supabase_storage": self._check_supabase_storage(),
        }
        if checks["database"] == "ok":
            checks["migration"] = self._check_migration()
        status = "ready" if all(value in {"ok", "configured"} for value in checks.values()) else "not_ready"
        return {"status": status, "checks": checks}, 200 if status == "ready" else 503

    def _check_database(self) -> str:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return "error"
        return "ok"

    def _check_migration(self) -> str:
        try:
            head_revisions = set(self._script_directory().get_heads())
            with self.engine.connect() as connection:
                current_revisions = set(connection.scalars(text("SELECT version_num FROM alembic_version")).all())
        except SQLAlchemyError:
            return "missing"
        except Exception:
            return "unknown"
        if current_revisions and current_revisions <= head_revisions:
            return "ok"
        return "pending"

    def _script_directory(self) -> ScriptDirectory:
        config = Config(str(self.project_root / "alembic.ini"))
        config.set_main_option("script_location", str(self.project_root / "alembic"))
        return ScriptDirectory.from_config(config)

    def _check_supabase_auth(self) -> str:
        if self.settings.supabase_url and self.settings.supabase_publishable_key:
            return "configured"
        return "missing"

    def _check_supabase_storage(self) -> str:
        if self.settings.supabase_url and self.settings.supabase_secret_key and self.settings.supabase_storage_bucket:
            return "configured"
        return "missing"
