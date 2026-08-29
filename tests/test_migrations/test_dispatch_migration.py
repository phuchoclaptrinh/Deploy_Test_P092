"""The dispatch migration agrees with the models it is supposed to create.

This is not a substitute for running it. Whether `8e9f0a1b2c3d` applies cleanly
is proved by upgrading a disposable PostgreSQL database, which
`docs/operations.md` documents and which needs a database this suite does not
have. What these tests catch is the failure that survives a successful
migration run and only surfaces weeks later: a column added to a model and
never added to the migration, so every fresh deployment is missing it while
every developer machine — built with `create_all` — has it.

The comparison is by name against the migration source. Crude on purpose: a
cleverer check would need to execute the migration, which is the thing that
cannot be done here.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from src.database.base import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION = "8e9f0a1b2c3d"
SOURCE = (VERSIONS / f"{REVISION}_replace_proposal_with_dispatch.py").read_text(encoding="utf-8")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _heads() -> list[str]:
    revisions, downs = set(), set()
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "([^"]+)"', text, re.M)
        down = re.search(r"^down_revision: str \| Sequence\[str\] \| None = (.+)$", text, re.M)
        if revision:
            revisions.add(revision.group(1))
            if down:
                downs.add(down.group(1).strip().strip('"'))
    return sorted(revisions - downs)


def test_the_dispatch_revision_is_still_in_the_chain():
    """It has a successor now, so it is no longer the head.

    The "exactly one head" invariant did not move -- it lives in the newest
    revision's test file, which is where it stays correct without every older
    file having to be edited.
    """
    assert REVISION not in _heads()
    assert len(_heads()) == 1


def test_it_follows_the_p3_review_revision():
    module = _load(VERSIONS / f"{REVISION}_replace_proposal_with_dispatch.py")
    assert module.revision == REVISION
    assert module.down_revision == "7d8e9f0a1b2c"


def test_it_refuses_to_pretend_it_is_reversible():
    """It deletes proposal batches and their confirmation snapshots.

    A `downgrade` that recreated the empty tables would report success while the
    data they existed to hold was gone.
    """
    module = _load(VERSIONS / f"{REVISION}_replace_proposal_with_dispatch.py")
    with pytest.raises(RuntimeError, match="forward-only"):
        module.downgrade()


@pytest.mark.parametrize("table_name", ["dispatch_events", "at_risk_decisions"])
def test_every_model_column_is_created_by_the_migration(table_name):
    table = Base.metadata.tables[table_name]
    for column in table.c:
        assert f'"{column.name}"' in SOURCE, f"{table_name}.{column.name} is in the model but not the migration"


def test_the_new_assignment_columns_are_added():
    for column in (
        "acceptance_due_at",
        "planned_start_at",
        "planned_finish_at",
        "planned_order",
        "risk_state",
        "slack_seconds",
        "dispatch_event_id",
    ):
        assert f'add_column("ticket_assignments", sa.Column("{column}"' in SOURCE or f'"{column}"' in SOURCE


def test_the_acceptance_deadline_carries_over_rather_than_starting_empty():
    """In-flight assignments keep their deadline across the deploy.

    `acceptance_reassign_at` and `acceptance_due_at` are the same instant under
    two names; dropping one without copying it would leave every currently
    assigned ticket with no deadline and no way to reconstruct one.
    """
    assert "UPDATE ticket_assignments SET acceptance_due_at = acceptance_reassign_at" in SOURCE


def test_every_dropped_table_is_dropped_before_its_parents():
    """Children first, or the foreign keys refuse."""
    order = [
        "assignment_proposal_item_members",
        "assignment_proposal_items",
        "ai_assignment_job_members",
        "assignment_proposal_schedules",
        "ai_assignment_jobs",
        "assignment_proposal_batches",
    ]
    # Scoped to the drop loop: the table names also appear in the docstring and
    # in the detach step above it, and a whole-file search would find those.
    loop = SOURCE[SOURCE.index("for table in ("):SOURCE.index("op.drop_table(table)")]
    positions = [loop.index(f'"{name}"') for name in order]
    assert positions == sorted(positions)


def test_the_assignment_source_vocabulary_is_rewritten_not_widened():
    """One spelling in the column, so the check constraint can enumerate it."""
    for old, new in (
        ("'MANUAL', 'COORDINATOR_MANUAL'", "COORDINATOR_MANUAL"),
        ("assignment_source = 'AI_AUTO'", "AUTO_SCHEDULER"),
        ("assignment_source = 'AI_PROPOSAL_CONFIRMED'", "COORDINATOR_VISUAL"),
    ):
        assert old in SOURCE
        assert new in SOURCE


def test_the_new_internal_tables_get_row_level_security():
    """Same treatment the agent's internal tables get: no anonymous reach."""
    assert "ENABLE ROW LEVEL SECURITY" in SOURCE
    assert "REVOKE ALL ON TABLE" in SOURCE
