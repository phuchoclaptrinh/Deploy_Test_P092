"""Domain model contracts for FixIt Agent."""

from src.models.agent_schemas import AgentResult
from src.models.enums import Category, ClassificationStatus, Priority, Severity, TicketStatus, UserRole
from src.models.scoring_schemas import ScoringResult

__all__ = [
    "AgentResult",
    "Category",
    "ClassificationStatus",
    "Priority",
    "ScoringResult",
    "Severity",
    "TicketStatus",
    "UserRole",
]
