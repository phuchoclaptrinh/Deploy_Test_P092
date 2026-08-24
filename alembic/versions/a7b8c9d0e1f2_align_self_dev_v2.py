"""align runtime schema with canonical Self_Dev_Docs v2/v3

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-07 10:30:00.000000

This revision intentionally follows (and never edits) the already-created f6
Technician revision. Self_Dev_Docs v2/v3 is now the product source of truth and
removes the Technician actor entirely.

Safety policy: legacy ticket/workflow tables must be empty before the structural
cutover. Resident/Coordinator identities and unit catalog rows are migrated. A
non-empty operational dataset requires an explicit one-off data migration rather
than a silent destructive conversion.
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


user_role_enum = postgresql.ENUM("RESIDENT", "COORDINATOR", name="user_role_enum", create_type=False)
ticket_status_v2_enum = postgresql.ENUM(
    "NEW", "WAITING_RESIDENT_INFO", "APPROVED", "IN_PROGRESS", "COMPLETED", "UNRESOLVABLE", "CANCELLED",
    name="ticket_status_v2_enum", create_type=False,
)
classification_status_enum = postgresql.ENUM(
    "PENDING", "PROCESSING", "RESOLVED", "MANUAL_REVIEW", "FAILED",
    name="classification_status_enum", create_type=False,
)
attachment_type_enum = postgresql.ENUM(
    "ISSUE_ORIGINAL", "RESIDENT_SUPPLEMENT", name="attachment_type_enum", create_type=False
)
image_quality_status_enum = postgresql.ENUM(
    "PENDING", "READABLE", "UNREADABLE", name="image_quality_status_enum", create_type=False
)
analysis_run_status_enum = postgresql.ENUM(
    "RUNNING", "SUCCEEDED", "FAILED", name="analysis_run_status_enum", create_type=False
)
severity_source_enum = postgresql.ENUM(
    "VISION", "TEXT_FALLBACK", name="severity_source_enum", create_type=False
)
severity_v2_enum = postgresql.ENUM("LOW", "MEDIUM", "HIGH", name="severity_v2_enum", create_type=False)
priority_level_enum = postgresql.ENUM("P1", "P2", "P3", name="priority_level_enum", create_type=False)
information_request_status_enum = postgresql.ENUM(
    "OPEN", "RESPONDED", "CLOSED", name="information_request_status_enum", create_type=False
)
notification_channel_enum = postgresql.ENUM(
    "PUSH", "SMS", "IN_APP", name="notification_channel_enum", create_type=False
)
notification_status_enum = postgresql.ENUM(
    "PENDING", "SENT", "FAILED", "READ", name="notification_status_enum", create_type=False
)

SCORING_RULE_VERSION = "self-dev-v2.0.0"

SCORING_CONFIG = {
    "category_base": {
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
    },
    "location_bonus": {
        "LOCK_DOOR": {
            "MAIN_DOOR": 30,
            "SECURITY_DOOR": 30,
        },
        "COMMON_LIGHT": {
            "FIRE_EXIT": 25,
        },
    },
    "density": {
        "1": 0,
        "2-3": 15,
        "4+": 30,
        "categories": [
            "WATER_LEAK",
            "ELECTRICAL_SHORT",
        ],
    },
    "severity": {
        "LOW": 0,
        "MEDIUM": 10,
        "HIGH": 20,
    },
    "thresholds": {
        "P1": "<30",
        "P2": "30-59",
        "P3": ">=60",
    },
    "sla_minutes": {
        "P3": 5,
        "P2": 180,
        "P1": 4320,
    },
}

LEGACY_OPERATIONAL_TABLES = (
    "tickets",
    "ticket_attachments",
    "ticket_attachment_upload_sessions",
    "ai_analysis_runs",
    "ticket_scoring_results",
    "ticket_status_history",
    "notifications",
    "audit_logs",
    "ticket_assignments",
    "technician_skills",
    "technician_profiles",
)


def upgrade() -> None:
    _preflight_empty_legacy_runtime()
    _drop_legacy_policies_and_views()
    _create_v2_types()
    _create_identity_tables_and_migrate_profiles()
    _align_unit_catalog()
    _create_resident_bindings_from_legacy_memberships()
    _drop_legacy_runtime_tables()
    _drop_legacy_actor_tables_and_types()
    _create_v2_catalog_and_workflow_tables()
    _seed_v2_catalogs()
    _create_v2_indexes()
    _create_v2_rls()


def _preflight_empty_legacy_runtime() -> None:
    checks = " UNION ALL ".join(
        f"SELECT '{table}' AS table_name, count(*)::bigint AS row_count FROM public.{table}"
        for table in LEGACY_OPERATIONAL_TABLES
    )
    op.execute(
        f"""
        DO $$
        DECLARE
            nonempty_count integer;
            multi_binding_count integer;
        BEGIN
            SELECT count(*) INTO nonempty_count
            FROM ({checks}) counts
            WHERE row_count > 0;

            SELECT count(*) INTO multi_binding_count
            FROM (
                SELECT resident_id
                FROM public.resident_unit_memberships
                WHERE is_active = true
                GROUP BY resident_id
                HAVING count(*) > 1
            ) conflicts;

            IF nonempty_count > 0 OR multi_binding_count > 0 THEN
                RAISE EXCEPTION
                    'SELF_DEV_V2_CUTOVER_REQUIRES_MANUAL_DATA_MIGRATION: nonempty_operational_tables=%, multi_unit_residents=%',
                    nonempty_count, multi_binding_count;
            END IF;
        END $$;
        """
    )


def _drop_legacy_policies_and_views() -> None:
    op.execute(
        """
        DO $$
        DECLARE p record;
        BEGIN
            FOR p IN
                SELECT schemaname, tablename, policyname
                FROM pg_policies
                WHERE schemaname='public'
            LOOP
                EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', p.policyname, p.schemaname, p.tablename);
            END LOOP;
        END $$;
        DROP VIEW IF EXISTS public.technician_ticket_view;
        DROP VIEW IF EXISTS public.resident_ticket_view;
        """
    )


def _create_v2_types() -> None:
    bind = op.get_bind()
    for enum in (
        user_role_enum,
        ticket_status_v2_enum,
        classification_status_enum,
        severity_v2_enum,
        attachment_type_enum,
        image_quality_status_enum,
        analysis_run_status_enum,
        severity_source_enum,
        priority_level_enum,
        information_request_status_enum,
        notification_channel_enum,
        notification_status_enum,
    ):
        enum.create(bind, checkfirst=True)


def _create_identity_tables_and_migrate_profiles() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("full_name", sa.String(150), nullable=True),
        sa.Column("role", user_role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            r"phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9][0-9]{6,14}$'",
            name="ck_user_profiles_phone_e164",
        ),
    )
    op.create_index("ix_user_profiles_phone_e164", "user_profiles", ["phone_e164"], unique=True)
    op.create_index("ix_user_profiles_role_active", "user_profiles", ["role", "is_active"])

    op.execute(
        """
        INSERT INTO public.user_profiles (user_id, phone_e164, full_name, role, is_active, created_at, updated_at)
        SELECT id, phone_number, full_name, 'RESIDENT'::user_role_enum, is_active, created_at, updated_at
        FROM public.residents;

        INSERT INTO public.user_profiles (user_id, phone_e164, full_name, role, is_active, created_at, updated_at)
        SELECT id, NULL, full_name, 'COORDINATOR'::user_role_enum, is_active, created_at, updated_at
        FROM public.bql_staff;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='auth' AND table_name='users'
            ) THEN
                ALTER TABLE public.user_profiles
                ADD CONSTRAINT fk_user_profiles_auth_users
                FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )


def _align_unit_catalog() -> None:
    op.create_table(
        "buildings",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_table(
        "floors",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("building_id", sa.Uuid(), sa.ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("floor_code", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("adjacency_index", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("building_id", "floor_code", name="uq_floors_building_code"),
    )

    op.execute(
        """
        INSERT INTO buildings(code, name, is_active)
        SELECT DISTINCT building_code, building_code, true FROM units;

        INSERT INTO floors(building_id, floor_code, display_name, adjacency_index, is_active)
        SELECT b.id, u.floor, u.floor,
               dense_rank() OVER (
                   PARTITION BY b.id
                   ORDER BY
                       CASE
                           WHEN upper(u.floor) ~ '^B[0-9]+$' THEN 0
                           WHEN u.floor ~ '^[0-9]+$' THEN 1
                           ELSE 2
                       END,
                       CASE
                           WHEN upper(u.floor) ~ '^B[0-9]+$' THEN -substring(upper(u.floor) from 2)::integer
                           WHEN u.floor ~ '^[0-9]+$' THEN u.floor::integer
                           ELSE NULL
                       END,
                       u.floor
               )::integer,
               true
        FROM (SELECT DISTINCT building_code, floor FROM units) u
        JOIN buildings b ON b.code=u.building_code;
        """
    )

    op.add_column("units", sa.Column("building_id_v2", sa.Uuid(), nullable=True))
    op.add_column("units", sa.Column("floor_id_v2", sa.Uuid(), nullable=True))
    op.add_column("units", sa.Column("unit_code", sa.String(80), nullable=True))
    op.add_column("units", sa.Column("status", sa.String(30), nullable=True))
    op.execute(
        """
        UPDATE units u
        SET building_id_v2=b.id,
            floor_id_v2=f.id,
            unit_code=u.building_code || '-' || u.unit_number,
            status=CASE WHEN u.is_active THEN 'ACTIVE' ELSE 'INACTIVE' END
        FROM buildings b, floors f
        WHERE b.code=u.building_code
          AND f.building_id=b.id
          AND f.floor_code=u.floor;
        """
    )
    op.alter_column("units", "building_id_v2", nullable=False)
    op.alter_column("units", "floor_id_v2", nullable=False)
    op.alter_column("units", "unit_code", nullable=False)
    op.alter_column("units", "status", nullable=False, server_default=sa.text("'ACTIVE'"))
    op.create_foreign_key("fk_units_building_v2", "units", "buildings", ["building_id_v2"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_units_floor_v2", "units", "floors", ["floor_id_v2"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_units_building_code", "units", ["building_id_v2", "unit_code"])

    # Legacy fields are no longer part of the canonical contract.
    op.drop_column("units", "building_code")
    op.drop_column("units", "floor")
    op.drop_column("units", "unit_number")
    op.drop_column("units", "is_active")
    op.alter_column("units", "building_id_v2", new_column_name="building_id")
    op.alter_column("units", "floor_id_v2", new_column_name="floor_id")


def _create_resident_bindings_from_legacy_memberships() -> None:
    op.create_table(
        "resident_profiles",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("units.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_resident_profiles_unit", "resident_profiles", ["unit_id"])
    op.create_index(
        "uq_resident_profiles_one_primary_per_unit",
        "resident_profiles",
        ["unit_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )
    op.execute(
        """
        INSERT INTO resident_profiles(user_id, unit_id, is_primary, verified_at)
        SELECT m.resident_id,
               m.unit_id,
               row_number() OVER (PARTITION BY m.unit_id ORDER BY m.linked_at, m.resident_id) = 1,
               m.linked_at
        FROM resident_unit_memberships m
        WHERE m.is_active=true;
        """
    )


def _drop_legacy_runtime_tables() -> None:
    # Empty by preflight. Order respects FKs.
    for table in (
        "ticket_assignments",
        "technician_skills",
        "ticket_scoring_results",
        "ai_analysis_runs",
        "ticket_attachments",
        "ticket_attachment_upload_sessions",
        "ticket_status_history",
        "notifications",
        "audit_logs",
        "tickets",
        "technician_profiles",
    ):
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")


def _drop_legacy_actor_tables_and_types() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_residents_prevent_actor_profile_conflict ON public.residents")
    op.execute("DROP TRIGGER IF EXISTS trg_bql_staff_prevent_actor_profile_conflict ON public.bql_staff")
    op.execute("DROP FUNCTION IF EXISTS public.prevent_actor_profile_conflict()")
    op.execute("DROP TABLE IF EXISTS public.resident_unit_memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS public.residents CASCADE")
    op.execute("DROP TABLE IF EXISTS public.bql_staff CASCADE")
    for type_name in ("assignment_status_enum", "ticket_status_enum", "category_enum", "severity_enum", "priority_enum"):
        op.execute(f"DROP TYPE IF EXISTS {type_name}")


def _create_v2_catalog_and_workflow_tables() -> None:
    op.create_table(
        "location_types",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("building_id", sa.Uuid(), sa.ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("floor_id", sa.Uuid(), sa.ForeignKey("floors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_type_id", sa.Uuid(), sa.ForeignKey("location_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("units.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_locations_floor_type_active", "locations", ["floor_id", "location_type_id", "is_active"])

    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("priority_ceiling", priority_level_enum, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_table(
        "scoring_rule_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version", sa.String(50), nullable=False, unique=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_scoring_rule_versions_active", "scoring_rule_versions", ["is_active"])
    op.create_index(
        "uq_scoring_rule_versions_one_active",
        "scoring_rule_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("reporter_user_id", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_unit_id", sa.Uuid(), sa.ForeignKey("units.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("location_id", sa.Uuid(), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", ticket_status_v2_enum, nullable=False, server_default=sa.text("'NEW'")),
        sa.Column("classification_status", classification_status_enum, nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("priority", priority_level_enum, nullable=True),
        sa.Column("severity", severity_v2_enum, nullable=True),
        sa.Column("red_flag_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("score_total", sa.Numeric(7, 2), nullable=True),
        sa.Column("sla_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index("ix_tickets_reporter_user_id", "tickets", ["reporter_user_id"])
    op.create_index("ix_tickets_source_unit_id", "tickets", ["source_unit_id"])
    op.create_index("ix_tickets_location_id", "tickets", ["location_id"])

    op.create_table(
        "ticket_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_type", attachment_type_enum, nullable=False, server_default=sa.text("'ISSUE_ORIGINAL'")),
        sa.Column("storage_bucket", sa.String(100), nullable=False, server_default=sa.text("'ticket-attachments'")),
        sa.Column("object_path", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("image_quality_status", image_quality_status_enum, nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ticket_attachments_ticket_id", "ticket_attachments", ["ticket_id"])

    op.create_table(
        "ticket_attachment_upload_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("file_size > 0", name="ck_ticket_attachment_upload_sessions_file_size_positive"),
        sa.CheckConstraint("mime_type IN ('image/jpeg','image/png','image/webp')", name="ck_ticket_attachment_upload_sessions_mime_type"),
        sa.CheckConstraint("status IN ('pending','consumed','expired')", name="ck_ticket_attachment_upload_sessions_status"),
    )
    op.create_index("ix_ticket_attachment_upload_sessions_owner_status", "ticket_attachment_upload_sessions", ["owner_user_id", "status"])
    op.create_index("ix_ticket_attachment_upload_sessions_expires_at", "ticket_attachment_upload_sessions", ["expires_at"])

    op.create_table(
        "ai_analysis_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("text_model_version", sa.String(100), nullable=True),
        sa.Column("vision_model_version", sa.String(100), nullable=True),
        sa.Column("rule_version_id", sa.Uuid(), sa.ForeignKey("scoring_rule_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("text_categories", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("image_categories", postgresql.JSONB(), nullable=True),
        sa.Column("red_flag_text", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("red_flag_signal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("severity", severity_v2_enum, nullable=True),
        sa.Column("severity_source", severity_source_enum, nullable=True),
        sa.Column("category_match", sa.Boolean(), nullable=True),
        sa.Column("score_components", postgresql.JSONB(), nullable=True),
        sa.Column("score_total", sa.Numeric(7, 2), nullable=True),
        sa.Column("priority_raw", priority_level_enum, nullable=True),
        sa.Column("priority_final", priority_level_enum, nullable=True),
        sa.Column("ceiling_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", analysis_run_status_enum, nullable=False, server_default=sa.text("'RUNNING'")),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_analysis_runs_ticket_id", "ai_analysis_runs", ["ticket_id"])

    op.create_table(
        "ticket_status_history",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", ticket_status_v2_enum, nullable=True),
        sa.Column("to_status", ticket_status_v2_enum, nullable=False),
        sa.Column("changed_by", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ticket_status_history_ticket_created_at", "ticket_status_history", ["ticket_id", "created_at"])

    op.create_table(
        "information_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_message", sa.Text(), nullable=False),
        sa.Column("status", information_request_status_enum, nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("resident_response_text", sa.Text(), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_information_requests_ticket_id", "information_requests", ["ticket_id"])

    op.create_table(
        "incident_cases",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("building_id", sa.Uuid(), sa.ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("density_value", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "incident_case_members",
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("incident_cases.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_unit_id", sa.Uuid(), sa.ForeignKey("units.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("recipient_user_id", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notification_type", sa.String(100), nullable=False),
        sa.Column("channel", notification_channel_enum, nullable=False, server_default=sa.text("'IN_APP'")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", notification_status_enum, nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_recipient_status_created_at", "notifications", ["recipient_user_id", "status", "created_at"])
    op.create_index("ix_notifications_ticket_id", "notifications", ["ticket_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_role", sa.String(50), nullable=False, server_default=sa.text("'SYSTEM'")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("before_data", postgresql.JSONB(), nullable=True),
        sa.Column("after_data", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id", "created_at"])
    op.create_index("ix_audit_logs_actor_created_at", "audit_logs", ["actor_user_id", "created_at"])


def _seed_v2_catalogs() -> None:
    op.execute(
        """
        INSERT INTO categories(code, display_name, priority_ceiling) VALUES
          ('WATER_LEAK','Rò rỉ nước',NULL),
          ('ELECTRICAL_SHORT','Chập điện',NULL),
          ('ELEVATOR','Thang máy',NULL),
          ('SERIOUS_SECURITY_DISORDER','An ninh / gây rối nghiêm trọng',NULL),
          ('LOCK_DOOR','Khóa / cửa','P2'),
          ('HVAC','Điều hòa / thông gió','P2'),
          ('LOCAL_POWER_OUTAGE','Mất điện cục bộ','P2'),
          ('STRUCTURAL_ISSUE','Sự cố kết cấu','P2'),
          ('COMMON_LIGHT','Đèn khu vực chung','P2'),
          ('ODOR_HYGIENE','Mùi / vệ sinh','P1'),
          ('NOISE_NEIGHBOR','Tiếng ồn hàng xóm','P1');

        INSERT INTO location_types(code, display_name) VALUES
          ('CORRIDOR','Hành lang'),
          ('FIRE_EXIT','Cầu thang / lối thoát hiểm'),
          ('BASEMENT_PARKING','Hầm / bãi đỗ xe'),
          ('INSIDE_UNIT','Bên trong căn hộ'),
          ('MAIN_DOOR','Cửa chính'),
          ('SECURITY_DOOR','Cửa an ninh');

        -- Floor-level canonical locations used by the Resident dropdown.
        INSERT INTO locations(building_id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT f.building_id, f.id, lt.id, NULL, lt.display_name || ' - ' || f.display_name, true
        FROM floors f
        CROSS JOIN location_types lt
        WHERE lt.code IN ('CORRIDOR','FIRE_EXIT');

        INSERT INTO locations(building_id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT f.building_id, f.id, lt.id, NULL, lt.display_name || ' - ' || f.display_name, true
        FROM floors f
        CROSS JOIN location_types lt
        WHERE lt.code='BASEMENT_PARKING'
          AND upper(f.floor_code) ~ '^B[0-9]+$';

        -- Unit-scoped locations. They cannot be selected for another unit because
        -- TicketService validates location.unit_id against the authenticated binding.
        INSERT INTO locations(building_id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT u.building_id, u.floor_id, lt.id, u.id, lt.display_name || ' ' || u.unit_code, true
        FROM units u
        CROSS JOIN location_types lt
        WHERE lt.code IN ('INSIDE_UNIT','MAIN_DOOR','SECURITY_DOOR');
        """
    )

    scoring_statement = sa.text(
        """
        INSERT INTO scoring_rule_versions(
            version,
            config,
            is_active
        )
        VALUES (
            :version,
            CAST(:config AS jsonb),
            true
        )
        """
    ).bindparams(
        sa.bindparam(
            "version",
            value=SCORING_RULE_VERSION,
            type_=sa.String(),
        ),
        sa.bindparam(
            "config",
            value=json.dumps(
                SCORING_CONFIG,
                ensure_ascii=False,
            ),
            type_=sa.Text(),
        ),
    )

    op.execute(scoring_statement)


def _create_v2_indexes() -> None:
    op.execute(
        """
        CREATE INDEX idx_tickets_dashboard
        ON tickets (priority DESC, created_at ASC)
        WHERE status NOT IN ('COMPLETED','CANCELLED');
        CREATE INDEX idx_tickets_resident_history ON tickets (source_unit_id, created_at DESC);
        CREATE INDEX idx_tickets_category_window ON tickets (category_id, created_at DESC);
        CREATE INDEX idx_tickets_location_window ON tickets (location_id, created_at DESC);
        """
    )


def _create_v2_rls() -> None:
    # Backend owns mutations. RLS is defense-in-depth for any direct Supabase access.
    tables = (
        "user_profiles", "resident_profiles", "buildings", "floors", "units", "location_types", "locations",
        "categories", "scoring_rule_versions", "tickets", "ticket_attachments", "ticket_attachment_upload_sessions",
        "ai_analysis_runs", "ticket_status_history", "information_requests", "incident_cases", "incident_case_members",
        "notifications", "audit_logs",
    )
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")

    op.execute(
        """
        CREATE POLICY rls_user_profiles_select_own ON user_profiles
        FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
        CREATE POLICY rls_resident_profiles_select_own ON resident_profiles
        FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));

        CREATE POLICY rls_units_catalog_select ON units FOR SELECT TO authenticated USING (true);
        CREATE POLICY rls_buildings_catalog_select ON buildings FOR SELECT TO authenticated USING (true);
        CREATE POLICY rls_floors_catalog_select ON floors FOR SELECT TO authenticated USING (true);
        CREATE POLICY rls_location_types_catalog_select ON location_types FOR SELECT TO authenticated USING (true);
        CREATE POLICY rls_locations_catalog_select ON locations FOR SELECT TO authenticated USING (is_active=true);
        CREATE POLICY rls_categories_catalog_select ON categories FOR SELECT TO authenticated USING (is_active=true);

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
        CREATE POLICY rls_ticket_attachments_visible_ticket ON ticket_attachments
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM tickets t WHERE t.id=ticket_attachments.ticket_id)
        );
        CREATE POLICY rls_ticket_status_history_visible_ticket ON ticket_status_history
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM tickets t WHERE t.id=ticket_status_history.ticket_id)
        );
        CREATE POLICY rls_information_requests_visible_ticket ON information_requests
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM tickets t WHERE t.id=information_requests.ticket_id)
        );
        CREATE POLICY rls_notifications_select_own ON notifications
        FOR SELECT TO authenticated USING (recipient_user_id=(SELECT auth.uid()));

        CREATE POLICY rls_upload_sessions_deny_client ON ticket_attachment_upload_sessions
        FOR ALL TO authenticated USING (false) WITH CHECK (false);
        CREATE POLICY rls_ai_runs_deny_client ON ai_analysis_runs
        FOR ALL TO authenticated USING (false) WITH CHECK (false);
        CREATE POLICY rls_scoring_rules_deny_client ON scoring_rule_versions
        FOR ALL TO authenticated USING (false) WITH CHECK (false);
        CREATE POLICY rls_incident_cases_deny_client ON incident_cases
        FOR ALL TO authenticated USING (false) WITH CHECK (false);
        CREATE POLICY rls_incident_members_deny_client ON incident_case_members
        FOR ALL TO authenticated USING (false) WITH CHECK (false);
        CREATE POLICY rls_audit_logs_deny_client ON audit_logs
        FOR ALL TO authenticated USING (false) WITH CHECK (false);
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "a7b8c9d0e1f2 is an intentional Self Dev v2 cutover. Use a forward corrective migration, not downgrade."
    )
