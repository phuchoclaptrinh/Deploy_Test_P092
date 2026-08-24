"""The §5.2 failover pair, enforced everywhere it has to be enforced.

Scoped to `ASSIGNMENT_DECISION_ENGINE=AI`, which is why every production
`Settings` built here selects it: on the default `RULE` engine there is no
model pair to fail over, and refusing to boot over two unread model names
would be its own outage. `test_the_rule_engine_needs_no_model_pair` pins that
boundary.

Three processes read the same two settings and must agree about them: the API,
the standalone worker, and the lazy engine construction inside the DIRECT and
PROPOSAL services. Disagreement is the failure worth testing for — a worker
that starts on a configuration the API rejects would quietly drain the queue
into the manual pile while the deployment looked half-healthy.

The other half of the file is about *how* a bad configuration fails. It must
never arrive as MANUAL_REQUIRED or as an EMPTY proposal row: those say "a model
considered this ticket and could not place it", which is a different fact from
"nobody configured a fallback", and a coordinator cannot tell them apart.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.assignment_agent.config import (
    AssignmentAgentSettings,
    AssignmentConfigurationError,
    enforce_failover,
)
from src.assignment_agent.model_client import build_model_clients
from src.assignment_agent.service import AssignmentAgentService
from src.config import Settings
from src.database.models.assignment_proposal import (
    AIAssignmentJob,
    AssignmentProposalBatch,
    AssignmentProposalItem,
)
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.user_profile import UserProfile
from src.models.enums import AssignmentJobStatus, ProposalBatchStatus, ProposalItemStatus, UserRole
from src.services.assignment_direct_service import DirectAssignmentService
from src.services.assignment_proposal_service import AssignmentProposalService
from src.services.assignment_trigger_service import AssignmentTriggerService
from src.workers import assignment_worker
from tests.test_v4.factories import approved_ticket, build_world
from tests.test_v4.scripted_assignment_model import ScriptedAssignmentModel

VALID = ("gpt-4.1-mini", "claude-sonnet-4-5")


def _pair(primary: str = "", fallback: str = "") -> AssignmentAgentSettings:
    """Settings built from arguments alone.

    `_env_file=None` matters: a developer .env carries real model names, and a
    test for "primary is missing" that silently reads one is not a test.
    """
    return AssignmentAgentSettings(
        _env_file=None,
        assignment_primary_model=primary,
        assignment_fallback_model=fallback,
    )


def _production(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "production",
        "database_url": "postgresql://disposable/test",
        "cors_origins": "http://localhost:3000",
        # The failover rule only exists for the AI engine; see the module
        # docstring.
        "assignment_decision_engine": "AI",
    }
    return Settings(**{**base, **overrides})


# ---------------------------------------------------------------------------
# The check itself: missing, identical, valid.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("primary", "fallback", "expected"),
    [
        ("", "", "ASSIGNMENT_PRIMARY_MODEL"),
        ("", VALID[1], "ASSIGNMENT_PRIMARY_MODEL"),
        (VALID[0], "", "ASSIGNMENT_FALLBACK_MODEL"),
        (VALID[0], VALID[0], "must differ"),
    ],
)
def test_strict_rejects_a_pair_that_cannot_fail_over(primary, fallback, expected):
    with pytest.raises(AssignmentConfigurationError) as excinfo:
        enforce_failover(strict=True, settings=_pair(primary, fallback))
    assert expected in str(excinfo.value)


def test_strict_accepts_two_different_models():
    settings = enforce_failover(strict=True, settings=_pair(*VALID))
    assert settings.failover_ready is True


def test_the_error_never_carries_a_key(monkeypatch):
    """The message reaches a startup log and a supervisor crash report.

    The sentinel is deliberately not shaped like a real key: a literal that
    looks like one would be a finding for `scripts/scan_secrets.py`, and a
    tracked file full of near-miss credentials is how a scanner gets muted.
    """
    sentinel = "provider-credential-that-must-never-be-logged"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    with pytest.raises(AssignmentConfigurationError) as excinfo:
        enforce_failover(strict=True, settings=_pair(VALID[0], VALID[0]))
    assert sentinel not in str(excinfo.value)
    assert "ASSIGNMENT_" in str(excinfo.value)


def test_non_strict_warns_and_carries_on(caplog):
    """A developer running the API to look at tickets needs no model pair."""
    with caplog.at_level("WARNING"):
        settings = enforce_failover(strict=False, settings=_pair(VALID[0], VALID[0]))
    assert settings.failover_ready is False
    assert "failover is not configured" in caplog.text


def test_a_missing_primary_is_a_configuration_error_even_when_lenient():
    """`strict=False` forgives a missing *fallback*, not a missing primary:
    there would be nothing to call."""
    with pytest.raises(AssignmentConfigurationError):
        build_model_clients(_pair("", ""), strict=False)


# ---------------------------------------------------------------------------
# API startup.
# ---------------------------------------------------------------------------


def _patch_pair(monkeypatch, settings: AssignmentAgentSettings) -> None:
    import src.assignment_agent.config as agent_config

    monkeypatch.setattr(agent_config, "get_assignment_settings", lambda: settings)


def test_production_api_startup_refuses_a_missing_pair(monkeypatch):
    _patch_pair(monkeypatch, _pair())
    with pytest.raises(AssignmentConfigurationError):
        _production().validate_runtime_safety()


def test_production_api_startup_refuses_an_identical_pair(monkeypatch):
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))
    with pytest.raises(AssignmentConfigurationError):
        _production().validate_runtime_safety()


def test_production_api_startup_accepts_a_valid_pair(monkeypatch):
    _patch_pair(monkeypatch, _pair(*VALID))
    _production().validate_runtime_safety()


def test_development_startup_still_boots_without_a_pair(monkeypatch):
    """Local work must not require two model names from two providers."""
    _patch_pair(monkeypatch, _pair())
    Settings(
        _env_file=None, app_env="development", database_url="sqlite:///dev.db"
    ).validate_runtime_safety()


# ---------------------------------------------------------------------------
# The worker: the same check, before it claims anything.
# ---------------------------------------------------------------------------


def _seed_eligible(env):
    db = env.session()
    try:
        world = build_world(db)
        db.add(AutoAssignmentSetting(id=1, enabled=True, activation_delay="IMMEDIATE", version=1))
        db.commit()
        return approved_ticket(world).id
    finally:
        db.close()


def _enqueue(env) -> None:
    db = env.session()
    try:
        AssignmentTriggerService(db).enqueue_newly_eligible()
        db.commit()
    finally:
        db.close()


def test_the_worker_refuses_to_start_on_a_bad_pair_and_claims_nothing(v4_env, monkeypatch):
    ticket_id = _seed_eligible(v4_env)
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))
    monkeypatch.setattr(assignment_worker, "get_settings", _production)

    assert assignment_worker.main(["--once"]) == 2

    db = v4_env.session()
    try:
        # Not merely "no assignment": no job either. The pass never ran.
        assert db.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket_id)) is None
        assert db.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id)) is None
    finally:
        db.close()


def test_the_worker_starts_on_a_valid_pair(v4_env, monkeypatch):
    _seed_eligible(v4_env)
    _patch_pair(monkeypatch, _pair(*VALID))
    monkeypatch.setattr(assignment_worker, "get_settings", _production)
    agent = AssignmentAgentService(
        ScriptedAssignmentModel(model_version="scripted-primary"),
        ScriptedAssignmentModel(model_version="scripted-fallback"),
    )
    monkeypatch.setattr(AssignmentAgentService, "from_settings", classmethod(lambda cls, **kwargs: agent))

    assert assignment_worker.main(["--once"]) == 0


def test_the_rule_engine_needs_no_model_pair(monkeypatch):
    """The same unusable pair that stops the AI engine dead is irrelevant on
    `RULE`: nothing reads those names, so production boots and the worker
    claims jobs."""
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))
    settings = _production(assignment_decision_engine="RULE")
    monkeypatch.setattr(assignment_worker, "get_settings", lambda: settings)

    assert settings.require_assignment_failover is False
    settings.validate_runtime_safety()
    assignment_worker.verify_configuration()


def test_verify_configuration_matches_the_api_check(monkeypatch):
    """One rule, two processes. If these ever diverge, a worker would claim
    jobs on a configuration the API refuses to boot on."""
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))
    monkeypatch.setattr(assignment_worker, "get_settings", _production)
    with pytest.raises(AssignmentConfigurationError):
        assignment_worker.verify_configuration()
    with pytest.raises(AssignmentConfigurationError):
        _production().validate_runtime_safety()


# ---------------------------------------------------------------------------
# A configuration error is not a per-ticket outcome.
# ---------------------------------------------------------------------------


def test_direct_uses_strict_construction_in_production(v4_env, monkeypatch):
    _seed_eligible(v4_env)
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))
    monkeypatch.setattr("src.services.assignment_direct_service.get_settings", _production)

    db = v4_env.session()
    try:
        with pytest.raises(AssignmentConfigurationError):
            _ = DirectAssignmentService(db).engine
    finally:
        db.close()


def test_direct_never_turns_a_bad_pair_into_manual_required(v4_env, monkeypatch):
    ticket_id = _seed_eligible(v4_env)
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))
    _enqueue(v4_env)

    monkeypatch.setattr("src.services.assignment_direct_service.get_settings", _production)
    db = v4_env.session()
    try:
        with pytest.raises(AssignmentConfigurationError):
            DirectAssignmentService(db).run_due_jobs()
        db.rollback()
    finally:
        db.close()

    db = v4_env.session()
    try:
        job = db.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket_id))
        assert job is not None
        assert job.status != AssignmentJobStatus.MANUAL_REQUIRED.value
        assert job.error_code is None
        assert db.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id)) is None
    finally:
        db.close()


def test_proposal_never_turns_a_bad_pair_into_empty_rows(v4_env, monkeypatch):
    _seed_eligible(v4_env)
    _patch_pair(monkeypatch, _pair(VALID[0], VALID[0]))

    db = v4_env.session()
    try:
        coordinator = db.scalar(select(UserProfile).where(UserProfile.role == UserRole.COORDINATOR))
        batch_id = AssignmentProposalService(db).create_batch(coordinator.user_id).id
    finally:
        db.close()

    monkeypatch.setattr("src.services.assignment_proposal_service.get_settings", _production)
    db = v4_env.session()
    try:
        with pytest.raises(AssignmentConfigurationError):
            AssignmentProposalService(db).run_due_batches()
        db.rollback()
    finally:
        db.close()

    db = v4_env.session()
    try:
        batch = db.get(AssignmentProposalBatch, batch_id)
        # A READY batch of EMPTY rows would tell the coordinator the model had
        # nothing to suggest. It was never asked.
        assert batch.status == ProposalBatchStatus.BUILDING.value
        items = db.scalars(
            select(AssignmentProposalItem).where(AssignmentProposalItem.batch_id == batch_id)
        ).all()
        assert items
        assert all(item.status == ProposalItemStatus.PENDING.value for item in items)
    finally:
        db.close()


def test_an_injected_agent_needs_no_model_configuration(v4_env, monkeypatch):
    """The seam the tests and the E2E runner depend on: a scripted agent never
    reaches `from_settings`, so no key and no model pair is required."""
    ticket_id = _seed_eligible(v4_env)
    for name in ("ASSIGNMENT_PRIMARY_MODEL", "ASSIGNMENT_FALLBACK_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    _patch_pair(monkeypatch, _pair())
    monkeypatch.setattr("src.services.assignment_direct_service.get_settings", _production)
    _enqueue(v4_env)

    db = v4_env.session()
    try:
        agent = AssignmentAgentService(
            ScriptedAssignmentModel(model_version="scripted-primary"),
            ScriptedAssignmentModel(model_version="scripted-fallback"),
        )
        report = DirectAssignmentService(db, engine=agent).run_due_jobs()
        assert report.assignments_created == 1
        assert db.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id)) is not None
    finally:
        db.close()
