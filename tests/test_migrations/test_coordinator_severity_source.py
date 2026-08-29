"""`tickets.severity_source` and the COORDINATOR_MANUAL label are additive.

§8.3 lets a Coordinator supply the severity a failed analysis never produced.
Recording that truthfully needs a source the enum could not express, so the
revision widens `severity_source_enum` and adds one nullable ticket column. It
must not rewrite a single existing row: an AI-derived severity keeps its real
source on `ai_analysis_runs.severity_source`.
"""

from pathlib import Path

from src.models.enums import SeveritySource

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "0d1e2f3a4b5c_record_coordinator_chosen_severity.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_follows_the_current_head():
    text = _text()
    assert 'revision: str = "0d1e2f3a4b5c"' in text
    assert 'down_revision: str | Sequence[str] | None = "9c0d1e2f3a4b"' in text


def test_upgrade_widens_the_enum_before_adding_the_column():
    text = _text()
    upgrade = text[text.index("def upgrade()") : text.index("def downgrade()")]
    # A column typed on the enum can only be added once the label exists.
    label_at = upgrade.index("ALTER TYPE severity_source_enum ADD VALUE IF NOT EXISTS 'COORDINATOR_MANUAL'")
    column_at = upgrade.index('sa.Column("severity_source"')
    assert label_at < column_at
    assert 'batch_alter_table("tickets")' in upgrade


def test_the_column_is_nullable_so_no_existing_row_changes():
    upgrade = _text()
    upgrade = upgrade[upgrade.index("def upgrade()") : upgrade.index("def downgrade()")]
    assert "nullable=True" in upgrade
    for destructive in ("UPDATE tickets", "drop_table", "drop_column", "server_default"):
        assert destructive not in upgrade


def test_downgrade_only_removes_the_column():
    text = _text()
    downgrade = text[text.index("def downgrade()") :]
    assert 'batch.drop_column("severity_source")' in downgrade
    # PostgreSQL cannot remove an enum label; the revision leaves it rather than
    # pretending to reverse it, and drops nothing else.
    assert "ALTER TYPE" not in downgrade
    assert "DROP TYPE" not in downgrade
    assert "drop_table" not in downgrade


def test_the_enum_the_migration_writes_matches_the_application_enum():
    assert SeveritySource.COORDINATOR_MANUAL.value == "COORDINATOR_MANUAL"
    assert "'COORDINATOR_MANUAL'" in _text()
