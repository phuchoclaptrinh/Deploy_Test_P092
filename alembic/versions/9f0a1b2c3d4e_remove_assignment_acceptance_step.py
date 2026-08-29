"""remove the assignment acceptance step

Revision ID: 9f0a1b2c3d4e
Revises: 8e9f0a1b2c3d
Create Date: 2026-08-27 00:00:00.000000

The technician lifecycle loses its acknowledgement step:

    before:  ASSIGNED -> ACCEPTED -> IN_PROGRESS -> COMPLETED
    after:   ASSIGNED ------------> IN_PROGRESS -> COMPLETED

Three moves, in this order, because each one depends on the last:

1. **Delete the ticket domain.** This is a test environment and the deletion is
   authorised. It comes first because step 2 cannot rewrite a column while rows
   still hold ``'ACCEPTED'``, and because a half-migrated ticket whose
   technician had accepted but not started would have no correct state to land
   in.
2. **Replace ``assignment_status_enum``.** PostgreSQL cannot drop one label
   from an enum type, so the type is rebuilt without ``ACCEPTED`` and the
   column is swapped onto it.
3. **Drop the acceptance columns and their indexes**, plus ``cycle_started_at``,
   whose only purpose was to be the clock those deadlines were measured from.

**What is deliberately NOT added.** No ``start_due_at``, no
``start_warning_at``. The old ``acceptance_due_at`` cannot simply be renamed:
it was measured from the moment work was handed over, and a ticket legitimately
third in a queue is *planned* to begin hours after that. Turning
``planned_start_at`` into a hard deadline -- and choosing the grace period and
what happens when it passes -- is a business decision that has not been made,
and a column added now would need a default that quietly became the policy.
Those columns are a one-line ``add_column`` the day the rule exists.

**What is kept.** ``assigned_at``, ``planned_start_at``, ``planned_finish_at``,
``planned_order``, ``risk_state``, ``slack_seconds``, ``started_at``,
``completed_at``, ``ended_at``, ``end_reason``, and the partial unique index
that allows a technician only one IN_PROGRESS assignment at a time -- which
matters more than before, because starting work is now the only way into that
state.

**Delete scope.** Everything in ``TICKET_DOMAIN_TABLES`` either holds a
``ticket_id`` or exists only to describe tickets. Nothing that describes *the
building or its people* is touched: ``user_profiles``, ``resident_profiles``,
``technician_profiles``, ``technician_skills``,
``technician_availability_events``, ``categories``, ``locations``,
``location_types``, ``floors``, ``units``, ``scoring_rule_versions`` and
``auto_assignment_settings`` all survive, and ``audit_logs`` loses only the
rows whose ``entity_type`` is a ticket or an assignment -- account and catalog
history is not ticket data.

Two entries deserve their reason spelled out:

* ``resident_ticket_rate_limits`` -- daily counters derived purely from ticket
  activity. Leaving them would block a resident from reporting because of
  tickets that no longer exist.
* ``ticket_attachment_upload_sessions`` -- signed-upload handles for photos on
  tickets that are being deleted. They carry no ``ticket_id``, but they are
  ticket-domain data and every one of them is now unusable.

**Forward-only.** ``downgrade`` raises. Recreating the ``ACCEPTED`` label would
report success while every ticket, assignment, analysis run and dispatch event
this revision deleted stayed gone -- and the acceptance timestamps it dropped
cannot be recovered from anywhere. This follows ``8e9f0a1b2c3d``'s convention:
a revision that deletes data does not claim to be reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9f0a1b2c3d4e"
down_revision: str | Sequence[str] | None = "8e9f0a1b2c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Children before parents. ``tickets`` comes after everything that points at
#: it; the two tail entries hold no ``ticket_id`` and are explained above.
TICKET_DOMAIN_TABLES = (
    "at_risk_decisions",
    "ticket_relations",
    "ai_agent_tool_calls",
    "ai_agent_questions",
    "ticket_status_history",
    "ticket_attachments",
    "information_requests",
    "notifications",
    "incident_case_members",
    "incident_cases",
    "ticket_assignments",
    "dispatch_events",
    "ai_analysis_runs",
    "ai_analysis_sessions",
    "tickets",
    "resident_ticket_rate_limits",
    "ticket_attachment_upload_sessions",
)

#: The acceptance step's own columns. ``cycle_started_at`` is here because its
#: only defined purpose was to anchor those deadlines.
ACCEPTANCE_COLUMNS = (
    "accepted_at",
    "acceptance_due_at",
    "acceptance_warning_at",
    "warning_sent_at",
    "cycle_started_at",
)

ACCEPTANCE_INDEXES = (
    "ix_ticket_assignments_acceptance_due_at",
    "ix_ticket_assignments_acceptance_warning_at",
)

NEW_ASSIGNMENT_STATUSES = (
    "ASSIGNED",
    "IN_PROGRESS",
    "COMPLETED",
    "REJECTED",
    "REASSIGNED",
    "UNABLE_TO_HANDLE",
)

#: Objects that embed an ``assignment_status_enum`` literal and therefore pin
#: the old type in place. ``ALTER COLUMN ... TYPE`` does not rewrite them; it
#: fails with "operator does not exist: assignment_status_enum_new =
#: assignment_status_enum", so they are dropped before the swap and rebuilt
#: against the new type after it.
#:
#: The partial unique index is §3's one-IN_PROGRESS-per-technician rule, and it
#: is recreated verbatim -- it is the only thing standing between two concurrent
#: `/start` calls and a technician on two live jobs, and it matters more now
#: that starting is the sole way into that state.
STATUS_DEPENDENTS = (
    (
        "DROP INDEX IF EXISTS uq_ticket_assignments_one_in_progress_per_technician",
        "CREATE UNIQUE INDEX uq_ticket_assignments_one_in_progress_per_technician"
        " ON public.ticket_assignments USING btree (technician_id)"
        " WHERE (status = 'IN_PROGRESS'::assignment_status_enum AND is_active)",
    ),
    (
        "ALTER TABLE ticket_assignments DROP CONSTRAINT IF EXISTS"
        " ck_ticket_assignments_unable_reason_required",
        "ALTER TABLE ticket_assignments ADD CONSTRAINT"
        " ck_ticket_assignments_unable_reason_required CHECK ("
        "status <> 'UNABLE_TO_HANDLE'::assignment_status_enum"
        " OR (unable_reason IS NOT NULL AND length(trim(both from unable_reason)) > 0))",
    ),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # ------------------------------------------------------------------- 1
    # Break the tickets <-> ai_analysis_runs cycle, and tickets' self-reference,
    # before deleting either side. Both foreign keys are ON DELETE SET NULL, but
    # the order is made explicit here rather than left to the database.
    if "tickets" in existing:
        op.execute(sa.text("UPDATE tickets SET duplicate_analysis_run_id = NULL"))
        op.execute(sa.text("UPDATE tickets SET duplicate_of_ticket_id = NULL"))

    for table in TICKET_DOMAIN_TABLES:
        if table in existing:
            op.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608 - literal names, tuple above

    # Only the ticket-domain audit trail, which is where ACCEPT_ASSIGNMENT and
    # ACCEPTANCE_TIMEOUT_REASSIGN entries live. Account, category and
    # auto-assignment history is not ticket data and stays.
    if "audit_logs" in existing:
        op.execute(sa.text("DELETE FROM audit_logs WHERE entity_type IN ('TICKET', 'TICKET_ASSIGNMENT')"))

    # ------------------------------------------------------------------- 2
    # SQLite renders this column as a VARCHAR + CHECK, so there is no type to
    # rebuild there; the Python enum is the whole constraint.
    if _is_postgres():
        labels = ", ".join(f"'{value}'" for value in NEW_ASSIGNMENT_STATUSES)
        op.execute(sa.text(f"CREATE TYPE assignment_status_enum_new AS ENUM ({labels})"))
        # The default and the two dependents all embed a literal of the old
        # type. None of them is rewritten by ALTER COLUMN TYPE, and each one
        # left in place makes the retype fail outright -- so they come off
        # first and go back on afterwards, against the renamed type.
        op.execute(sa.text("ALTER TABLE ticket_assignments ALTER COLUMN status DROP DEFAULT"))
        for drop, _restore in STATUS_DEPENDENTS:
            op.execute(sa.text(drop))
        op.execute(
            sa.text(
                "ALTER TABLE ticket_assignments ALTER COLUMN status "
                "TYPE assignment_status_enum_new USING status::text::assignment_status_enum_new"
            )
        )
        op.execute(sa.text("DROP TYPE assignment_status_enum"))
        op.execute(sa.text("ALTER TYPE assignment_status_enum_new RENAME TO assignment_status_enum"))
        op.execute(
            sa.text(
                "ALTER TABLE ticket_assignments ALTER COLUMN status "
                "SET DEFAULT 'ASSIGNED'::assignment_status_enum"
            )
        )
        for _drop, restore in STATUS_DEPENDENTS:
            op.execute(sa.text(restore))

    # ------------------------------------------------------------------- 3
    if "ticket_assignments" in existing:
        index_names = {index["name"] for index in inspector.get_indexes("ticket_assignments")}
        for name in ACCEPTANCE_INDEXES:
            if name in index_names:
                op.drop_index(name, table_name="ticket_assignments")

        columns = {column["name"] for column in inspector.get_columns("ticket_assignments")}
        for name in ACCEPTANCE_COLUMNS:
            if name in columns:
                op.drop_column("ticket_assignments", name)


def downgrade() -> None:
    raise RuntimeError(
        "9f0a1b2c3d4e is forward-only: it deletes every ticket, assignment, analysis run and "
        "dispatch event, and drops the acceptance timestamps. Restoring the ACCEPTED enum label "
        "would not restore any of that."
    )
