"""Print safe T-006/T-007 environment-variable presence only."""

from __future__ import annotations

import os
from pathlib import Path

REQUIRED_NAMES = (
    "APP_ENV",
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_STORAGE_BUCKET",
    "ALLOW_LIVE_MIGRATION",
    "RUN_SUPABASE_INTEGRATION_TESTS",
)

OPTIONAL_TOKEN_NAMES = (
    "SUPABASE_TEST_RESIDENT_ACCESS_TOKEN",
    "SUPABASE_TEST_BQL_ACCESS_TOKEN",
)

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def main() -> int:
    """Print only whether each expected setting is present."""
    for name in (*REQUIRED_NAMES, *OPTIONAL_TOKEN_NAMES):
        status = "PRESENT" if _is_present(name) else "MISSING"
        print(f"{name}: {status}")
    return 0


def _is_present(name: str) -> bool:
    if os.getenv(name):
        return True
    if not ENV_FILE.exists():
        return False

    prefix = f"{name}="
    try:
        with ENV_FILE.open(encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if line.startswith("#") or not line.startswith(prefix):
                    continue
                return bool(line.removeprefix(prefix).strip())
    except OSError:
        return False

    return False


if __name__ == "__main__":
    raise SystemExit(main())
