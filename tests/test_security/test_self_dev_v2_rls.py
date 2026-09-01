from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_current_cutover_migration_keeps_backend_owned_mutations_and_unit_scoping():
    text = (ROOT / "alembic" / "versions" / "a7b8c9d0e1f2_align_self_dev_v2.py").read_text(encoding="utf-8")
    assert "rls_tickets_resident_or_coordinator_select" in text
    assert "resident_profiles" in text
    assert "source_unit_id" in text
    assert "rls_audit_logs_deny_client" in text
    assert "FORCE ROW LEVEL SECURITY" in text
