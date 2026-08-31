"""hard cutover to risk scoring v2

Revision ID: a1b2c3d4e5f7
Revises: 9f0a1b2c3d4e
Create Date: 2026-08-29 00:00:00.000000

The scoring model is replaced outright. Out go category base scores, location
bonuses, density bonuses, priority ceilings and the whole LOW/MEDIUM/HIGH
severity scale; in comes the five-criterion rubric of
``docs/risk_scoring_v2.md``, computed by the backend from numbers the Agent
supplies and stored as an append-only revision per ticket.

**This is a deliberate compatibility break, and it deletes data.** Nothing maps
an old ticket onto the new model, and nothing tries to. A v1 ticket carries a
severity and a category base score; a v2 ticket carries five judgements about
human safety, spread, essential function, scope and speed. There is no function
from the first to the second -- inventing one would fabricate a
``human_safety`` score nobody ever made, and every priority derived from it
would be a guess presented as a record. So the operational ticket graph is
deleted and the building keeps its people, its apartments and its catalog.

**The priority scale inverts.** P3 was the five-minute emergency and P1 the
routine multi-day promise. From here P1 is routine and **P5 is the emergency**.
This is the other reason no ticket is migrated: even a ticket whose old label
could be carried across would mean the opposite of what it says.

Order of operations, and why each step needs the one before it:

1. **Delete the operational ticket graph.** First, because steps 2 and 3 cannot
   rewrite ``tickets.priority`` while rows still hold the old three labels, and
   because a half-migrated ticket has no correct priority to land on.
2. **Rebuild ``priority_level_enum``** with five labels. PostgreSQL can add a
   label in place, but the two new ones have to sort after the old three and
   ``ADD VALUE`` cannot run inside this transaction alongside the columns that
   use it -- so the type is rebuilt and every column swapped onto it.
3. **Drop the v1 scoring surface**: ``tickets.severity``,
   ``tickets.severity_source``, ``tickets.red_flag_detected``,
   ``tickets.score_total``, ``categories.base_score``,
   ``categories.priority_ceiling`` and the constraint that made an active
   category require a base score, the severity columns on
   ``ai_analysis_runs``, and ``scoring_rule_versions`` with the
   ``rule_version_id`` that pointed at it.
4. **Rename the emergency gate** from ``p3_review_*`` to ``emergency_review_*``
   and give its two enums real PostgreSQL types. The gate did not change; only
   the band behind it did, and leaving the old names would put "P3 review" on
   every screen showing a P5.
5. **Create ``ticket_risk_assessments``** and the two ticket cache columns that
   point at it.

**Forward-only.** ``downgrade`` raises. Recreating ``severity_v2_enum`` and
``categories.base_score`` would report success while every ticket, assignment,
analysis run and dispatch event this revision deleted stayed gone, and while
every risk assessment written since had no column to go back to. This follows
the convention of ``8e9f0a1b2c3d`` and ``9f0a1b2c3d4e``: a revision that
deletes data does not claim to be reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | Sequence[str] | None = "9f0a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Children before parents. Identical in shape to ``9f0a1b2c3d4e``'s list and
#: for the same reason: everything here either holds a ``ticket_id`` or exists
#: only to describe tickets.
#:
#: What survives, deliberately: ``user_profiles``, ``resident_profiles``,
#: ``technician_profiles``, ``technician_skills``,
#: ``technician_availability_events``, ``categories``, ``locations``,
#: ``location_types``, ``floors``, ``units`` and ``auto_assignment_settings``.
#: The building and the people in it are not ticket data.
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
    # Before `ai_analysis_runs`, whose `risk_assessment_id` points back at it.
    # That pair is a cycle, broken the same way the tickets <-> runs cycle is:
    # the back-reference is nulled before the loop starts.
    "ticket_risk_assessments",
    "ai_analysis_runs",
    "ai_analysis_sessions",
    "tickets",
    "resident_ticket_rate_limits",
    "ticket_attachment_upload_sessions",
)

NEW_PRIORITIES = ("P1", "P2", "P3", "P4", "P5")

#: Every column on ``priority_level_enum``, as (table, column). All of them are
#: swapped onto the rebuilt type in one pass; a column left behind would pin the
#: old type and make the DROP fail.
PRIORITY_COLUMNS = (
    ("tickets", "priority"),
    ("ai_analysis_runs", "ai_priority_before_review"),
    ("ai_analysis_runs", "effective_priority"),
    ("ai_analysis_runs", "priority_raw"),
    ("ai_analysis_runs", "priority_final"),
    ("categories", "priority_ceiling"),
)

#: v1 scoring columns, by table. Dropped after the enum swap because two of them
#: are themselves ``priority_level_enum`` columns and have to be rebuilt first
#: or dropped last -- dropping last is simpler and leaves no window where the
#: type has a dangling dependency.
DROPPED_COLUMNS = {
    "tickets": ("severity", "severity_source", "red_flag_detected", "score_total"),
    "categories": ("base_score", "priority_ceiling"),
    "ai_analysis_runs": (
        "severity",
        "severity_source",
        "red_flag",
        "red_flag_text",
        "red_flag_signal",
        "red_flag_relation",
        "rule_version_id",
        "score_components",
        "score_total",
        "priority_raw",
        "priority_final",
        "ceiling_applied",
    ),
}

#: ``p3_review_*`` -> ``emergency_review_*``. Renames rather than drop/add, so
#: the review history of tickets written before the cutover would survive if any
#: existed. None do -- step 1 deleted them -- but a rename states the intent:
#: this is the same gate under a new name, not a new gate.
RENAMED_REVIEW_COLUMNS = (
    ("p3_review_status", "emergency_review_status"),
    ("p3_reviewed_by", "emergency_reviewed_by"),
    ("p3_reviewed_at", "emergency_reviewed_at"),
    ("p3_decision", "emergency_decision"),
    ("p3_decision_reason", "emergency_decision_reason"),
)

EMERGENCY_REVIEW_STATUSES = ("NOT_REQUIRED", "PENDING", "CONFIRMED", "DOWNGRADED")
EMERGENCY_DECISIONS = ("CONFIRM_P5", "DOWNGRADE_PRIORITY")
RISK_ASSESSMENT_SOURCES = ("AI_ANALYSIS", "GROUPING_RESCORE", "HUMAN_REVIEW", "DUPLICATE_ESCALATION")

#: 0-4 on every criterion column, and the two scope columns that share the
#: scale. Written as one loop so the rubric's range is stated once.
CRITERION_COLUMNS = (
    "human_safety_score",
    "property_spread_score",
    "essential_function_score",
    "ai_scope_score",
    "backend_scope_score",
    "effective_scope_score",
    "deterioration_speed_score",
)

JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


#: §7.1, defined by `5e6f7a8b9c0d` and still live in
#: `src/database/models/ticket.py`. Dropped and recreated around the teardown
#: below, never removed: v2 keeps it.
LINKED_DUPLICATE_CHECK = "ck_tickets_linked_duplicate_needs_master"
LINKED_DUPLICATE_CONDITION = "status <> 'LINKED_DUPLICATE' OR duplicate_of_ticket_id IS NOT NULL"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _has_check_constraint(inspector: sa.Inspector, table: str, name: str) -> bool:
    """Whether `table` carries a check constraint called `name`.

    Asked rather than assumed: a database that never ran `5e6f7a8b9c0d` does
    not have it, and dropping a constraint that is not there would turn a
    missing precondition into a failed migration.
    """
    try:
        return any(item.get("name") == name for item in inspector.get_check_constraints(table))
    except NotImplementedError:
        # Some dialects cannot report check constraints. Leaving it in place is
        # the safe answer: the UPDATE below either works or fails loudly.
        return False


def _existing_enum(*labels: str, name: str) -> sa.Enum:
    """A column type for an enum this migration has already created itself.

    `create_type=False` is a PostgreSQL-dialect argument. The generic
    `sa.Enum` accepts it and then loses it on the way to the dialect -- the
    impl comes back with `create_type=True` -- so `op.create_table` emits a
    second `CREATE TYPE` after the explicit one above and a real server
    rejects it with `DuplicateObject`. SQLite renders VARCHAR + CHECK and has
    no type to collide with, which is why the test suite never saw this.
    """
    if _is_postgres():
        return postgresql.ENUM(*labels, name=name, create_type=False)
    return sa.Enum(*labels, name=name, native_enum=True)


def _priority_enum() -> sa.Enum:
    return _existing_enum(*NEW_PRIORITIES, name="priority_level_enum")


def clear_ticket_domain(inspector: sa.Inspector, existing: set[str]) -> None:
    """Empty every ticket-domain table, cycles and invariants handled.

    Its own function so it can be executed by a test. Every other migration
    test in this repository reads the source text, which is how the constraint
    violation below reached a real database unnoticed: nothing had ever run
    this code against a row. It cannot be covered by running the chain on
    SQLite either -- an earlier revision renders JSONB unguarded, so the v1
    chain does not build there at all.
    """
    # ------------------------------------------------------------------- 1
    # Break the tickets <-> ai_analysis_runs cycle and tickets' self-reference
    # before deleting either side, exactly as 9f0a1b2c3d4e does. Both foreign
    # keys are ON DELETE SET NULL; the order is made explicit rather than left
    # to the database.
    suspended_duplicate_check = False
    if "tickets" in existing:
        # `duplicate_of_ticket_id` cannot simply be nulled. §7.1's constraint
        # says a LINKED_DUPLICATE ticket must point at a master, so clearing
        # the pointer while the status still reads LINKED_DUPLICATE violates
        # it. The first run of this revision against a real database aborted
        # exactly here, on a ticket that had been linked as a duplicate two
        # days earlier -- and aborted cleanly, because `env.py` runs one
        # transaction per revision, which is the only reason that discovery
        # cost nothing.
        #
        # The invariant is suspended rather than worked around. Every row in
        # this table is about to be deleted, so there is no state left for it
        # to protect, and it is recreated below against an empty table, where
        # it holds trivially and goes on holding for v2. Deleting the
        # LINKED_DUPLICATE rows first would look simpler and is not: a master
        # that is itself a duplicate would fire ON DELETE SET NULL onto another
        # constrained row, in a statement whose row order nothing guarantees.
        if _has_check_constraint(inspector, "tickets", LINKED_DUPLICATE_CHECK):
            with op.batch_alter_table("tickets") as batch:
                batch.drop_constraint(LINKED_DUPLICATE_CHECK, type_="check")
            suspended_duplicate_check = True

        op.execute(sa.text("UPDATE tickets SET duplicate_analysis_run_id = NULL"))
        op.execute(sa.text("UPDATE tickets SET duplicate_of_ticket_id = NULL"))
        # Only present when this list is reused by a later reset: this revision
        # creates the column further down, after the delete loop has run.
        if "current_risk_assessment_id" in {item["name"] for item in inspector.get_columns("tickets")}:
            op.execute(sa.text("UPDATE tickets SET current_risk_assessment_id = NULL"))
    if "ai_analysis_runs" in existing:
        if "risk_assessment_id" in {item["name"] for item in inspector.get_columns("ai_analysis_runs")}:
            op.execute(sa.text("UPDATE ai_analysis_runs SET risk_assessment_id = NULL"))

    for table in TICKET_DOMAIN_TABLES:
        if table in existing:
            op.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608 - literal names, tuple above

    # The table is empty now, so the suspended invariant is restored against
    # nothing and is in force for every row v2 goes on to write.
    if suspended_duplicate_check:
        with op.batch_alter_table("tickets") as batch:
            batch.create_check_constraint(LINKED_DUPLICATE_CHECK, LINKED_DUPLICATE_CONDITION)

    # Only the ticket-domain audit trail. Account, category and
    # auto-assignment history is not ticket data and stays.
    if "audit_logs" in existing:
        op.execute(sa.text("DELETE FROM audit_logs WHERE entity_type IN ('TICKET', 'TICKET_ASSIGNMENT')"))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    clear_ticket_domain(inspector, existing)

    # ------------------------------------------------------------------- 2
    # Rebuild priority_level_enum with five labels.
    #
    # SQLite renders these columns as VARCHAR + CHECK, so there is no type to
    # rebuild there and the Python enum is the whole constraint.
    if _is_postgres():
        labels = ", ".join(f"'{value}'" for value in NEW_PRIORITIES)
        op.execute(sa.text(f"CREATE TYPE priority_level_enum_new AS ENUM ({labels})"))
        for table, column in PRIORITY_COLUMNS:
            if table not in existing:
                continue
            column_names = {item["name"] for item in inspector.get_columns(table)}
            if column not in column_names:
                continue
            op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"))
            op.execute(
                sa.text(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "
                    f"TYPE priority_level_enum_new USING {column}::text::priority_level_enum_new"
                )
            )
        op.execute(sa.text("DROP TYPE priority_level_enum"))
        op.execute(sa.text("ALTER TYPE priority_level_enum_new RENAME TO priority_level_enum"))

    # ------------------------------------------------------------------- 3
    # Drop the v1 scoring surface.
    if "categories" in existing:
        op.execute(
            sa.text("ALTER TABLE categories DROP CONSTRAINT IF EXISTS ck_categories_active_base_score_required")
        )

    for table, columns in DROPPED_COLUMNS.items():
        if table not in existing:
            continue
        present = {item["name"] for item in inspector.get_columns(table)}
        for column in columns:
            if column in present:
                op.drop_column(table, column)

    if "scoring_rule_versions" in existing:
        op.drop_table("scoring_rule_versions")

    if _is_postgres():
        # Nothing references these any more. IF EXISTS rather than a bare DROP
        # because a database restored from a dump taken mid-rollout may not
        # have had them in the first place.
        op.execute(sa.text("DROP TYPE IF EXISTS severity_v2_enum"))
        op.execute(sa.text("DROP TYPE IF EXISTS severity_source_enum"))

    # ------------------------------------------------------------------- 4
    # Rename the emergency gate and give its two enums real types.
    if "ai_analysis_runs" in existing:
        present = {item["name"] for item in inspector.get_columns("ai_analysis_runs")}
        for old_name, new_name in RENAMED_REVIEW_COLUMNS:
            if old_name in present:
                op.alter_column("ai_analysis_runs", old_name, new_column_name=new_name)

        if _is_postgres():
            status_labels = ", ".join(f"'{value}'" for value in EMERGENCY_REVIEW_STATUSES)
            decision_labels = ", ".join(f"'{value}'" for value in EMERGENCY_DECISIONS)
            op.execute(sa.text(f"CREATE TYPE emergency_review_status_enum AS ENUM ({status_labels})"))
            op.execute(sa.text(f"CREATE TYPE emergency_decision_enum AS ENUM ({decision_labels})"))
            # The columns were VARCHAR under the old names. They are empty --
            # step 1 deleted every run -- so the USING clause has nothing to
            # convert and exists only to satisfy the type change.
            op.execute(
                sa.text(
                    "ALTER TABLE ai_analysis_runs ALTER COLUMN emergency_review_status "
                    "TYPE emergency_review_status_enum USING emergency_review_status::text::emergency_review_status_enum"
                )
            )
            op.execute(
                sa.text(
                    "ALTER TABLE ai_analysis_runs ALTER COLUMN emergency_decision "
                    "TYPE emergency_decision_enum USING emergency_decision::text::emergency_decision_enum"
                )
            )

    # ------------------------------------------------------------------- 5
    # The new record, and the ticket cache that points at it.
    if _is_postgres():
        source_labels = ", ".join(f"'{value}'" for value in RISK_ASSESSMENT_SOURCES)
        op.execute(sa.text(f"CREATE TYPE risk_assessment_source_enum AS ENUM ({source_labels})"))

    op.create_table(
        "ticket_risk_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "source",
            _existing_enum(*RISK_ASSESSMENT_SOURCES, name="risk_assessment_source_enum"),
            nullable=False,
        ),
        sa.Column(
            "ai_analysis_run_id",
            sa.Uuid(),
            sa.ForeignKey("ai_analysis_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("ticket_risk_assessments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("human_safety_score", sa.Integer(), nullable=False),
        sa.Column("property_spread_score", sa.Integer(), nullable=False),
        sa.Column("essential_function_score", sa.Integer(), nullable=False),
        sa.Column("ai_scope_score", sa.Integer(), nullable=False),
        sa.Column("backend_scope_score", sa.Integer(), nullable=True),
        sa.Column("effective_scope_score", sa.Integer(), nullable=False),
        sa.Column("deterioration_speed_score", sa.Integer(), nullable=False),
        sa.Column("confirmed_affected_unit_count", sa.Integer(), nullable=True),
        sa.Column("blocker_codes", JSON_TYPE, nullable=False),
        sa.Column("evidence", JSON_TYPE, nullable=False),
        sa.Column("unknown_facts", JSON_TYPE, nullable=False),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("score_priority", _priority_enum(), nullable=False),
        sa.Column("blocker_floor", _priority_enum(), nullable=True),
        sa.Column("final_priority", _priority_enum(), nullable=False),
        sa.Column("rubric_version", sa.String(32), nullable=False),
        sa.Column("case_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("case_density_snapshot", sa.Integer(), nullable=True),
        sa.Column("override_reason", sa.String(1000), nullable=True),
        sa.Column(
            "reviewed_by",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ticket_id", "revision_no", name="uq_ticket_risk_assessments_ticket_revision"),
        sa.CheckConstraint("revision_no >= 1", name="ck_ticket_risk_assessments_revision_positive"),
        *(
            sa.CheckConstraint(
                f"{column} IS NULL OR ({column} >= 0 AND {column} <= 4)",
                name=f"ck_ticket_risk_assessments_{column}_range",
            )
            for column in CRITERION_COLUMNS
        ),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_ticket_risk_assessments_score_range"),
        sa.CheckConstraint(
            "confirmed_affected_unit_count IS NULL OR "
            "(confirmed_affected_unit_count >= 1 AND confirmed_affected_unit_count <= 5)",
            name="ck_ticket_risk_assessments_unit_count_range",
        ),
        sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="ck_ticket_risk_assessments_not_self"),
    )
    op.create_index("ix_ticket_risk_assessments_ticket_id", "ticket_risk_assessments", ["ticket_id"])
    op.create_index(
        "ix_ticket_risk_assessments_ticket_revision", "ticket_risk_assessments", ["ticket_id", "revision_no"]
    )

    op.add_column("tickets", sa.Column("current_risk_assessment_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tickets_current_risk_assessment",
        "tickets",
        "ticket_risk_assessments",
        ["current_risk_assessment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("tickets", sa.Column("risk_score", sa.Numeric(5, 2), nullable=True))

    op.add_column("ai_analysis_runs", sa.Column("risk_assessment_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_ai_analysis_runs_risk_assessment",
        "ai_analysis_runs",
        "ticket_risk_assessments",
        ["risk_assessment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    raise RuntimeError(
        "a1b2c3d4e5f7 is forward-only: it deletes every ticket, assignment, analysis run and "
        "dispatch event, drops severity, base_score and priority_ceiling, and inverts the "
        "priority scale so that P5 rather than P3 is the emergency. Restoring the old columns "
        "would restore none of that data and would leave every risk assessment written since "
        "with nowhere to go."
    )
