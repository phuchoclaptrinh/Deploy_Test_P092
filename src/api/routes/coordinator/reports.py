"""Coordinator report routes."""

import csv
import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.coordinator import (
    ExportReportRequest,
    SlaPerformanceReport,
    TechnicianProductivityReport,
    TicketSummaryReport,
)
from src.services.coordinator_service import CoordinatorService
from src.services.technician_report_service import TechnicianReportService

router = APIRouter()


@router.get("/reports/tickets-summary", response_model=ApiResponse[TicketSummaryReport])
def tickets_summary(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, TicketSummaryReport(**CoordinatorService(db).tickets_summary()))


@router.get("/reports/sla-performance", response_model=ApiResponse[SlaPerformanceReport])
def sla_performance(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, SlaPerformanceReport(**CoordinatorService(db).sla_performance()))


@router.get("/reports/technician-productivity", response_model=ApiResponse[TechnicianProductivityReport])
def technician_productivity(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    period: str = Query(default="month", pattern="^(week|month)$"),
):
    """§2.13. Coordinator-only; §0.4 gives no other role this table."""
    return ok(request, TechnicianProductivityReport(**TechnicianReportService(db).productivity(period)))


@router.post("/reports/export")
def export_report(
    body: ExportReportRequest,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    service = CoordinatorService(db)
    output = io.StringIO()
    writer = csv.writer(output)
    report = service.sla_performance() if body.report == "sla-performance" else service.tickets_summary()
    writer.writerow(["key", "value"])
    for key, value in report.items():
        writer.writerow([key, value])
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{body.report}.csv"'},
    )
