"""align rls with private ai phase

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-23 10:30:00.000000

Defence in depth for the visibility rule the API already enforces.

Until now `rls_tickets_resident_or_coordinator_select` (a7b8c9d0e1f2) let any
member of the reporting apartment — and any coordinator — select any row of
their unit, and `rls_ticket_attachments_visible_ticket` let a client read an
attachment as long as *some* ticket owned it. Under the new rule a report is
private to its reporter while the Agent is working on it:

    private  <=>  classification_status IN ('PENDING', 'PROCESSING')

so those policies would still hand a housemate or a coordinator a row the API
refuses to return. This revision replaces them:

* residents select their unit's tickets only when they sent the report or
  classification has finished;
* coordinators select only tickets whose classification has finished;
* attachments and status history inherit the authorized parent ticket instead
  of merely requiring that a parent exists;
* `ai_agent_questions` keeps its explicit deny — the AI conversation is reached
  through the reporter-only backend endpoints, never through the database.

The older policy migrations are immutable; this is the forward correction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8b9c0d1e2f3a"
down_revision: str | Sequence[str] | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The single source of the rule inside the database, mirroring
#: `src.services.ticket_visibility.PRIVATE_AI_PHASE`.
_PUBLISHED = "t.classification_status NOT IN ('PENDING', 'PROCESSING')"

_UPGRADE = f"""
DROP POLICY IF EXISTS rls_tickets_resident_or_coordinator_select ON tickets;
CREATE POLICY rls_tickets_visible_phase_select ON tickets
FOR SELECT TO authenticated USING (
  EXISTS (
    SELECT 1 FROM tickets t
    WHERE t.id = tickets.id
      AND (
        -- The reporter always sees their own report, including while the
        -- Agent is still analysing it or waiting for their answer.
        (
          t.reporter_user_id = (SELECT auth.uid())
          AND EXISTS (
            SELECT 1 FROM user_profiles up
            WHERE up.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'RESIDENT'
          )
        )
        -- Everyone else waits for classification to finish: the rest of the
        -- apartment, and Building Management.
        OR (
          {_PUBLISHED}
          AND (
            EXISTS (
              SELECT 1 FROM user_profiles up
              WHERE up.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'COORDINATOR'
            )
            OR t.source_unit_id IN (
              SELECT rp.unit_id FROM resident_profiles rp
              JOIN user_profiles up ON up.user_id = rp.user_id
              WHERE rp.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'RESIDENT'
            )
          )
        )
      )
  )
);

DROP POLICY IF EXISTS rls_ticket_attachments_visible_ticket ON ticket_attachments;
CREATE POLICY rls_ticket_attachments_visible_ticket ON ticket_attachments
FOR SELECT TO authenticated USING (
  EXISTS (
    SELECT 1 FROM tickets t
    WHERE t.id = ticket_attachments.ticket_id
      AND (
        (
          t.reporter_user_id = (SELECT auth.uid())
          AND EXISTS (
            SELECT 1 FROM user_profiles up
            WHERE up.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'RESIDENT'
          )
        )
        OR (
          {_PUBLISHED}
          AND (
            EXISTS (
              SELECT 1 FROM user_profiles up
              WHERE up.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'COORDINATOR'
            )
            OR t.source_unit_id IN (
              SELECT rp.unit_id FROM resident_profiles rp
              JOIN user_profiles up ON up.user_id = rp.user_id
              WHERE rp.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'RESIDENT'
            )
          )
        )
      )
  )
);

DROP POLICY IF EXISTS rls_ticket_status_history_visible_ticket ON ticket_status_history;
CREATE POLICY rls_ticket_status_history_visible_ticket ON ticket_status_history
FOR SELECT TO authenticated USING (
  EXISTS (
    SELECT 1 FROM tickets t
    WHERE t.id = ticket_status_history.ticket_id
      AND (
        (
          t.reporter_user_id = (SELECT auth.uid())
          AND EXISTS (
            SELECT 1 FROM user_profiles up
            WHERE up.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'RESIDENT'
          )
        )
        OR (
          {_PUBLISHED}
          AND (
            EXISTS (
              SELECT 1 FROM user_profiles up
              WHERE up.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'COORDINATOR'
            )
            OR t.source_unit_id IN (
              SELECT rp.unit_id FROM resident_profiles rp
              JOIN user_profiles up ON up.user_id = rp.user_id
              WHERE rp.user_id = (SELECT auth.uid()) AND up.is_active = true AND up.role = 'RESIDENT'
            )
          )
        )
      )
  )
);

-- The AI question/answer conversation is reporter-only and is served by the
-- backend, so direct client access stays denied outright.
ALTER TABLE ai_agent_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_agent_questions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE ai_agent_questions FROM PUBLIC;
DROP POLICY IF EXISTS rls_ai_agent_questions_deny_client ON ai_agent_questions;
CREATE POLICY rls_ai_agent_questions_deny_client ON ai_agent_questions
FOR ALL TO authenticated USING (false) WITH CHECK (false);
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite test databases have no row-level security to align.
        return
    op.execute(_UPGRADE)


def downgrade() -> None:
    """Restore the pre-private-phase policies verbatim."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DROP POLICY IF EXISTS rls_ai_agent_questions_deny_client ON ai_agent_questions;

        DROP POLICY IF EXISTS rls_ticket_status_history_visible_ticket ON ticket_status_history;
        CREATE POLICY rls_ticket_status_history_visible_ticket ON ticket_status_history
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM tickets t WHERE t.id = ticket_status_history.ticket_id)
        );

        DROP POLICY IF EXISTS rls_ticket_attachments_visible_ticket ON ticket_attachments;
        CREATE POLICY rls_ticket_attachments_visible_ticket ON ticket_attachments
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM tickets t WHERE t.id = ticket_attachments.ticket_id)
        );

        DROP POLICY IF EXISTS rls_tickets_visible_phase_select ON tickets;
        CREATE POLICY rls_tickets_resident_or_coordinator_select ON tickets
        FOR SELECT TO authenticated USING (
          EXISTS (
            SELECT 1 FROM user_profiles up
            WHERE up.user_id=(SELECT auth.uid()) AND up.is_active=true AND up.role='COORDINATOR'
          )
          OR source_unit_id IN (
            SELECT rp.unit_id FROM resident_profiles rp
            JOIN user_profiles up ON up.user_id=rp.user_id
            WHERE rp.user_id=(SELECT auth.uid()) AND up.is_active=true AND up.role='RESIDENT'
          )
        );
        """
    )
