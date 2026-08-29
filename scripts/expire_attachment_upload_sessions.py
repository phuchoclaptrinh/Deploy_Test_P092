"""Mark expired pending ticket attachment upload sessions as expired."""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, text

from src.database.session import get_database_url


def parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(description="Expire pending ticket attachment upload sessions.")
    arg_parser.add_argument("--dry-run", action="store_true", help="Report how many sessions would be expired.")
    return arg_parser


def main() -> int:
    args = parser().parse_args()
    engine = create_engine(get_database_url())
    if args.dry_run:
        with engine.connect() as connection:
            count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM ticket_attachment_upload_sessions
                    WHERE status = 'pending'
                      AND expires_at <= now()
                    """
                )
            )
        print(f"DRY RUN: would expire {int(count or 0)} pending upload sessions.")
        return 0
    with engine.begin() as connection:
        result = connection.execute(
            text(
                """
                UPDATE ticket_attachment_upload_sessions
                SET status = 'expired',
                    updated_at = now()
                WHERE status = 'pending'
                  AND expires_at <= now()
                """
            )
        )
    print(f"Expired {result.rowcount or 0} pending upload sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
