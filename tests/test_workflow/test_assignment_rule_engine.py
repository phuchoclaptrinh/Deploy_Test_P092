"""RULE_ENGINE_V1 — the deterministic selection rule.

Two layers, and they answer different questions:

* the pure-ranking tests build `CandidateSnapshotV4` objects by hand, because
  the ordering is the thing being specified and a database would only make it
  harder to read;
* the orchestration tests run the real `DirectAssignmentService` and
  `AssignmentProposalService` against the real candidate builder with no model
  configured at all, which is the property the whole change exists for: an
  assignment now happens with zero model calls and cannot land in
  MANUAL_REQUIRED because a model timed out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from src.assignment_agent.schemas import (
    AssignmentDecisionType,
    AssignmentMode,
    AssignmentProposalBatchRequestV4,
    AssignmentTrigger,
    CandidateSnapshotV4,
    DirectAssignmentBatchRequestV4,
    DirectWorkItemRequestV4,
    ProposalWorkItemRequestV4,
    WorkItemType,
    WorkItemV4,
)
from src.assignment_rules.config import (
    AssignmentRuleConfig,
    AssignmentRuleConfigError,
    load_rule_config,
)
from src.assignment_rules.engine import ProjectedLoad, decide_items, sort_work_items
from src.assignment_rules.engine import select as select_technician
from src.assignment_rules.service import RuleBasedAssignmentService
from src.database.models.assignment_proposal import AIAssignmentJob
from src.services.assignment_direct_service import DirectAssignmentService
from src.services.assignment_proposal_service import AssignmentProposalService
from src.services.assignment_trigger_service import AssignmentTriggerService

from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.ticket_assignment import TicketAssignment
from src.models.enums import (
    ActivationDelay,
    AssignmentJobStatus,
    AssignmentSource,
    AssignmentStatus,
    Priority,
    ProposalBatchStatus,
    ProposalItemStatus,
)
from tests.test_v4.factories import approved_ticket, build_world, make_assignment

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

# Fixed ids so `technician_id` — the last key of every ranking — is a known
# order rather than whatever uuid4 produced this run.
TECH_A = UUID("00000000-0000-4000-8000-00000000000a")
TECH_B = UUID("00000000-0000-4000-8000-00000000000b")
TECH_C = UUID("00000000-0000-4000-8000-00000000000c")


def candidate(
    technician_id: UUID,
    *,
    total: int = 0,
    p1: int = 0,
    p2: int = 0,
    p3: int = 0,
    last_assigned_at: datetime | None = None,
) -> CandidateSnapshotV4:
    return CandidateSnapshotV4(
        technician_id=technician_id,
        display_name="KTV",
        matched_skills=["Thang máy"],
        active_assignment_count=total,
        active_p1_count=p1,
        active_p2_count=p2,
        active_p3_count=p3,
        last_assigned_at=last_assigned_at,
    )


def work_item(priority: Priority, *, tickets: int = 1, created_at: datetime | None = None) -> WorkItemV4:
    ticket_ids = [uuid4() for _ in range(tickets)]
    if tickets == 1:
        return WorkItemV4(
            work_item_type=WorkItemType.TICKET,
            work_item_id=ticket_ids[0],
            ticket_ids=ticket_ids,
            category_id=uuid4(),
            priority=priority,
            created_at=created_at,
        )
    return WorkItemV4(
        work_item_type=WorkItemType.INCIDENT_CASE,
        work_item_id=uuid4(),
        ticket_ids=ticket_ids,
        category_id=uuid4(),
        priority=priority,
        created_at=created_at,
    )


def direct_item(item: WorkItemV4, candidates: list[CandidateSnapshotV4]) -> DirectWorkItemRequestV4:
    return DirectWorkItemRequestV4(
        decision_id=uuid4(),
        work_item=item,
        trigger=AssignmentTrigger.INITIAL_AUTO,
        candidates=candidates,
    )


def chosen(item: WorkItemV4, candidates: list[CandidateSnapshotV4], config: AssignmentRuleConfig) -> UUID | None:
    return select_technician(item, candidates, ProjectedLoad(), config).technician_id


DEFAULTS = AssignmentRuleConfig()


def enable_auto(db) -> None:
    """DIRECT jobs are only created while the switch is ON (§4.2)."""
    row = db.get(AutoAssignmentSetting, 1) or AutoAssignmentSetting(id=1)
    db.add(row)
    row.enabled = True
    row.activation_delay = ActivationDelay.IMMEDIATE.value
    db.commit()


# ---------------------------------------------------------------------------
# The ranking key, per priority.
# ---------------------------------------------------------------------------


def test_p3_prefers_the_technician_running_the_fewest_emergencies():
    """P3 leads with the P3 count, not the total.

    B carries twice the work of A overall but no emergency; A is already on
    one. Five minutes is an interrupt, so the second emergency goes to B.
    """
    a = candidate(TECH_A, total=1, p3=1)
    b = candidate(TECH_B, total=2, p3=0)
    assert chosen(work_item(Priority.P3), [a, b], DEFAULTS) == TECH_B


def test_p2_leads_with_total_load_and_keeps_p3_as_its_second_key():
    a = candidate(TECH_A, total=1, p3=1)
    b = candidate(TECH_B, total=2, p3=0)
    assert chosen(work_item(Priority.P2), [a, b], DEFAULTS) == TECH_A

    # Same total: ordinary work drifts away from whoever is mid-emergency.
    c = candidate(TECH_A, total=2, p3=1)
    d = candidate(TECH_B, total=2, p3=0)
    assert chosen(work_item(Priority.P2), [c, d], DEFAULTS) == TECH_B


def test_p1_ignores_the_p3_count_entirely():
    """P1 ranks on total load, then idle time. A holds one emergency and B does
    not, but on P1 that is not a tiebreaker at all — the totals decide, and the
    72-hour job goes to the lighter person."""
    a = candidate(TECH_A, total=1, p3=1, last_assigned_at=NOW - timedelta(days=1))
    b = candidate(TECH_B, total=2, p3=0, last_assigned_at=NOW - timedelta(days=9))
    assert chosen(work_item(Priority.P1), [a, b], DEFAULTS) == TECH_A


def test_equal_load_goes_to_whoever_waited_longest():
    a = candidate(TECH_A, total=2, last_assigned_at=NOW - timedelta(hours=1))
    b = candidate(TECH_B, total=2, last_assigned_at=NOW - timedelta(days=3))
    assert chosen(work_item(Priority.P2), [a, b], DEFAULTS) == TECH_B


def test_a_technician_who_never_had_work_outranks_everyone_on_a_tie():
    a = candidate(TECH_A, total=0, last_assigned_at=NOW - timedelta(days=30))
    b = candidate(TECH_B, total=0, last_assigned_at=None)
    assert chosen(work_item(Priority.P2), [a, b], DEFAULTS) == TECH_B


def test_the_tie_break_can_be_switched_off_and_id_order_decides():
    """B has been idle for five days, A for a minute, and their loads are equal.

    With the tie-break on, idle time decides and B wins. With it off the key
    falls through to `technician_id`, and A — the lower id — wins instead.
    """
    a = candidate(TECH_A, total=2, last_assigned_at=NOW - timedelta(minutes=1))
    b = candidate(TECH_B, total=2, last_assigned_at=NOW - timedelta(days=5))

    assert chosen(work_item(Priority.P2), [a, b], DEFAULTS) == TECH_B
    off = AssignmentRuleConfig(tie_break_on_last_assigned_at=False)
    assert chosen(work_item(Priority.P2), [a, b], off) == TECH_A


def test_the_same_input_always_produces_the_same_answer():
    """No randomness anywhere: three fully tied candidates resolve on id."""
    candidates = [candidate(TECH_C), candidate(TECH_A), candidate(TECH_B)]
    item = work_item(Priority.P2)
    assert {chosen(item, candidates, DEFAULTS) for _ in range(20)} == {TECH_A}


# ---------------------------------------------------------------------------
# Projected load across a batch.
# ---------------------------------------------------------------------------


def test_a_batch_spreads_work_instead_of_stacking_it_on_the_idlest_person():
    """The point of §4.3a, without a model.

    Three identical P2 tickets and three idle technicians: a snapshot-only rule
    would hand all three to the same person, because all three read
    `active_assignment_count = 0`.
    """
    candidates = [candidate(TECH_A), candidate(TECH_B), candidate(TECH_C)]
    items = [direct_item(work_item(Priority.P2, created_at=NOW + timedelta(minutes=i)), candidates) for i in range(3)]

    decisions = decide_items(items, DEFAULTS, decided_at=NOW)

    assert [d.selected_technician_id for d in decisions] == [TECH_A, TECH_B, TECH_C]


def test_an_incident_case_consumes_capacity_for_every_member():
    """A four-member case is one decision and four tickets of projected load,
    so the next work item goes elsewhere even though the snapshot said both
    technicians were idle."""
    candidates = [candidate(TECH_A), candidate(TECH_B)]
    case = direct_item(work_item(Priority.P2, tickets=4, created_at=NOW), candidates)
    single = direct_item(work_item(Priority.P2, created_at=NOW + timedelta(minutes=5)), candidates)

    decisions = decide_items([case, single], DEFAULTS, decided_at=NOW)

    assert decisions[0].selected_technician_id == TECH_A
    assert decisions[1].selected_technician_id == TECH_B


def test_work_items_are_ordered_p3_then_p2_then_p1_then_oldest_first():
    older_p1 = direct_item(work_item(Priority.P1, created_at=NOW - timedelta(days=2)), [candidate(TECH_A)])
    newer_p2 = direct_item(work_item(Priority.P2, created_at=NOW), [candidate(TECH_A)])
    newest_p3 = direct_item(work_item(Priority.P3, created_at=NOW + timedelta(hours=1)), [candidate(TECH_A)])
    old_p2 = direct_item(work_item(Priority.P2, created_at=NOW - timedelta(days=5)), [candidate(TECH_A)])

    ordered = sort_work_items([older_p1, newer_p2, newest_p3, old_p2])

    assert [item.work_item.priority for item in ordered] == [
        Priority.P3,
        Priority.P2,
        Priority.P2,
        Priority.P1,
    ]
    assert ordered[1] is old_p2


def test_the_result_keeps_request_order_even_though_p3_was_decided_first():
    """The decisions come back alongside the request they answer; only the
    *processing* order is by priority."""
    p1 = direct_item(work_item(Priority.P1, created_at=NOW), [candidate(TECH_A), candidate(TECH_B)])
    p3 = direct_item(work_item(Priority.P3, created_at=NOW), [candidate(TECH_A), candidate(TECH_B)])

    decisions = decide_items([p1, p3], DEFAULTS, decided_at=NOW)

    assert [d.decision_id for d in decisions] == [p1.decision_id, p3.decision_id]
    # P3 ran first, so it took the idle technician and P1 took the next.
    assert dict(zip((d.decision_id for d in decisions), (d.selected_technician_id for d in decisions))) == {
        p3.decision_id: TECH_A,
        p1.decision_id: TECH_B,
    }


# ---------------------------------------------------------------------------
# Configured caps.
# ---------------------------------------------------------------------------


def test_a_technician_at_the_total_cap_is_filtered_out():
    config = AssignmentRuleConfig(max_active_assignments=3)
    at_cap = candidate(TECH_A, total=3)
    below = candidate(TECH_B, total=3 - 1)
    assert chosen(work_item(Priority.P2), [at_cap, below], config) == TECH_B


def test_a_p3_cap_only_binds_on_p3_work():
    config = AssignmentRuleConfig(max_active_p3_assignments=1)
    busy_on_p3 = candidate(TECH_A, total=1, p3=1)
    free = candidate(TECH_B, total=5, p3=0)

    assert chosen(work_item(Priority.P3), [busy_on_p3, free], config) == TECH_B
    # The same technician is still the lightest choice for a P2.
    assert chosen(work_item(Priority.P2), [busy_on_p3, free], config) == TECH_A


def test_every_candidate_over_the_cap_means_no_suitable_candidate_for_p1_and_p2():
    config = AssignmentRuleConfig(max_active_assignments=2)
    candidates = [candidate(TECH_A, total=2), candidate(TECH_B, total=4)]

    selection = select_technician(work_item(Priority.P2), candidates, ProjectedLoad(), config)

    assert selection.technician_id is None
    assert "giới hạn tải" in selection.reason


def test_p3_is_placed_over_the_cap_rather_than_missing_its_five_minutes():
    config = AssignmentRuleConfig(max_active_assignments=2)
    candidates = [candidate(TECH_A, total=4, p3=2), candidate(TECH_B, total=2, p3=0)]

    selection = select_technician(work_item(Priority.P3), candidates, ProjectedLoad(), config)

    assert selection.technician_id == TECH_B
    assert selection.overloaded is True
    assert "quá tải P3" in selection.reason


def test_the_p3_overload_exception_can_be_switched_off():
    config = AssignmentRuleConfig(max_active_assignments=2, allow_p3_overload_when_all_capped=False)
    candidates = [candidate(TECH_A, total=4), candidate(TECH_B, total=2)]

    assert select_technician(work_item(Priority.P3), candidates, ProjectedLoad(), config).technician_id is None


def test_a_cap_never_makes_a_full_incident_case_unassignable():
    """The cap asks "are you already at your limit", not "will this fit".

    A cap of 2 and a five-member case: the case is one piece of work landing on
    one person, so an idle technician takes it.
    """
    config = AssignmentRuleConfig(max_active_assignments=2)
    assert chosen(work_item(Priority.P2, tickets=5), [candidate(TECH_A, total=1)], config) == TECH_A


def test_a_cap_whose_count_backend_did_not_send_cannot_bind():
    """The P1/P2 splits are optional in the snapshot (§4.3). A request built
    without them must not have work refused over a count nobody supplied."""
    config = AssignmentRuleConfig(max_active_p1_assignments=1)
    legacy = CandidateSnapshotV4(technician_id=TECH_A, active_assignment_count=9, active_p3_count=0)
    assert chosen(work_item(Priority.P1), [legacy], config) == TECH_A


# ---------------------------------------------------------------------------
# The decision payload.
# ---------------------------------------------------------------------------


def test_a_decision_carries_the_rule_version_and_a_reason_a_human_can_read():
    items = [direct_item(work_item(Priority.P2, created_at=NOW), [candidate(TECH_A, total=2, p3=0)])]

    decision = decide_items(items, DEFAULTS, decided_at=NOW)[0]

    assert decision.decision is AssignmentDecisionType.SELECTED
    assert decision.model_version == "RULE_ENGINE_V1"
    assert decision.reason == (
        "Chọn theo RULE_ENGINE_V1: tải dự kiến 2, P3 dự kiến 0; "
        "ưu tiên thấp nhất trong nhóm ứng viên hợp lệ."
    )
    assert decision.decided_at == NOW


def test_the_service_answers_both_modes_without_a_model_or_a_fallback():
    service = RuleBasedAssignmentService(DEFAULTS, clock=lambda: NOW)
    candidates = [candidate(TECH_A), candidate(TECH_B)]

    direct = service.decide_direct(
        DirectAssignmentBatchRequestV4(
            request_id=uuid4(),
            work_items=[direct_item(work_item(Priority.P2, created_at=NOW), candidates)],
            requested_at=NOW,
        )
    )
    proposal = service.decide_proposal(
        AssignmentProposalBatchRequestV4(
            batch_decision_id=uuid4(),
            proposal_batch_id=uuid4(),
            assignment_mode=AssignmentMode.PROPOSAL,
            work_items=[
                ProposalWorkItemRequestV4(
                    decision_id=uuid4(),
                    work_item=work_item(Priority.P2, created_at=NOW),
                    candidates=candidates,
                )
            ],
            requested_at=NOW,
        )
    )

    assert service.engine_version == "RULE_ENGINE_V1"
    assert service.fallback_version is None
    for outcome in (direct, proposal):
        assert outcome.failures == []
        assert outcome.fallback_used is False
        assert outcome.result.decisions[0].selected_technician_id == TECH_A


# ---------------------------------------------------------------------------
# Configuration loading.
# ---------------------------------------------------------------------------


def test_the_shipped_rule_file_parses_and_ships_without_caps():
    config = load_rule_config()
    assert config.rule_version == "RULE_ENGINE_V1"
    assert config.has_any_cap is False
    assert config.allow_p3_overload_when_all_capped is True


def test_environment_overrides_beat_the_file(tmp_path, monkeypatch):
    path = tmp_path / "rules.yaml"
    path.write_text("max_active_assignments: 4\n", encoding="utf-8")
    monkeypatch.setenv("ASSIGNMENT_RULE_MAX_ACTIVE_ASSIGNMENTS", "9")

    assert load_rule_config(path).max_active_assignments == 9


def test_a_typo_in_a_rule_key_is_a_configuration_error(tmp_path):
    """The one failure this module exists to prevent: `max_active_assignment`
    would parse fine as YAML and cap nothing at all."""
    path = tmp_path / "rules.yaml"
    path.write_text("max_active_assignment: 4\n", encoding="utf-8")

    with pytest.raises(AssignmentRuleConfigError, match="max_active_assignment"):
        load_rule_config(path)


@pytest.mark.parametrize("body", ["max_active_assignments: 0\n", "max_active_assignments: nhiều\n"])
def test_an_unusable_cap_value_is_a_configuration_error(tmp_path, body):
    path = tmp_path / "rules.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(AssignmentRuleConfigError):
        load_rule_config(path)


# ---------------------------------------------------------------------------
# End to end, on the real services, with no model configured.
# ---------------------------------------------------------------------------


def test_direct_assigns_with_zero_model_calls(db_session):
    """The whole point of the change. No scripted model, no injected engine —
    `DirectAssignmentService` builds `RULE_ENGINE_V1` from the settings and the
    ticket comes out assigned."""
    world = build_world(db_session, technician_count=2)
    ticket = approved_ticket(world, priority=Priority.P2)
    enable_auto(db_session)
    AssignmentTriggerService(db_session).enqueue_newly_eligible(now=datetime.now(UTC))
    db_session.commit()

    report = DirectAssignmentService(db_session).run_due_jobs()

    assert report.assignments_created == 1
    assert report.manual_required == 0
    assignment = db_session.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket.id))
    assert assignment.assignment_source == AssignmentSource.AI_AUTO.value
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job.status == AssignmentJobStatus.COMPLETED.value
    assert job.primary_model == "RULE_ENGINE_V1"
    assert job.fallback_model is None
    assert job.completed_model == "RULE_ENGINE_V1"
    assert job.decision_reason.startswith("Chọn theo RULE_ENGINE_V1")


def test_direct_sends_a_p3_ahead_of_an_older_p1_in_the_same_round(db_session):
    world = build_world(db_session, technician_count=2)
    now = datetime.now(UTC)
    p1 = approved_ticket(world, priority=Priority.P1, created_at=now - timedelta(days=3))
    p3 = approved_ticket(world, priority=Priority.P3, created_at=now)
    enable_auto(db_session)
    AssignmentTriggerService(db_session).enqueue_newly_eligible(now=now)
    db_session.commit()

    DirectAssignmentService(db_session).run_due_jobs()

    by_ticket = {
        row.ticket_id: row.technician_id
        for row in db_session.scalars(select(TicketAssignment).where(TicketAssignment.is_active.is_(True)))
    }
    # Two idle technicians, and the P3 was placed first, so it took the one the
    # id order puts ahead and the P1 took the other.
    assert by_ticket[p3.id] != by_ticket[p1.id]
    first, second = sorted(t.user_id for t in world.technicians[:2])
    assert by_ticket[p3.id] == first
    assert by_ticket[p1.id] == second


def test_direct_balances_a_round_across_technicians(db_session):
    """Three eligible tickets, three idle technicians, one round: one each."""
    world = build_world(db_session, technician_count=3)
    for _ in range(3):
        approved_ticket(world, priority=Priority.P2)
    enable_auto(db_session)
    AssignmentTriggerService(db_session).enqueue_newly_eligible(now=datetime.now(UTC))
    db_session.commit()

    report = DirectAssignmentService(db_session).run_due_jobs()

    assert report.assignments_created == 3
    assigned = list(db_session.scalars(select(TicketAssignment.technician_id).where(TicketAssignment.is_active.is_(True))))
    assert len(set(assigned)) == 3


def test_direct_respects_a_configured_total_cap_and_says_so(db_session, monkeypatch):
    """Every technician is at the cap, so the ticket goes to the manual queue as
    a business answer — COMPLETED with NO_SUITABLE_CANDIDATE, not FAILED."""
    world = build_world(db_session, technician_count=1)
    busy = approved_ticket(world, priority=Priority.P2)
    make_assignment(world, busy, world.technician(0))
    ticket = approved_ticket(world, priority=Priority.P2)
    enable_auto(db_session)
    AssignmentTriggerService(db_session).enqueue_newly_eligible(now=datetime.now(UTC))
    db_session.commit()

    service = DirectAssignmentService(db_session, engine=RuleBasedAssignmentService(AssignmentRuleConfig(max_active_assignments=1)))
    report = service.run_due_jobs()

    assert report.assignments_created == 0
    assert report.manual_required == 1
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job.status == AssignmentJobStatus.COMPLETED.value
    assert job.selected_technician_id is None
    db_session.refresh(ticket)
    assert ticket.auto_assignment_paused is True


def test_a_technician_who_rejected_is_still_excluded_under_the_rule_engine(db_session):
    """§4.3 rule 1 lives in the candidate builder, so it survives the engine
    swap: the person who rejected this ticket is not in the snapshot, and the
    ranking never sees them."""
    world = build_world(db_session, technician_count=2)
    ticket = approved_ticket(world, priority=Priority.P2)
    rejected_by = world.technician(0)
    make_assignment(
        world,
        ticket,
        rejected_by,
        status=AssignmentStatus.REJECTED,
        is_active=False,
        end_reason="TECHNICIAN_REJECTED",
    )
    enable_auto(db_session)
    AssignmentTriggerService(db_session).enqueue_newly_eligible(now=datetime.now(UTC))
    db_session.commit()

    DirectAssignmentService(db_session).run_due_jobs()

    assignment = db_session.scalar(
        select(TicketAssignment).where(TicketAssignment.ticket_id == ticket.id, TicketAssignment.is_active.is_(True))
    )
    assert assignment.technician_id == world.technician(1).user_id


def test_proposal_fills_a_batch_without_a_model(db_session):
    world = build_world(db_session, technician_count=2)
    approved_ticket(world, priority=Priority.P2)
    approved_ticket(world, priority=Priority.P2)
    db_session.commit()

    service = AssignmentProposalService(db_session)
    batch = service.create_batch(world.coordinator.user_id)
    service.run_due_batches()
    batch = service.get_batch(batch.id)

    assert [item.status for item in batch.items] == [ProposalItemStatus.PROPOSED.value] * 2
    assert len({item.proposed_technician_id for item in batch.items}) == 2
    for item in batch.items:
        assert item.completed_model == "RULE_ENGINE_V1"
        assert item.reason.startswith("Chọn theo RULE_ENGINE_V1")


def test_rule_proposal_can_be_built_inline_without_waiting_for_worker(db_session):
    world = build_world(db_session, technician_count=2)
    approved_ticket(world, priority=Priority.P2)
    approved_ticket(world, priority=Priority.P2)
    db_session.commit()

    service = AssignmentProposalService(db_session)
    batch = service.create_batch(world.coordinator.user_id, build_immediately=True)

    assert batch.status == ProposalBatchStatus.READY.value
    assert [item.status for item in batch.items] == [ProposalItemStatus.PROPOSED.value] * 2
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.proposal_batch_id == batch.id))
    assert job is not None
    assert job.status == "COMPLETED"


def test_rule_proposal_inline_ready_includes_previously_paused_tickets(db_session):
    """Regression: RULE must not need the worker's polling cadence, and the
    inline batch it builds has to include the AUTO_ASSIGNMENT_DISABLED backlog
    the coordinator opened this proposal to clear -- not just the one ticket
    that was never paused."""
    world = build_world(db_session, technician_count=2)
    approved_ticket(world, priority=Priority.P2)
    approved_ticket(world, priority=Priority.P2, auto_assignment_paused=True)
    approved_ticket(world, priority=Priority.P1, auto_assignment_paused=True)
    db_session.commit()

    service = AssignmentProposalService(db_session)
    batch = service.create_batch(world.coordinator.user_id, build_immediately=True)

    assert batch.status == ProposalBatchStatus.READY.value
    assert len(batch.items) == 3
    assert [item.status for item in batch.items] == [ProposalItemStatus.PROPOSED.value] * 3
