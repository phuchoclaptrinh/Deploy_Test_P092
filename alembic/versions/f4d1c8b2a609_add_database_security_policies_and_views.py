"""add database security policies and views

Revision ID: f4d1c8b2a609
Revises: c7a3f2d9e105
Create Date: 2026-08-05 12:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4d1c8b2a609"
down_revision: str | Sequence[str] | None = "c7a3f2d9e105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = (
    "users",
    "units",
    "user_unit_memberships",
    "technician_profiles",
    "technician_skills",
    "tickets",
    "ticket_attachments",
    "ticket_assignments",
    "ticket_status_history",
    "ai_analysis_runs",
    "ticket_scoring_results",
    "notifications",
    "audit_logs",
)

POLICY_NAMES = (
    ("users", "rls_users_deny_all"),
    ("units", "rls_units_deny_all"),
    ("user_unit_memberships", "rls_user_unit_memberships_deny_all"),
    ("technician_profiles", "rls_technician_profiles_deny_all"),
    ("technician_skills", "rls_technician_skills_deny_all"),
    ("tickets", "rls_tickets_resident_select_owned_pending_identity"),
    ("tickets", "rls_tickets_technician_select_assigned_pending_identity"),
    ("tickets", "rls_tickets_deny_client_mutation"),
    ("ticket_attachments", "rls_ticket_attachments_resident_select_owned_pending_identity"),
    ("ticket_attachments", "rls_ticket_attachments_technician_select_assigned_pending_identity"),
    ("ticket_attachments", "rls_ticket_attachments_deny_client_mutation"),
    ("ticket_assignments", "rls_ticket_assignments_technician_select_assigned_pending_identity"),
    ("ticket_assignments", "rls_ticket_assignments_deny_client_mutation"),
    ("ticket_status_history", "rls_ticket_status_history_resident_select_owned_pending_identity"),
    ("ticket_status_history", "rls_ticket_status_history_technician_select_assigned_pending_identity"),
    ("ticket_status_history", "rls_ticket_status_history_deny_client_mutation"),
    ("ai_analysis_runs", "rls_ai_analysis_runs_deny_all"),
    ("ticket_scoring_results", "rls_ticket_scoring_results_deny_all"),
    ("notifications", "rls_notifications_user_select_owned_pending_identity"),
    ("notifications", "rls_notifications_deny_client_mutation"),
    ("audit_logs", "rls_audit_logs_deny_client_select"),
    ("audit_logs", "rls_audit_logs_service_insert_pending_identity"),
    ("audit_logs", "rls_audit_logs_deny_client_update"),
    ("audit_logs", "rls_audit_logs_deny_client_delete"),
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in RLS_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")

    op.execute(
        """
        CREATE VIEW resident_ticket_view
        WITH (security_invoker = true) AS
        SELECT
            tickets.id AS ticket_id,
            tickets.title,
            tickets.description,
            tickets.category,
            tickets.priority,
            tickets.status,
            tickets.location_description,
            tickets.created_at,
            tickets.updated_at,
            tickets.resolved_at
        FROM tickets
        """
    )
    op.execute(
        """
        CREATE VIEW technician_ticket_view
        WITH (security_invoker = true) AS
        SELECT
            tickets.id AS ticket_id,
            tickets.title,
            tickets.description,
            tickets.category,
            tickets.priority,
            tickets.status,
            tickets.location_description,
            ticket_assignments.assigned_at,
            ticket_assignments.accepted_at
        FROM tickets
        JOIN ticket_assignments
          ON ticket_assignments.ticket_id = tickets.id
         AND ticket_assignments.is_active = true
        """
    )

    op.execute("CREATE POLICY rls_users_deny_all ON users USING (false) WITH CHECK (false)")
    op.execute("CREATE POLICY rls_units_deny_all ON units USING (false) WITH CHECK (false)")
    op.execute(
        """
        CREATE POLICY rls_user_unit_memberships_deny_all
        ON user_unit_memberships
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_technician_profiles_deny_all
        ON technician_profiles
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_technician_skills_deny_all
        ON technician_skills
        USING (false)
        WITH CHECK (false)
        """
    )

    # Runtime identity binding is not confirmed. These policies preserve the
    # required ownership predicates but cannot grant access until the identity
    # source is approved and NULL::uuid is replaced by that source.
    op.execute(
        """
        CREATE POLICY rls_tickets_resident_select_owned_pending_identity
        ON tickets
        FOR SELECT
        USING (
            false
            AND EXISTS (
                SELECT 1
                FROM user_unit_memberships membership
                WHERE membership.user_id = NULL::uuid
                  AND membership.unit_id = tickets.unit_id
                  AND membership.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_technician_select_assigned_pending_identity
        ON tickets
        FOR SELECT
        USING (
            false
            AND EXISTS (
                SELECT 1
                FROM ticket_assignments assignment
                WHERE assignment.ticket_id = tickets.id
                  AND assignment.technician_id = NULL::uuid
                  AND assignment.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_deny_client_mutation
        ON tickets
        FOR ALL
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_attachments_resident_select_owned_pending_identity
        ON ticket_attachments
        FOR SELECT
        USING (
            false
            AND EXISTS (
                SELECT 1
                FROM tickets
                JOIN user_unit_memberships membership
                  ON membership.unit_id = tickets.unit_id
                 AND membership.is_active = true
                WHERE tickets.id = ticket_attachments.ticket_id
                  AND membership.user_id = NULL::uuid
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_attachments_technician_select_assigned_pending_identity
        ON ticket_attachments
        FOR SELECT
        USING (
            false
            AND EXISTS (
                SELECT 1
                FROM ticket_assignments assignment
                WHERE assignment.ticket_id = ticket_attachments.ticket_id
                  AND assignment.technician_id = NULL::uuid
                  AND assignment.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_attachments_deny_client_mutation
        ON ticket_attachments
        FOR ALL
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_assignments_technician_select_assigned_pending_identity
        ON ticket_assignments
        FOR SELECT
        USING (
            false
            AND technician_id = NULL::uuid
            AND is_active = true
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_assignments_deny_client_mutation
        ON ticket_assignments
        FOR ALL
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_status_history_resident_select_owned_pending_identity
        ON ticket_status_history
        FOR SELECT
        USING (
            false
            AND EXISTS (
                SELECT 1
                FROM tickets
                JOIN user_unit_memberships membership
                  ON membership.unit_id = tickets.unit_id
                 AND membership.is_active = true
                WHERE tickets.id = ticket_status_history.ticket_id
                  AND membership.user_id = NULL::uuid
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_status_history_technician_select_assigned_pending_identity
        ON ticket_status_history
        FOR SELECT
        USING (
            false
            AND EXISTS (
                SELECT 1
                FROM ticket_assignments assignment
                WHERE assignment.ticket_id = ticket_status_history.ticket_id
                  AND assignment.technician_id = NULL::uuid
                  AND assignment.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_status_history_deny_client_mutation
        ON ticket_status_history
        FOR ALL
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ai_analysis_runs_deny_all
        ON ai_analysis_runs
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_scoring_results_deny_all
        ON ticket_scoring_results
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_notifications_user_select_owned_pending_identity
        ON notifications
        FOR SELECT
        USING (
            false
            AND recipient_user_id = NULL::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_notifications_deny_client_mutation
        ON notifications
        FOR ALL
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_audit_logs_deny_client_select
        ON audit_logs
        FOR SELECT
        USING (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_audit_logs_service_insert_pending_identity
        ON audit_logs
        FOR INSERT
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_audit_logs_deny_client_update
        ON audit_logs
        FOR UPDATE
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_audit_logs_deny_client_delete
        ON audit_logs
        FOR DELETE
        USING (false)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS technician_ticket_view")
    op.execute("DROP VIEW IF EXISTS resident_ticket_view")

    for table_name, policy_name in reversed(POLICY_NAMES):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")

    for table_name in reversed(RLS_TABLES):
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
