"""Safety checks for live database migrations."""

from __future__ import annotations

from src.config import Settings, get_settings


def validate_live_migration_safety(settings: Settings | None = None) -> None:
    """Allow online migrations only for explicitly gated dev/test targets."""
    selected = settings or get_settings()

    if selected.app_env not in {"development", "test"}:
        raise RuntimeError("Online migration is allowed only in development or test.")

    if not selected.allow_live_migration:
        raise RuntimeError("Set ALLOW_LIVE_MIGRATION=true to run online migrations.")
