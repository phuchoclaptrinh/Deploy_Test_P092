"""Active-day history needs its own table, and existing rows need a start point.

§2.13 counts the days a Technician had readiness switched on. `technician_profiles`
keeps only the current flag, so this revision adds the transition log the report
reads and seeds one row per existing Technician.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "9c0d1e2f3a4b_add_technician_availability_events.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_follows_the_rls_alignment_revision():
    text = _text()
    assert 'revision: str = "9c0d1e2f3a4b"' in text
    assert 'down_revision: str | Sequence[str] | None = "8b9c0d1e2f3a"' in text


def test_upgrade_creates_the_event_table_with_an_audit_actor():
    text = _text()
    upgrade = text[text.index("def upgrade()") : text.index("def downgrade()")]
    assert '"technician_availability_events"' in upgrade
    assert '"is_available"' in upgrade
    assert '"changed_by_user_id"' in upgrade
    assert '"changed_at"' in upgrade


def test_upgrade_seeds_current_readiness_so_existing_rows_have_a_start_point():
    upgrade = _text()
    upgrade = upgrade[upgrade.index("def upgrade()") : upgrade.index("def downgrade()")]
    assert "INSERT INTO technician_availability_events" in upgrade
    assert "FROM technician_profiles" in upgrade
    assert "MIGRATION_BACKFILL" in upgrade


def test_downgrade_drops_only_the_new_table():
    downgrade = _text()
    downgrade = downgrade[downgrade.index("def downgrade()") :]
    assert 'op.drop_table("technician_availability_events")' in downgrade
    assert "technician_profiles" not in downgrade
