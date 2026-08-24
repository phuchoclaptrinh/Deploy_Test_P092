"""`2f3a4b5c6d7e` records why DIRECT is on, and rewrites nothing.

DIRECT auto-assignment can only be started by confirming a real proposal, so the
switch has to be able to say which one. `updated_by_user_id` cannot: it names
whoever last touched the row, which after a later delay change is no longer the
person who authorised autonomous assignment.

The revision is three nullable columns and two `SET NULL` foreign keys. Rows
already enabled when it runs keep NULL — that activation happened before
anything recorded it, and inventing a batch id for it would be a lie in the one
place the feature exists to be honest.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "2f3a4b5c6d7e_record_direct_activation_source.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    text = _text()
    return text[text.index("def upgrade()") : text.index("def downgrade()")]


def test_revision_follows_the_current_head():
    text = _text()
    assert 'revision: str = "2f3a4b5c6d7e"' in text
    assert 'down_revision: str | Sequence[str] | None = "1e2f3a4b5c6d"' in text


def test_it_adds_the_three_provenance_columns():
    upgrade = _upgrade()
    for column in ("activated_by_batch_id", "activated_by_user_id", "activated_at"):
        assert f'sa.Column("{column}"' in upgrade
    assert 'batch_alter_table("auto_assignment_settings")' in upgrade


def test_every_column_is_nullable_so_no_row_is_rewritten():
    upgrade = _upgrade()
    # Three columns, three nullable=True, and no default that would claim a
    # provenance for a switch that was already on.
    assert upgrade.count("nullable=True") == 3
    assert "server_default" not in upgrade
    for destructive in ("UPDATE ", "DELETE ", "drop_table", "drop_column"):
        assert destructive not in upgrade


def test_losing_a_batch_must_not_block_deleting_it():
    """SET NULL, not RESTRICT: provenance is nice to have, not a lock."""
    upgrade = _upgrade()
    assert upgrade.count('ondelete="SET NULL"') == 2


def test_downgrade_removes_exactly_what_upgrade_added():
    text = _text()
    downgrade = text[text.index("def downgrade()") :]
    for column in ("activated_at", "activated_by_user_id", "activated_by_batch_id"):
        assert f'batch.drop_column("{column}")' in downgrade
    # Constraints first, then the columns they point at.
    assert downgrade.index("drop_constraint") < downgrade.index("drop_column")
