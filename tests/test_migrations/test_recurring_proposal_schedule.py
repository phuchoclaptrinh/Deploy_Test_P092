"""`1e2f3a4b5c6d` adds two features' storage and rewrites no existing row.

The revision carries the recurring proposal schedule and the confirmation
snapshot. Both are new storage for new behaviour, so the bar it has to clear is
narrow and worth pinning: every addition is nullable or defaulted, nothing is
dropped, and no `UPDATE` backfills a value onto rounds that were confirmed
before snapshots existed. Those rows keep `confirmation_snapshot IS NULL` and
the history endpoint reports them as pre-snapshot — inventing content for them
is the exact bug the snapshot exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from src.models.enums import ProposalBatchCreatedBy, ProposalScheduleInterval

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "1e2f3a4b5c6d_add_recurring_proposal_schedule.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    text = _text()
    return text[text.index("def upgrade()") : text.index("def downgrade()")]


def test_revision_follows_the_current_head():
    text = _text()
    assert 'revision: str = "1e2f3a4b5c6d"' in text
    assert 'down_revision: str | Sequence[str] | None = "0d1e2f3a4b5c"' in text


def test_the_schedule_is_a_singleton_that_cannot_look_on_and_do_nothing():
    upgrade = _upgrade()
    assert 'op.create_table(\n        "assignment_proposal_schedules"' in upgrade
    assert 'sa.CheckConstraint("id = 1"' in upgrade
    # An enabled schedule with no interval could never come due; one with no
    # next run would never fire. The database refuses both.
    assert "enabled = false OR (interval_code IS NOT NULL AND next_run_at IS NOT NULL)" in upgrade


def test_the_schedule_intervals_match_the_enum():
    upgrade = _upgrade()
    for interval in ProposalScheduleInterval:
        assert f"'{interval.value}'" in upgrade
    # No IMMEDIATE: "build a new draft table immediately, forever" is not a
    # schedule, and it is not offered by the enum either.
    assert not hasattr(ProposalScheduleInterval, "IMMEDIATE")


def test_the_batch_columns_are_additive():
    upgrade = _upgrade()
    for column in ("confirmation_snapshot", "followup_schedule", "followup_schedule_set_at"):
        assert f'sa.Column("{column}"' in upgrade
    # The only non-nullable addition carries a default, so existing rows are
    # described rather than rewritten: they were all coordinator-created.
    assert f'server_default="{ProposalBatchCreatedBy.COORDINATOR.value}"' in upgrade


def test_no_existing_row_is_rewritten():
    upgrade = _upgrade()
    for destructive in ("UPDATE ", "DELETE ", "drop_table", "drop_column"):
        assert destructive not in upgrade


def test_downgrade_removes_exactly_what_upgrade_added():
    text = _text()
    downgrade = text[text.index("def downgrade()") :]
    for column in (
        "followup_schedule_set_at",
        "followup_schedule",
        "confirmation_snapshot",
        "created_by_type",
    ):
        assert f'batch.drop_column("{column}")' in downgrade
    assert 'op.drop_table("assignment_proposal_schedules")' in downgrade
