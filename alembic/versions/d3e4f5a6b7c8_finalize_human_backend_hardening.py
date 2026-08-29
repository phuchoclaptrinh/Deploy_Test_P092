"""finalize human backend hardening

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-11 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CANONICAL_BASE_SCORES = {
    "WATER_LEAK": 10,
    "ELECTRICAL_SHORT": 50,
    "ELEVATOR": 35,
    "SERIOUS_SECURITY_DISORDER": 40,
    "LOCK_DOOR": 25,
    "HVAC": 20,
    "LOCAL_POWER_OUTAGE": 25,
    "STRUCTURAL_ISSUE": 20,
    "COMMON_LIGHT": 10,
    "ODOR_HYGIENE": 10,
    "NOISE_NEIGHBOR": 10,
}


def upgrade() -> None:
    _backfill_category_base_scores()
    _enforce_category_scoring_config()
    _enforce_one_resident_per_unit()
    _harden_technician_ticket_rls()


def _backfill_category_base_scores() -> None:
    for code, score in CANONICAL_BASE_SCORES.items():
        op.execute(
            sa.text("UPDATE public.categories SET base_score = :score WHERE code = :code AND base_score IS NULL")
            .bindparams(code=code, score=score)
        )


def _enforce_category_scoring_config() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            missing_active_base_score_count integer;
        BEGIN
            SELECT count(*)
            INTO missing_active_base_score_count
            FROM public.categories
            WHERE is_active = true
              AND base_score IS NULL;

            IF missing_active_base_score_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot enforce Category scoring: active categories without base_score=%',
                    missing_active_base_score_count;
            END IF;

            ALTER TABLE public.categories
                ADD CONSTRAINT ck_categories_active_base_score_required
                CHECK (is_active = false OR base_score IS NOT NULL);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def _enforce_one_resident_per_unit() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            duplicate_unit_count integer;
        BEGIN
            SELECT count(*)
            INTO duplicate_unit_count
            FROM (
                SELECT unit_id
                FROM public.resident_profiles
                GROUP BY unit_id
                HAVING count(*) > 1
            ) duplicates;

            IF duplicate_unit_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot enforce one Resident per Unit: duplicate_bound_units=%',
                    duplicate_unit_count;
            END IF;
        END $$;
        """
    )
    op.create_index(
        "uq_resident_profiles_unit_id",
        "resident_profiles",
        ["unit_id"],
        unique=True,
        if_not_exists=True,
    )


def _harden_technician_ticket_rls() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS rls_tickets_technician_select_assigned ON public.tickets;
        CREATE POLICY rls_tickets_technician_select_assigned ON public.tickets
        FOR SELECT TO authenticated USING (
            EXISTS (
                SELECT 1
                FROM public.ticket_assignments assignment
                WHERE assignment.ticket_id = tickets.id
                  AND assignment.technician_id = (SELECT auth.uid())
            )
        );

        DROP POLICY IF EXISTS rls_ticket_attachments_technician_assigned_read ON public.ticket_attachments;
        CREATE POLICY rls_ticket_attachments_technician_assigned_read ON public.ticket_attachments
        FOR SELECT TO authenticated USING (
            attachment_type IN ('ISSUE_ORIGINAL', 'RESIDENT_SUPPLEMENT', 'TECHNICIAN_COMPLETION')
            AND EXISTS (
                SELECT 1
                FROM public.ticket_assignments assignment
                WHERE assignment.ticket_id = ticket_attachments.ticket_id
                  AND assignment.technician_id = (SELECT auth.uid())
            )
        );
        """
    )


def downgrade() -> None:
    raise RuntimeError("d3e4f5a6b7c8 is a forward-only human backend hardening migration.")
