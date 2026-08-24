"""RLS must say the same thing the API says.

The API is authoritative, but the database policies are the second line: if a
client ever reaches Supabase directly with a resident or coordinator JWT, the
private AI phase has to hold there too. `8b9c0d1e2f3a` is the forward
correction that replaces the policies which only checked the apartment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "alembic" / "versions"
MIGRATION = VERSIONS / "8b9c0d1e2f3a_align_rls_with_private_ai_phase.py"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _rendered_upgrade_sql() -> str:
    """The SQL the migration actually issues, with the shared predicate expanded."""
    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._UPGRADE


def test_revision_follows_the_appeal_cleanup():
    text = _text()
    assert 'revision: str = "8b9c0d1e2f3a"' in text
    assert 'down_revision: str | Sequence[str] | None = "7a8b9c0d1e2f"' in text


def test_the_private_phase_is_spelled_the_same_way_as_in_the_service_layer():
    from src.services.ticket_visibility import PRIVATE_AI_PHASE

    assert {status.value for status in PRIVATE_AI_PHASE} == {"PENDING", "PROCESSING"}
    assert "classification_status NOT IN ('PENDING', 'PROCESSING')" in _rendered_upgrade_sql()


def test_the_apartment_wide_ticket_policy_is_replaced():
    upgrade = _rendered_upgrade_sql()
    assert "DROP POLICY IF EXISTS rls_tickets_resident_or_coordinator_select ON tickets" in upgrade
    assert "CREATE POLICY rls_tickets_visible_phase_select ON tickets" in upgrade
    # The reporter clause is what lets them keep reading their own private row.
    assert "t.reporter_user_id = (SELECT auth.uid())" in upgrade


def test_coordinators_only_reach_published_tickets():
    upgrade = _rendered_upgrade_sql()
    coordinator_clauses = upgrade.count("up.role = 'COORDINATOR'")
    # tickets, attachments, status history — each gated behind the published
    # predicate, never on its own.
    assert coordinator_clauses == 3
    assert upgrade.count("classification_status NOT IN ('PENDING', 'PROCESSING')") == 3


def test_attachments_and_history_inherit_the_authorized_parent():
    upgrade = _rendered_upgrade_sql()
    # The old policy accepted any attachment whose parent ticket merely existed.
    assert "EXISTS (SELECT 1 FROM tickets t WHERE t.id=ticket_attachments.ticket_id)" not in upgrade
    assert "CREATE POLICY rls_ticket_attachments_visible_ticket ON ticket_attachments" in upgrade
    assert "CREATE POLICY rls_ticket_status_history_visible_ticket ON ticket_status_history" in upgrade


def test_agent_questions_stay_closed_to_direct_client_access():
    upgrade = _rendered_upgrade_sql()
    assert "CREATE POLICY rls_ai_agent_questions_deny_client ON ai_agent_questions" in upgrade
    assert "FOR ALL TO authenticated USING (false) WITH CHECK (false)" in upgrade


def test_the_older_policy_migrations_are_not_edited():
    """The correction is forward-only; the cutover migrations stay as written."""
    cutover = (VERSIONS / "a7b8c9d0e1f2_align_self_dev_v2.py").read_text(encoding="utf-8")
    assert "rls_tickets_resident_or_coordinator_select" in cutover
    hardening = (VERSIONS / "d3e4f5a6b7c8_finalize_human_backend_hardening.py").read_text(encoding="utf-8")
    assert "rls_tickets_technician_select_assigned" in hardening
