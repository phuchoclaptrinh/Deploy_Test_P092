"""The grouping_status widening is additive and reversible.

`ai_analysis_runs.grouping_status` was `VARCHAR(30)`; the emergency gate writes
a 35-character status into it. This locks the revision chain, the fact that the
upgrade only widens, and the fact that the downgrade clears the rows that would
not fit before narrowing the column again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "d4e5f6a7b9ca_widen_grouping_status_for_the_emergency_gate.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_follows_the_dispatch_emergency_migration():
    text = _text()
    assert 'revision: str = "d4e5f6a7b9ca"' in text
    assert 'down_revision: str | Sequence[str] | None = "c3d4e5f6a8b9"' in text


def test_upgrade_widens_thirty_to_fifty():
    text = _text()
    assert "OLD_LENGTH = 30" in text
    assert "NEW_LENGTH = 50" in text
    upgrade = text[text.index("def upgrade()") : text.index("def downgrade()")]
    # Widening, not narrowing: old is what the column *was*, new is the target.
    assert "existing_type=sa.String(OLD_LENGTH)" in upgrade
    assert "type_=sa.String(NEW_LENGTH)" in upgrade


def test_downgrade_clears_long_rows_before_narrowing():
    text = _text()
    downgrade = text[text.index("def downgrade()") :]
    # Narrowing while a 35-character row exists would fail exactly the way the
    # original bug did, so the UPDATE has to come first.
    update_at = downgrade.index("UPDATE ai_analysis_runs SET grouping_status = NULL")
    alter_at = downgrade.index("batch_alter_table")
    assert update_at < alter_at


def test_migration_does_not_drop_legacy_objects():
    text = _text()
    for destructive in ("drop_table", "drop_column", "DROP TABLE", "DROP COLUMN"):
        assert destructive not in text
