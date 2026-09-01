"""§8.3 — the Coordinator settles a report the analysis could not classify.

Two things this module pins down:

* **The Category check reads the stored prediction in the spelling that run
  wrote it.** v3 and v4 store Category UUIDs (§1.3); only historical v2 rows
  store Category codes. Comparing a code against a v4 run's UUID list rejected
  every legitimate choice, which is the bug these tests stand on.
* **A missing risk assessment is asked for, not invented.** A report whose
  session failed reaches MANUAL_REVIEW with no assessment at all, and there is
  no default — the Coordinator scores the five criteria and Backend derives the
  priority from them.

Resolving is only the end of classification: the ticket becomes RESOLVED and
still has to pass the normal APPROVE action (§10).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.api.routes.coordinator_tickets import coordinator_ticket_response
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.audit_log import AuditLog
from src.models.agent_schemas import RiskCriteriaPayload
from src.models.api.coordinator import ManualReviewResolveRequest
from src.models.api.errors import CATEGORY_REQUIRED, RISK_ASSESSMENT_REQUIRED, DomainError
from src.models.enums import (
    AnalysisRunStatus,
    Category,
    ClassificationStatus,
    Priority,
    ResolutionSource,
    RiskAssessmentSource,
    TicketStatus,
)
from src.services.coordinator_service import CoordinatorService
from src.services.risk_assessment_service import RiskAssessmentService
from tests.test_workflow.factories import build_world, make_ticket

#: A middling assessment: 11.25, a P1. Used where the test is about the Category
#: check rather than about the score.
MILD = RiskCriteriaPayload(
    human_safety=0, property_spread=1, essential_function=1, affected_scope=0, deterioration_speed=1
)
#: 35 + 2.5 + 17.5 = 55.00, a P3. Distinct from MILD so a test can tell which one
#: a resolution used.
SEVERE = RiskCriteriaPayload(
    human_safety=4, property_spread=2, essential_function=2, affected_scope=0, deterioration_speed=0
)


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=2, technician_count=1)


def _manual_review_ticket(world, *, criteria: RiskCriteriaPayload | None = MILD):
    """A ticket parked in manual review, with or without an assessment.

    `criteria=None` is the case the Coordinator has to settle: the analysis
    never scored the ticket, so nothing about it implies a priority.
    """
    ticket = make_ticket(
        world,
        location=world.corridor_10,
        classification_status=ClassificationStatus.MANUAL_REVIEW,
    )
    if criteria is not None:
        RiskAssessmentService(world.db).record(
            ticket, criteria=criteria.to_domain(), source=RiskAssessmentSource.AI_ANALYSIS
        )
    world.db.commit()
    return ticket


def _run(
    world,
    ticket,
    *,
    contract_version: str = "v4",
    text_categories: list[str] | None = None,
    image_categories: list[str] | None = None,
    grouping_status: str | None = None,
):
    run = AIAnalysisRun(
        ticket_id=ticket.id,
        run_number=1,
        text_categories=text_categories or [],
        image_categories=image_categories,
        status=AnalysisRunStatus.SUCCEEDED,
        contract_version=contract_version,
        exit_reason="LIMIT_REACHED",
        is_confident=False,
        grouping_status=grouping_status,
    )
    world.db.add(run)
    world.db.commit()
    return run


def _resolve(
    world,
    ticket,
    category,
    source,
    *,
    criteria: RiskCriteriaPayload | None = None,
    blockers: list[str] | None = None,
    reason: str = "BQL xác nhận.",
):
    return CoordinatorService(world.db).resolve_manual_review(
        world.coordinator.user_id,
        ticket.id,
        ManualReviewResolveRequest(
            category_id=category.id,
            resolution_source=source,
            criteria=criteria,
            blockers=blockers or [],
            reason=reason,
        ),
    )


# ---------------------------------------------------------------------------
# v4 predictions are Category UUIDs.
# ---------------------------------------------------------------------------


def test_v4_image_source_accepts_the_matching_uuid_category(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, image_categories=[str(world.water.id)], text_categories=[str(world.elevator.id)])

    resolved = _resolve(world, ticket, world.water, ResolutionSource.IMAGE)

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    assert resolved.category_id == world.water.id
    assert resolved.priority is not None
    assert resolved.sla_due_at is not None
    # A stored assessment is kept, and the Coordinator is never asked to
    # restate it: manual review settles the Category, not the score.
    assert float(resolved.risk_score) == 11.25
    assert resolved.priority is Priority.P1


def test_v4_text_source_accepts_the_matching_uuid_category(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, text_categories=[str(world.elevator.id)], image_categories=[str(world.water.id)])

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT)

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    assert resolved.category_id == world.elevator.id


def test_resolving_manual_review_reopens_grouping_for_groupable_category(world):
    ticket = _manual_review_ticket(world)
    run = _run(
        world,
        ticket,
        image_categories=[str(world.water.id)],
        text_categories=[str(world.elevator.id)],
        grouping_status="NOT_ELIGIBLE",
    )

    _resolve(world, ticket, world.water, ResolutionSource.IMAGE)

    world.db.refresh(run)
    assert run.grouping_status == "PENDING"


def test_a_category_outside_the_image_prediction_is_rejected(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, image_categories=[str(world.water.id)], text_categories=[str(world.water.id)])

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.elevator, ResolutionSource.IMAGE)

    assert error.value.code == CATEGORY_REQUIRED
    world.db.refresh(ticket)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW


def test_a_category_outside_the_text_prediction_is_rejected(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, text_categories=[str(world.water.id)])

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.elevator, ResolutionSource.TEXT)

    assert error.value.code == CATEGORY_REQUIRED


def test_image_source_is_rejected_when_the_run_analysed_no_image(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, text_categories=[str(world.water.id)], image_categories=None)

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.water, ResolutionSource.IMAGE)

    assert error.value.code == CATEGORY_REQUIRED


def test_image_source_is_rejected_when_there_is_no_analysis_run_at_all(world):
    """Naming the image as the source with nothing to name would file the decision
    as AI-backed when no analysis ever ran."""
    ticket = _manual_review_ticket(world)

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.water, ResolutionSource.IMAGE)

    assert error.value.code == CATEGORY_REQUIRED
    assert error.value.status_code == 400
    assert "ảnh" in error.value.message
    assert "Danh mục khác" in error.value.message
    world.db.refresh(ticket)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.category_id is None


def test_text_source_is_rejected_when_there_is_no_analysis_run_at_all(world):
    ticket = _manual_review_ticket(world)

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.water, ResolutionSource.TEXT)

    assert error.value.code == CATEGORY_REQUIRED
    assert error.value.status_code == 400
    assert "text" in error.value.message
    assert "Danh mục khác" in error.value.message
    world.db.refresh(ticket)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.category_id is None


def test_text_source_is_rejected_when_the_run_predicted_no_text_category(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, text_categories=[], image_categories=[str(world.water.id)])

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.water, ResolutionSource.TEXT)

    assert error.value.code == CATEGORY_REQUIRED
    assert "Danh mục khác" in error.value.message


def test_other_source_resolves_without_any_analysis_run(world):
    """A report the analysis never reached is still the Coordinator's to settle."""
    ticket = _manual_review_ticket(world)

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.OTHER)

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    assert resolved.category_id == world.elevator.id
    assert resolved.priority is not None
    assert resolved.status is TicketStatus.NEW


def test_other_source_stays_a_free_coordinator_choice(world):
    """§8.3: overriding to a Category neither source proposed is a real option."""
    ticket = _manual_review_ticket(world)
    _run(world, ticket, text_categories=[str(world.water.id)], image_categories=[str(world.water.id)])

    resolved = _resolve(world, ticket, world.electrical, ResolutionSource.OTHER)

    assert resolved.category_id == world.electrical.id


# ---------------------------------------------------------------------------
# The missing risk assessment.
# ---------------------------------------------------------------------------


def test_a_ticket_without_an_assessment_resolves_on_the_coordinator_scores(world):
    ticket = _manual_review_ticket(world, criteria=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT, criteria=SEVERE)

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    # 35 + 2.5 + 17.5 = 55.00 -> P3, with an SLA off the same calculation.
    assert float(resolved.risk_score) == 55.00
    assert resolved.priority is Priority.P3
    assert resolved.sla_due_at is not None

    # The scores are a human's, and the record says so rather than crediting a
    # model that never produced them.
    assessment = RiskAssessmentService(world.db).current(resolved)
    assert assessment.source is RiskAssessmentSource.HUMAN_REVIEW
    assert assessment.reviewed_by == world.coordinator.user_id


def test_a_ticket_without_an_assessment_is_refused_when_no_scores_are_supplied(world):
    ticket = _manual_review_ticket(world, criteria=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.elevator, ResolutionSource.TEXT)

    assert error.value.code == RISK_ASSESSMENT_REQUIRED
    assert error.value.status_code == 400
    assert "chấm điểm rủi ro" in error.value.message
    world.db.refresh(ticket)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.risk_score is None
    assert ticket.priority is None


def test_a_stored_assessment_wins_over_scores_sent_by_the_coordinator(world):
    """Manual review settles the Category; changing an existing score is the
    classification override, not this action."""
    ticket = _manual_review_ticket(world, criteria=MILD)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT, criteria=SEVERE)

    assert float(resolved.risk_score) == 11.25
    assert resolved.priority is Priority.P1


def test_a_coordinator_blocker_floors_the_priority_it_scores_to(world):
    """A human can name an emergency the analysis never saw."""
    ticket = _manual_review_ticket(world, criteria=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    resolved = _resolve(
        world,
        ticket,
        world.elevator,
        ResolutionSource.TEXT,
        criteria=MILD,
        blockers=["PERSON_TRAPPED_IN_ELEVATOR"],
    )

    assert float(resolved.risk_score) == 11.25
    assert resolved.priority is Priority.P5


def test_the_audit_entry_records_the_manual_resolution(world):
    ticket = _manual_review_ticket(world, criteria=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    _resolve(
        world,
        ticket,
        world.elevator,
        ResolutionSource.TEXT,
        criteria=SEVERE,
        reason="AI không đánh giá được.",
    )

    entry = world.db.scalar(
        select(AuditLog).where(AuditLog.entity_id == ticket.id, AuditLog.action == "RESOLVE_MANUAL_REVIEW")
    )
    assert entry is not None
    assert entry.reason == "AI không đánh giá được."
    assert entry.before_data["risk_score"] is None
    assert entry.after_data["risk_score"] == 55.0
    assert entry.after_data["priority"] == Priority.P3.value
    assert entry.after_data["resolution_source"] == ResolutionSource.TEXT.value


# ---------------------------------------------------------------------------
# Historical data and the approval boundary.
# ---------------------------------------------------------------------------


def test_v3_uuid_predictions_still_resolve(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, contract_version="v3", text_categories=[str(world.water.id)])

    assert _resolve(world, ticket, world.water, ResolutionSource.TEXT).category_id == world.water.id


def test_legacy_v2_code_predictions_still_resolve(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, contract_version="v2", text_categories=[Category.WATER.value])

    assert _resolve(world, ticket, world.water, ResolutionSource.TEXT).category_id == world.water.id


def test_a_legacy_v2_run_still_rejects_a_category_it_never_predicted(world):
    ticket = _manual_review_ticket(world)
    _run(world, ticket, contract_version="v2", text_categories=[Category.WATER.value])

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.elevator, ResolutionSource.TEXT)

    assert error.value.code == CATEGORY_REQUIRED


def test_resolving_classification_does_not_approve_the_ticket(world):
    """§10: approval stays a separate coordinator action."""
    ticket = _manual_review_ticket(world)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT)

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    assert resolved.status is TicketStatus.NEW
    assert resolved.approved_at is None
    assert "APPROVE" in coordinator_ticket_response(resolved).available_actions

    approved = CoordinatorService(world.db).approve(world.coordinator.user_id, ticket.id)

    assert approved.status is TicketStatus.APPROVED
