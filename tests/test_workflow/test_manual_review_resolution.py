"""§8.3 — the Coordinator settles a report the analysis could not classify.

Two things this module pins down:

* **The Category check reads the stored prediction in the spelling that run
  wrote it.** v3 and v4 store Category UUIDs (§1.3); only historical v2 rows
  store Category codes. Comparing a code against a v4 run's UUID list rejected
  every legitimate choice, which is the bug these tests stand on.
* **A missing severity is asked for, not invented.** A report whose session
  failed reaches MANUAL_REVIEW with no severity at all, and §9.5 has no default
  — the Coordinator names one and Backend scores from it.

Resolving is only the end of classification: the ticket becomes RESOLVED and
still has to pass the normal APPROVE action (§10).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.api.routes.coordinator_tickets import coordinator_ticket_response
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.audit_log import AuditLog
from src.models.api.coordinator import ManualReviewResolveRequest
from src.models.api.errors import CATEGORY_REQUIRED, SEVERITY_REQUIRED, DomainError
from src.models.enums import (
    AnalysisRunStatus,
    Category,
    ClassificationStatus,
    Priority,
    ResolutionSource,
    Severity,
    SeveritySource,
    TicketStatus,
)
from src.services.coordinator_service import CoordinatorService
from tests.test_workflow.factories import build_world, make_ticket


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=2, technician_count=1)


def _manual_review_ticket(world, *, severity: Severity | None = Severity.MEDIUM, red_flag: bool = False):
    ticket = make_ticket(
        world,
        location=world.corridor_10,
        classification_status=ClassificationStatus.MANUAL_REVIEW,
        severity=severity,
    )
    ticket.red_flag_detected = red_flag
    world.db.commit()
    return ticket


def _run(
    world,
    ticket,
    *,
    contract_version: str = "v4",
    text_categories: list[str] | None = None,
    image_categories: list[str] | None = None,
    severity: Severity | None = Severity.MEDIUM,
    grouping_status: str | None = None,
):
    run = AIAnalysisRun(
        ticket_id=ticket.id,
        run_number=1,
        text_categories=text_categories or [],
        image_categories=image_categories,
        red_flag_text=False,
        red_flag_signal=False,
        severity=severity,
        severity_source=SeveritySource.TEXT_FALLBACK if severity else None,
        status=AnalysisRunStatus.SUCCEEDED,
        contract_version=contract_version,
        exit_reason="LIMIT_REACHED",
        is_confident=False,
        grouping_status=grouping_status,
    )
    world.db.add(run)
    world.db.commit()
    return run


def _resolve(world, ticket, category, source, *, severity: Severity | None = None, reason: str = "BQL xác nhận."):
    return CoordinatorService(world.db).resolve_manual_review(
        world.coordinator.user_id,
        ticket.id,
        ManualReviewResolveRequest(
            category_id=category.id,
            resolution_source=source,
            severity=severity,
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
    # A stored severity is kept, and the Coordinator is never asked to restate it.
    assert resolved.severity is Severity.MEDIUM
    assert resolved.severity_source is None


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
# The missing severity.
# ---------------------------------------------------------------------------


def test_a_ticket_without_severity_resolves_on_the_coordinator_choice(world):
    ticket = _manual_review_ticket(world, severity=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)], severity=None)

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT, severity=Severity.HIGH)

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    assert resolved.severity is Severity.HIGH
    # The value is a human's, and the record says so rather than crediting a model.
    assert resolved.severity_source is SeveritySource.COORDINATOR_MANUAL
    # 35 base + 20 severity = 55 -> P2, with an SLA off the same calculation.
    assert float(resolved.score_total) == 55.0
    assert resolved.priority is Priority.P2
    assert resolved.sla_due_at is not None


def test_a_ticket_without_severity_is_refused_when_none_is_supplied(world):
    ticket = _manual_review_ticket(world, severity=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)], severity=None)

    with pytest.raises(DomainError) as error:
        _resolve(world, ticket, world.elevator, ResolutionSource.TEXT)

    assert error.value.code == SEVERITY_REQUIRED
    assert error.value.status_code == 400
    assert "Mức độ nghiêm trọng" in error.value.message
    world.db.refresh(ticket)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.severity is None
    assert ticket.priority is None


def test_a_stored_severity_wins_over_one_sent_by_the_coordinator(world):
    """Manual review settles the Category; changing an existing severity is the
    classification override, not this action."""
    ticket = _manual_review_ticket(world, severity=Severity.HIGH)
    _run(world, ticket, text_categories=[str(world.elevator.id)])

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT, severity=Severity.LOW)

    assert resolved.severity is Severity.HIGH
    assert resolved.severity_source is None


def test_the_audit_entry_records_the_manual_severity_and_its_source(world):
    ticket = _manual_review_ticket(world, severity=None)
    _run(world, ticket, text_categories=[str(world.elevator.id)], severity=None)

    _resolve(world, ticket, world.elevator, ResolutionSource.TEXT, severity=Severity.LOW, reason="AI không đánh giá được.")

    entry = world.db.scalar(
        select(AuditLog).where(AuditLog.entity_id == ticket.id, AuditLog.action == "RESOLVE_MANUAL_REVIEW")
    )
    assert entry is not None
    assert entry.reason == "AI không đánh giá được."
    assert entry.before_data["severity"] is None
    assert entry.after_data["severity"] == Severity.LOW.value
    assert entry.after_data["severity_source"] == SeveritySource.COORDINATOR_MANUAL.value
    assert entry.after_data["resolution_source"] == ResolutionSource.TEXT.value


def test_a_red_flag_ticket_stays_p3_whatever_severity_is_chosen(world):
    """§9.6: red flag forces P3 and bypasses scoring; a manual LOW cannot soften it."""
    ticket = _manual_review_ticket(world, severity=None, red_flag=True)
    _run(world, ticket, text_categories=[str(world.elevator.id)], severity=None)

    resolved = _resolve(world, ticket, world.elevator, ResolutionSource.TEXT, severity=Severity.LOW)

    assert resolved.priority is Priority.P3
    assert resolved.score_total is None
    assert resolved.severity is Severity.LOW
    assert resolved.severity_source is SeveritySource.COORDINATOR_MANUAL


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
