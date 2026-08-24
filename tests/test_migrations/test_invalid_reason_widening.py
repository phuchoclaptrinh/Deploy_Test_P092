"""The COORDINATOR_REJECTED widening is additive and reversible.

Building Management rejecting a report records its own `invalid_reason`, which
the existing check constraint did not permit. This locks the revision chain, the
constraint text, and the fact that the downgrade cleans rows up before narrowing
the constraint again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "6f7a8b9c0d1e_widen_invalid_reason_for_coordinator_rejection.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_follows_the_v4_contract_migration():
    text = _text()
    assert 'revision: str = "6f7a8b9c0d1e"' in text
    assert 'down_revision: str | Sequence[str] | None = "5e6f7a8b9c0d"' in text


def test_upgrade_permits_all_three_reasons():
    text = _text()
    assert "'CONTENT_INSUFFICIENT', 'RESIDENT_RESPONSE_TIMEOUT', 'COORDINATOR_REJECTED'" in text


def test_downgrade_folds_rejected_rows_before_narrowing():
    text = _text()
    downgrade = text[text.index("def downgrade()") :]
    # Narrowing the constraint while a COORDINATOR_REJECTED row exists would
    # fail, so the update has to come first.
    update_at = downgrade.index("UPDATE tickets SET invalid_reason = 'CONTENT_INSUFFICIENT'")
    constraint_at = downgrade.index("batch_alter_table")
    assert update_at < constraint_at


def test_migration_does_not_drop_legacy_objects():
    text = _text()
    for destructive in ("drop_table", "drop_column", "DROP TABLE", "DROP COLUMN"):
        assert destructive not in text
