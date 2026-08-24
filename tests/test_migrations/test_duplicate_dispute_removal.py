"""The appeal cleanup is a forward correction, not a rewrite of history.

`7a8b9c0d1e2f` drops the two objects that only ever served the removed resident
duplicate appeal. It must leave every duplicate *detection* object alone, and it
must not touch the v4 revisions that created them — those are immutable and
still name the feature, which is fine.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "alembic" / "versions"
MIGRATION = VERSIONS / "7a8b9c0d1e2f_remove_duplicate_dispute_appeals.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_follows_the_current_head():
    text = _text()
    assert 'revision: str = "7a8b9c0d1e2f"' in text
    assert 'down_revision: str | Sequence[str] | None = "6f7a8b9c0d1e"' in text


def test_upgrade_drops_only_the_appeal_objects():
    upgrade = _text()
    upgrade = upgrade[upgrade.index("def upgrade()") : upgrade.index("def downgrade()")]
    assert 'op.drop_table("duplicate_disputes")' in upgrade
    assert 'batch.drop_column("duplicate_disputed_at")' in upgrade
    # Duplicate detection and linking survive untouched.
    for preserved in (
        "duplicate_of_ticket_id",
        "duplicate_linked_at",
        "duplicate_reason",
        "duplicate_analysis_run_id",
        "ck_tickets_duplicate_not_self",
        "ck_tickets_linked_duplicate_needs_master",
    ):
        assert preserved not in upgrade


def test_downgrade_is_structurally_valid():
    text = _text()
    downgrade = text[text.index("def downgrade()") :]
    assert 'op.create_table(\n        "duplicate_disputes"' in downgrade
    assert 'sa.Column("duplicate_disputed_at"' in downgrade
    assert "ck_duplicate_disputes_status_enum" in downgrade
    assert "uq_duplicate_disputes_one_open_per_ticket" in downgrade


def test_the_original_v4_revisions_are_left_alone():
    """They created the table and column and must stay immutable."""
    for name in (
        "1a2b3c4d5e6f_add_v4_backend_shell.py",
        "4d5e6f7a8b9c_add_assignment_proposal_shell.py",
        "5e6f7a8b9c0d_add_v4_agent_backend_contract.py",
    ):
        assert "duplicate_dispute" in (VERSIONS / name).read_text(encoding="utf-8")
