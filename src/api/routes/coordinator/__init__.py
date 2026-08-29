"""Coordinator route aggregator."""

from fastapi import APIRouter

from src.api.routes.coordinator import (
    accounts,
    audit,
    categories,
    clusters,
    dispatch,
    reports,
    technicians,
    visual_assignment,
)

router = APIRouter()
router.include_router(accounts.router)
router.include_router(technicians.router)
router.include_router(categories.router)
router.include_router(clusters.router)
router.include_router(audit.router)
router.include_router(reports.router)
router.include_router(visual_assignment.router)
router.include_router(dispatch.router)
