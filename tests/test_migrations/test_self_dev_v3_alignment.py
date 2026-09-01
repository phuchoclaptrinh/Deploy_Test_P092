import importlib.util
from pathlib import Path

import src.database.models  # noqa: F401
from src.database.base import Base

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "d3e4f5a6b7c8_finalize_human_backend_hardening.py"
AGENT_MIGRATION = ROOT / "alembic" / "versions" / "e4f5a6b7c8d9_add_agent_v3_backend_contract.py"
AGENT_RLS_MIGRATION = ROOT / "alembic" / "versions" / "f5a6b7c8d9e0_add_agent_internal_table_rls.py"
VERSIONS = ROOT / "alembic" / "versions"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_final_hardening_migration_is_current_forward_revision():
    module = _load(MIGRATION)

    assert module.revision == "d3e4f5a6b7c8"
    assert module.down_revision == "c2d3e4f5a6b7"


def test_agent_v3_migration_extends_current_forward_revision():
    module = _load(AGENT_MIGRATION)

    assert module.revision == "e4f5a6b7c8d9"
    assert module.down_revision == "d3e4f5a6b7c8"


def test_agent_internal_rls_migration_extends_agent_contract_revision():
    module = _load(AGENT_RLS_MIGRATION)

    assert module.revision == "f5a6b7c8d9e0"
    assert module.down_revision == "e4f5a6b7c8d9"


def test_local_migration_graph_has_one_head():
    revisions = {}
    down_revisions = set()
    for path in VERSIONS.glob("*.py"):
        module = _load(path)
        revisions[module.revision] = path.name
        down = module.down_revision
        if isinstance(down, str):
            down_revisions.add(down)
        elif down:
            down_revisions.update(down)

    heads = set(revisions) - down_revisions
    # Pinning a specific revision here means every new migration breaks this
    # test for the wrong reason. What must hold is that the graph stays linear.
    assert len(heads) == 1, f"Expected exactly one Alembic head, found {sorted(heads)}"


def test_current_metadata_has_v3_tables_columns_and_active_assignment_constraint():
    tables = Base.metadata.tables

    assert tables["categories"].c.code.type.length == 80
    assert {"technician_profiles", "technician_skills", "ticket_assignments"} <= set(tables)
    assert {"completion_note", "completed_at", "assigned_by_user_id"} <= set(tables["ticket_assignments"].c.keys())
    assert {"technician_id", "category_id"} <= set(tables["technician_skills"].c.keys())
    # `base_score` was here. It is gone with the v1 scoring model: a category
    # is a routing label now and contributes nothing to a priority.
    assert "base_score" not in tables["categories"].c
    assert {"ai_analysis_sessions", "ai_agent_tool_calls", "ai_agent_questions"} <= set(tables)
    assert {"contract_version", "analysis_session_id", "exit_reason", "tool_usage"} <= set(tables["ai_analysis_runs"].c.keys())

    indexes = tables["ticket_assignments"].indexes
    active_unique = [index for index in indexes if index.name == "uq_ticket_assignments_one_active_per_ticket"]
    assert active_unique and active_unique[0].unique
    assert active_unique[0].dialect_options["postgresql"]["where"] is not None
    unit_unique = [index for index in tables["resident_profiles"].indexes if index.name == "uq_resident_profiles_unit_id"]
    assert not unit_unique


def test_final_hardening_migration_adds_unit_uniqueness_and_technician_ticket_rls():
    text = MIGRATION.read_text(encoding="utf-8")

    assert "uq_resident_profiles_unit_id" in text
    # This revision added the constraint; `a1b2c3d4e5f7` drops it along with
    # the column it guarded. Still asserted here because the assertion is
    # about what *this* migration wrote, not about the current schema.
    assert "ck_categories_active_base_score_required" in text
    assert "rls_tickets_technician_select_assigned" in text
    assert "assignment.technician_id = (SELECT auth.uid())" in text


def test_agent_v3_migration_adds_contract_tables_and_invalid_status():
    text = AGENT_MIGRATION.read_text(encoding="utf-8")

    assert "ALTER TYPE ticket_status_v2_enum ADD VALUE IF NOT EXISTS 'INVALID'" in text
    assert "ai_analysis_sessions" in text
    assert "ai_agent_tool_calls" in text
    assert "ai_agent_questions" in text
    assert "contract_version" in text


def test_agent_internal_rls_migration_protects_internal_tables():
    text = AGENT_RLS_MIGRATION.read_text(encoding="utf-8")

    for table in ("ai_analysis_sessions", "ai_agent_tool_calls", "ai_agent_questions"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in text
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in text
        assert f"REVOKE ALL ON TABLE {table} FROM PUBLIC" in text
