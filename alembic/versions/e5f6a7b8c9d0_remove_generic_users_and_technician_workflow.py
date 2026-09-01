"""remove generic users and technician workflow

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-06 10:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cut over to the final Resident/BQL schema after safe validation."""
    op.execute(
        """
        DO $$
        DECLARE
            resident_count integer;
            copied_resident_count integer;
            coordinator_count integer;
            copied_bql_count integer;
            membership_count integer;
            copied_membership_count integer;
            invalid_membership_count integer;
            invalid_ticket_count integer;
            invalid_upload_session_count integer;
            profile_conflict_count integer;
            technician_profile_count integer;
            technician_skill_count integer;
            ticket_assignment_count integer;
        BEGIN
            SELECT count(*)
            INTO resident_count
            FROM public.users
            WHERE role = 'resident';

            SELECT count(*)
            INTO copied_resident_count
            FROM public.residents resident
            JOIN public.users app_user
              ON app_user.id = resident.id
             AND app_user.role = 'resident';

            SELECT count(*)
            INTO coordinator_count
            FROM public.users
            WHERE role = 'coordinator';

            SELECT count(*)
            INTO copied_bql_count
            FROM public.bql_staff staff
            JOIN public.users app_user
              ON app_user.id = staff.id
             AND app_user.role = 'coordinator';

            SELECT count(*)
            INTO membership_count
            FROM public.user_unit_memberships;

            SELECT count(*)
            INTO copied_membership_count
            FROM public.resident_unit_memberships resident_membership
            JOIN public.user_unit_memberships old_membership
              ON old_membership.id = resident_membership.id
             AND old_membership.user_id = resident_membership.resident_id
             AND old_membership.unit_id = resident_membership.unit_id;

            SELECT count(*)
            INTO invalid_membership_count
            FROM public.resident_unit_memberships membership
            LEFT JOIN public.residents resident
              ON resident.id = membership.resident_id
            WHERE resident.id IS NULL;

            SELECT count(*)
            INTO invalid_ticket_count
            FROM public.tickets ticket
            LEFT JOIN public.residents resident
              ON resident.id = ticket.resident_id
            WHERE resident.id IS NULL;

            SELECT count(*)
            INTO invalid_upload_session_count
            FROM public.ticket_attachment_upload_sessions upload_session
            LEFT JOIN public.residents resident
              ON resident.id = upload_session.resident_id
            WHERE resident.id IS NULL;

            SELECT count(*)
            INTO profile_conflict_count
            FROM public.residents resident
            JOIN public.bql_staff staff
              ON staff.id = resident.id;

            SELECT count(*)
            INTO technician_profile_count
            FROM public.technician_profiles;

            SELECT count(*)
            INTO technician_skill_count
            FROM public.technician_skills;

            SELECT count(*)
            INTO ticket_assignment_count
            FROM public.ticket_assignments;

            IF resident_count <> copied_resident_count
               OR coordinator_count <> copied_bql_count
               OR membership_count <> copied_membership_count
               OR invalid_membership_count > 0
               OR invalid_ticket_count > 0
               OR invalid_upload_session_count > 0
               OR profile_conflict_count > 0
               OR technician_profile_count > 0
               OR technician_skill_count > 0
               OR ticket_assignment_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot cut over actor schema: resident_count=%, copied_resident_count=%, coordinator_count=%, copied_bql_count=%, membership_count=%, copied_membership_count=%, invalid_membership_count=%, invalid_ticket_count=%, invalid_upload_session_count=%, profile_conflict_count=%, technician_profile_count=%, technician_skill_count=%, ticket_assignment_count=%',
                    resident_count,
                    copied_resident_count,
                    coordinator_count,
                    copied_bql_count,
                    membership_count,
                    copied_membership_count,
                    invalid_membership_count,
                    invalid_ticket_count,
                    invalid_upload_session_count,
                    profile_conflict_count,
                    technician_profile_count,
                    technician_skill_count,
                    ticket_assignment_count;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_residents_id_auth_users'
                  AND conrelid = 'public.residents'::regclass
            ) THEN
                ALTER TABLE public.residents
                    VALIDATE CONSTRAINT fk_residents_id_auth_users;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_bql_staff_id_auth_users'
                  AND conrelid = 'public.bql_staff'::regclass
            ) THEN
                ALTER TABLE public.bql_staff
                    VALIDATE CONSTRAINT fk_bql_staff_id_auth_users;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        DECLARE
            policy_record record;
            fk_record record;
        BEGIN
            FOR policy_record IN
                SELECT schemaname, tablename, policyname
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'users',
                    'user_unit_memberships',
                    'technician_profiles',
                    'technician_skills',
                    'ticket_assignments',
                    'residents',
                    'bql_staff',
                    'resident_unit_memberships',
                    'units',
                    'tickets',
                    'ticket_attachments',
                    'ticket_attachment_upload_sessions',
                    'ticket_status_history',
                    'notifications',
                    'audit_logs',
                    'ai_analysis_runs',
                    'ticket_scoring_results'
                  )
            LOOP
                EXECUTE format(
                    'DROP POLICY IF EXISTS %I ON %I.%I',
                    policy_record.policyname,
                    policy_record.schemaname,
                    policy_record.tablename
                );
            END LOOP;

            DROP VIEW IF EXISTS public.technician_ticket_view;
            DROP VIEW IF EXISTS public.resident_ticket_view;

            FOR fk_record IN
                SELECT
                    conname,
                    conrelid::regclass::text AS table_name
                FROM pg_constraint
                WHERE contype = 'f'
                  AND confrelid IN (
                    'public.users'::regclass,
                    'public.technician_profiles'::regclass
                  )
            LOOP
                EXECUTE format(
                    'ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I',
                    fk_record.table_name,
                    fk_record.conname
                );
            END LOOP;
        END $$;
        """
    )

    op.drop_index(
        "ix_ticket_attachment_upload_sessions_owner_status",
        table_name="ticket_attachment_upload_sessions",
    )
    op.drop_index(
        "ix_notifications_recipient_unread_created_at",
        table_name="notifications",
    )
    op.drop_index(
        "ix_audit_logs_actor_created_at",
        table_name="audit_logs",
    )

    op.drop_column("ticket_attachment_upload_sessions", "owner_user_id")
    op.drop_column("ticket_status_history", "changed_by_user_id")
    op.drop_column("notifications", "recipient_user_id")
    op.drop_column("audit_logs", "actor_user_id")

    op.execute("DROP TABLE IF EXISTS public.ticket_assignments")
    op.execute("DROP TABLE IF EXISTS public.technician_skills")
    op.execute("DROP TABLE IF EXISTS public.technician_profiles")
    op.drop_table("user_unit_memberships")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS role_enum")

    op.create_index(
        "ix_notifications_recipient_auth_unread_created_at",
        "notifications",
        ["recipient_auth_user_id", "is_read", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_actor_auth_created_at",
        "audit_logs",
        ["actor_auth_user_id", "created_at"],
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth'
                  AND table_name = 'users'
            ) THEN
                ALTER TABLE public.ticket_status_history
                    ADD CONSTRAINT fk_ticket_status_history_changed_by_auth_users
                    FOREIGN KEY (changed_by_auth_user_id)
                    REFERENCES auth.users(id)
                    ON DELETE SET NULL;

                ALTER TABLE public.notifications
                    ADD CONSTRAINT fk_notifications_recipient_auth_users
                    FOREIGN KEY (recipient_auth_user_id)
                    REFERENCES auth.users(id)
                    ON DELETE RESTRICT;

                ALTER TABLE public.audit_logs
                    ADD CONSTRAINT fk_audit_logs_actor_auth_users
                    FOREIGN KEY (actor_auth_user_id)
                    REFERENCES auth.users(id)
                    ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE VIEW resident_ticket_view
        WITH (security_invoker = true)
        AS
        SELECT
            tickets.id,
            tickets.resident_id,
            tickets.unit_id,
            tickets.title,
            tickets.description,
            tickets.status,
            tickets.category,
            tickets.severity,
            tickets.priority,
            tickets.location_description,
            tickets.created_at,
            tickets.updated_at,
            tickets.resolved_at
        FROM tickets;

        CREATE POLICY rls_residents_select_own_profile
        ON residents
        FOR SELECT
        TO authenticated
        USING (id = (SELECT auth.uid()));

        CREATE POLICY rls_bql_staff_select_own_profile
        ON bql_staff
        FOR SELECT
        TO authenticated
        USING (id = (SELECT auth.uid()) AND is_active = true);

        CREATE POLICY rls_resident_unit_memberships_select_own_active
        ON resident_unit_memberships
        FOR SELECT
        TO authenticated
        USING (
            resident_id = (SELECT auth.uid())
            AND is_active = true
        );

        CREATE POLICY rls_units_select_resident_active_membership
        ON units
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM resident_unit_memberships membership
                WHERE membership.unit_id = units.id
                  AND membership.resident_id = (SELECT auth.uid())
                  AND membership.is_active = true
            )
        );

        CREATE POLICY rls_tickets_resident_select_owned
        ON tickets
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM resident_unit_memberships membership
                WHERE membership.unit_id = tickets.unit_id
                  AND membership.resident_id = (SELECT auth.uid())
                  AND membership.is_active = true
            )
        );

        CREATE POLICY rls_tickets_bql_select_all_mvp
        ON tickets
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM bql_staff
                WHERE bql_staff.id = (SELECT auth.uid())
                  AND bql_staff.is_active = true
            )
        );

        CREATE POLICY rls_ticket_attachments_select_authorized_parent
        ON ticket_attachments
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM tickets
                WHERE tickets.id = ticket_attachments.ticket_id
            )
        );

        CREATE POLICY rls_ticket_status_history_select_authorized_parent
        ON ticket_status_history
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM tickets
                WHERE tickets.id = ticket_status_history.ticket_id
            )
        );

        CREATE POLICY rls_notifications_recipient_auth_select_owned
        ON notifications
        FOR SELECT
        TO authenticated
        USING (recipient_auth_user_id = (SELECT auth.uid()));

        CREATE POLICY rls_ticket_attachment_upload_sessions_deny_all_client_access
        ON ticket_attachment_upload_sessions
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);

        CREATE POLICY rls_ai_analysis_runs_deny_all_client_access
        ON ai_analysis_runs
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);

        CREATE POLICY rls_ticket_scoring_results_deny_all_client_access
        ON ticket_scoring_results
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);

        CREATE POLICY rls_audit_logs_deny_all_client_access
        ON audit_logs
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
        """
    )

    for table_name in (
        "residents",
        "bql_staff",
        "resident_unit_memberships",
        "units",
        "tickets",
        "ticket_attachments",
        "ticket_attachment_upload_sessions",
        "ticket_status_history",
        "notifications",
        "audit_logs",
        "ai_analysis_runs",
        "ticket_scoring_results",
    ):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")


def downgrade() -> None:
    """The final actor cutover is intentionally irreversible."""
    raise NotImplementedError(
        "Downgrade is not supported for the Resident/BQL actor cutover."
    )
