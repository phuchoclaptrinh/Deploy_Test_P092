"""bind supabase auth identity policies

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-05 13:45:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_POLICIES = (
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

NEW_POLICIES = (
    ("users", "rls_users_select_own_profile"),
    ("user_unit_memberships", "rls_memberships_select_own_active"),
    ("units", "rls_units_select_own_active_membership"),
    ("tickets", "rls_tickets_resident_select_owned"),
    ("tickets", "rls_tickets_technician_select_assigned"),
    ("tickets", "rls_tickets_coordinator_select_all_mvp"),
    ("tickets", "rls_tickets_deny_client_insert"),
    ("tickets", "rls_tickets_deny_client_update"),
    ("tickets", "rls_tickets_deny_client_delete"),
    ("ticket_attachments", "rls_ticket_attachments_select_authorized_parent"),
    ("ticket_attachments", "rls_ticket_attachments_deny_client_mutation"),
    ("ticket_status_history", "rls_ticket_status_history_select_authorized_parent"),
    ("ticket_status_history", "rls_ticket_status_history_deny_client_mutation"),
    ("ticket_assignments", "rls_ticket_assignments_technician_select_own"),
    ("ticket_assignments", "rls_ticket_assignments_coordinator_select_all_mvp"),
    ("ticket_assignments", "rls_ticket_assignments_deny_client_mutation"),
    ("notifications", "rls_notifications_select_own"),
    ("notifications", "rls_notifications_deny_client_mutation"),
    ("technician_profiles", "rls_technician_profiles_select_own"),
    ("technician_skills", "rls_technician_skills_select_own"),
    ("ai_analysis_runs", "rls_ai_analysis_runs_deny_all"),
    ("ticket_scoring_results", "rls_ticket_scoring_results_deny_all"),
    ("audit_logs", "rls_audit_logs_deny_all_client_access"),
)

OLD_POLICY_SQL = (
    "CREATE POLICY rls_users_deny_all ON users USING (false) WITH CHECK (false)",
    "CREATE POLICY rls_units_deny_all ON units USING (false) WITH CHECK (false)",
    """
    CREATE POLICY rls_user_unit_memberships_deny_all
    ON user_unit_memberships
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_technician_profiles_deny_all
    ON technician_profiles
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_technician_skills_deny_all
    ON technician_skills
    USING (false)
    WITH CHECK (false)
    """,
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
    """,
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
    """,
    """
    CREATE POLICY rls_tickets_deny_client_mutation
    ON tickets
    FOR ALL
    USING (false)
    WITH CHECK (false)
    """,
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
    """,
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
    """,
    """
    CREATE POLICY rls_ticket_attachments_deny_client_mutation
    ON ticket_attachments
    FOR ALL
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_ticket_assignments_technician_select_assigned_pending_identity
    ON ticket_assignments
    FOR SELECT
    USING (
        false
        AND technician_id = NULL::uuid
        AND is_active = true
    )
    """,
    """
    CREATE POLICY rls_ticket_assignments_deny_client_mutation
    ON ticket_assignments
    FOR ALL
    USING (false)
    WITH CHECK (false)
    """,
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
    """,
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
    """,
    """
    CREATE POLICY rls_ticket_status_history_deny_client_mutation
    ON ticket_status_history
    FOR ALL
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_ai_analysis_runs_deny_all
    ON ai_analysis_runs
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_ticket_scoring_results_deny_all
    ON ticket_scoring_results
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_notifications_user_select_owned_pending_identity
    ON notifications
    FOR SELECT
    USING (
        false
        AND recipient_user_id = NULL::uuid
    )
    """,
    """
    CREATE POLICY rls_notifications_deny_client_mutation
    ON notifications
    FOR ALL
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_audit_logs_deny_client_select
    ON audit_logs
    FOR SELECT
    USING (false)
    """,
    """
    CREATE POLICY rls_audit_logs_service_insert_pending_identity
    ON audit_logs
    FOR INSERT
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_audit_logs_deny_client_update
    ON audit_logs
    FOR UPDATE
    USING (false)
    WITH CHECK (false)
    """,
    """
    CREATE POLICY rls_audit_logs_deny_client_delete
    ON audit_logs
    FOR DELETE
    USING (false)
    """,
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, policy_name in OLD_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")

    op.execute(
        """
        CREATE POLICY rls_users_select_own_profile
        ON users FOR SELECT TO authenticated
        USING (id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY rls_memberships_select_own_active
        ON user_unit_memberships FOR SELECT TO authenticated
        USING (user_id = (select auth.uid()) AND is_active = true)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_units_select_own_active_membership
        ON units FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM user_unit_memberships membership
                WHERE membership.unit_id = units.id
                  AND membership.user_id = (select auth.uid())
                  AND membership.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_resident_select_owned
        ON tickets FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM user_unit_memberships membership
                WHERE membership.user_id = (select auth.uid())
                  AND membership.unit_id = tickets.unit_id
                  AND membership.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_technician_select_assigned
        ON tickets FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM ticket_assignments assignment
                WHERE assignment.ticket_id = tickets.id
                  AND assignment.technician_id = (select auth.uid())
                  AND assignment.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_coordinator_select_all_mvp
        ON tickets FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM users app_user
                WHERE app_user.id = (select auth.uid())
                  AND app_user.role = 'coordinator'
                  AND app_user.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_deny_client_insert
        ON tickets FOR INSERT TO authenticated
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_deny_client_update
        ON tickets FOR UPDATE TO authenticated
        USING (false)
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_tickets_deny_client_delete
        ON tickets FOR DELETE TO authenticated
        USING (false)
        """
    )

    op.execute(
        """
        CREATE POLICY rls_ticket_attachments_select_authorized_parent
        ON ticket_attachments FOR SELECT TO authenticated
        USING (EXISTS (SELECT 1 FROM tickets WHERE tickets.id = ticket_attachments.ticket_id))
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_attachments_deny_client_mutation
        ON ticket_attachments FOR ALL TO authenticated USING (false) WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_status_history_select_authorized_parent
        ON ticket_status_history FOR SELECT TO authenticated
        USING (EXISTS (SELECT 1 FROM tickets WHERE tickets.id = ticket_status_history.ticket_id))
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_status_history_deny_client_mutation
        ON ticket_status_history FOR ALL TO authenticated USING (false) WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_assignments_technician_select_own
        ON ticket_assignments FOR SELECT TO authenticated
        USING (technician_id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_assignments_coordinator_select_all_mvp
        ON ticket_assignments FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM users app_user
                WHERE app_user.id = (select auth.uid())
                  AND app_user.role = 'coordinator'
                  AND app_user.is_active = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY rls_ticket_assignments_deny_client_mutation
        ON ticket_assignments FOR ALL TO authenticated USING (false) WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_notifications_select_own
        ON notifications FOR SELECT TO authenticated
        USING (recipient_user_id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY rls_notifications_deny_client_mutation
        ON notifications FOR ALL TO authenticated USING (false) WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY rls_technician_profiles_select_own
        ON technician_profiles FOR SELECT TO authenticated
        USING (user_id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY rls_technician_skills_select_own
        ON technician_skills FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM technician_profiles profile
                WHERE profile.user_id = technician_skills.technician_id
                  AND profile.user_id = (select auth.uid())
            )
        )
        """
    )
    op.execute("CREATE POLICY rls_ai_analysis_runs_deny_all ON ai_analysis_runs USING (false) WITH CHECK (false)")
    op.execute(
        "CREATE POLICY rls_ticket_scoring_results_deny_all ON ticket_scoring_results USING (false) WITH CHECK (false)"
    )
    op.execute("CREATE POLICY rls_audit_logs_deny_all_client_access ON audit_logs USING (false) WITH CHECK (false)")


def downgrade() -> None:
    """Downgrade schema."""
    for table_name, policy_name in reversed(NEW_POLICIES):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
    for policy_sql in OLD_POLICY_SQL:
        op.execute(policy_sql)
