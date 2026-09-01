"""Central Self Dev v3 API router."""

from fastapi import APIRouter

from src.api.routes import (
    auth,
    catalog,
    coordinator,
    coordinator_attachments,
    coordinator_tickets,
    notifications,
    storage,
    technician_assignments,
    tickets,
)

api_router = APIRouter()
api_router.include_router(auth.router, tags=["Auth"])
api_router.include_router(catalog.router, prefix="/catalog", tags=["Catalog"])
api_router.include_router(storage.router, prefix="/storage", tags=["Storage"])
api_router.include_router(tickets.router, prefix="/tickets", tags=["Resident Tickets"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(coordinator_tickets.router, prefix="/coordinator", tags=["Coordinator"])
api_router.include_router(coordinator_attachments.router, prefix="/coordinator", tags=["Coordinator"])
api_router.include_router(coordinator.router, prefix="/coordinator", tags=["Coordinator"])
api_router.include_router(technician_assignments.router, prefix="/technician", tags=["Technician"])
