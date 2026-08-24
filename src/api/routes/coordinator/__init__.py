"""Coordinator route aggregator."""

from fastapi import APIRouter

from src.api.routes.coordinator import accounts, audit, categories, clusters, reports, technicians

router = APIRouter()
router.include_router(accounts.router)
router.include_router(technicians.router)
router.include_router(categories.router)
router.include_router(clusters.router)
router.include_router(audit.router)
router.include_router(reports.router)
