"""Record what is in a database before `a1b2c3d4e5f7` deletes most of it.

Read-only. It opens one connection, runs `SELECT` and `count(*)`, and writes a
report. It cannot migrate, stamp or drop anything, so it is safe to point at
production -- which is the whole reason it exists as its own script rather than
as a paragraph of the runbook telling somebody to type eighteen count queries
by hand at the moment they are least inclined to be careful.

What it is for:

* The revision the database is actually at, checked against the one the cutover
  expects to start from. A database somewhere else in the chain means stop.
* A row count for every table the cutover empties. Afterwards they must be
  zero, and "was it already empty?" is not a question you can answer later.
* A row count for every table that must survive untouched. If any of these
  moves, something went wrong that no amount of reading the diff will explain.
* The audit split. `a1b2c3d4e5f7` deletes only `TICKET` and
  `TICKET_ASSIGNMENT` rows; account, category and auto-assignment history is
  not ticket data and stays.

Run it twice -- before, and after -- and diff the two files.

    python scripts/premigration_inventory.py --database-url "postgresql://..." \\
        --out outputs/inventory-before.md

The URL is required and positional-by-flag on purpose: this script never reads
`DATABASE_URL` from `.env`, because the entire failure mode being guarded
against is somebody acting on a database they did not mean to name.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database.migration_safety import target_fingerprint  # noqa: E402

#: The revision the cutover is written to start from. `9f0a1b2c3d4e` is
#: "remove the assignment acceptance step", the last v1 revision.
EXPECTED_REVISION = "9f0a1b2c3d4e"

#: Imported rather than restated so the two lists cannot drift. If the
#: migration's tuple changes, this report changes with it.
def _tables_the_cutover_empties() -> tuple[str, ...]:
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "a1b2c3d4e5f7_hard_cutover_to_risk_scoring_v2.py"
    )
    spec = importlib.util.spec_from_file_location("_cutover", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.TICKET_DOMAIN_TABLES)


#: Everything the cutover must leave alone. Accounts, the building, the people
#: who fix things and the catalogue of what can break.
SURVIVING_TABLES = (
    "user_profiles",
    "resident_profiles",
    "technician_profiles",
    "technician_skills",
    "technician_availability_events",
    "units",
    "floors",
    "locations",
    "location_types",
    "categories",
    "auto_assignment_settings",
)


def _scalar(engine: Engine, sql: str, **params) -> object:
    with engine.connect() as connection:
        return connection.scalar(text(sql), params)


def _count(engine: Engine, table: str) -> int | str:
    try:
        return int(_scalar(engine, f"SELECT count(*) FROM {table}"))  # type: ignore[arg-type]
    except SQLAlchemyError as error:
        return f"unreadable ({type(error).__name__})"


def _table_exists(engine: Engine, table: str) -> bool:
    return bool(_scalar(engine, "SELECT to_regclass(:name)", name=f"public.{table}"))


def build_report(engine: Engine, url: str) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    version = _scalar(engine, "SELECT version()")
    database = _scalar(engine, "SELECT current_database()")
    revision = None
    if _table_exists(engine, "alembic_version"):
        revision = _scalar(engine, "SELECT version_num FROM alembic_version")

    lines: list[str] = [
        "# Pre-migration inventory",
        "",
        f"- Taken at: {now}",
        f"- Target: `{target_fingerprint(url)}`",
        f"- Database: `{database}`",
        f"- Server: `{version}`",
        f"- Alembic revision: `{revision or 'none'}`",
    ]

    if revision != EXPECTED_REVISION:
        lines += [
            "",
            f"> **Stop.** The cutover starts from `{EXPECTED_REVISION}`; this database "
            f"reports `{revision or 'no alembic_version table'}`. Nothing in the runbook "
            "below applies until that is explained.",
        ]

    lines += [
        "",
        "## Tables the cutover empties",
        "",
        "`a1b2c3d4e5f7` deletes every row from these, in this order, and has no",
        "`downgrade`. After the migration each count must be 0.",
        "",
        "| Table | Rows before |",
        "|---|---:|",
    ]
    doomed = _tables_the_cutover_empties()
    total = 0
    for table in doomed:
        if not _table_exists(engine, table):
            lines.append(f"| `{table}` | (no such table) |")
            continue
        rows = _count(engine, table)
        if isinstance(rows, int):
            total += rows
        lines.append(f"| `{table}` | {rows} |")
    lines.append(f"| **total** | **{total}** |")

    lines += [
        "",
        "## Tables that must survive unchanged",
        "",
        "Every count here must be identical afterwards. A change in any of them",
        "is not a rounding difference; it means the cutover reached further than",
        "it was supposed to.",
        "",
        "| Table | Rows |",
        "|---|---:|",
    ]
    for table in SURVIVING_TABLES:
        if not _table_exists(engine, table):
            lines.append(f"| `{table}` | (no such table) |")
            continue
        lines.append(f"| `{table}` | {_count(engine, table)} |")

    lines += [
        "",
        "## Audit log",
        "",
        "Only `TICKET` and `TICKET_ASSIGNMENT` rows are deleted. Everything else",
        "is account, category and auto-assignment history, which is not ticket",
        "data and stays.",
        "",
        "| entity_type | Rows | Deleted by the cutover |",
        "|---|---:|---|",
    ]
    if _table_exists(engine, "audit_logs"):
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT entity_type, count(*) AS n FROM audit_logs "
                    "GROUP BY entity_type ORDER BY n DESC"
                )
            ).all()
        for entity_type, count in rows:
            deleted = "yes" if entity_type in {"TICKET", "TICKET_ASSIGNMENT"} else "no"
            lines.append(f"| `{entity_type}` | {count} | {deleted} |")
    else:
        lines.append("| (no audit_logs table) | | |")

    lines += [
        "",
        "## To be filled in by a person",
        "",
        "The script cannot answer these, and they are the ones that decide",
        "whether the cutover goes ahead.",
        "",
        "- [ ] Who else connects to this database, and have they been told?",
        "- [ ] Is losing the entire ticket history accepted, in writing?",
        "- [ ] Is any of it needed for lookup later? If so the plan stops here:",
        "      the answer is an archive migration, not this hard cutover.",
        "- [ ] Has a dump been taken **and restored somewhere** to prove it works?",
        "- [ ] Was the dump taken through the direct host, or Supavisor session mode",
        "      on port 5432 when direct IPv6 was unavailable (never transaction mode 6543)?",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        required=True,
        help="The target, spelled out in full. Never read from .env; see the module docstring.",
    )
    parser.add_argument("--out", type=Path, help="Write the report here as well as to stdout.")
    args = parser.parse_args()

    url = args.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_engine(url, connect_args={"connect_timeout": 10})
    try:
        report = build_report(engine, url)
    except SQLAlchemyError as error:
        print(f"Could not read the database: {error}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"\nWritten to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
