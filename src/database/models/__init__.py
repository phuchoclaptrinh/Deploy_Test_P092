"""SQLAlchemy ORM model exports for the Self Dev v3 product model."""

from src.database.models.ai_agent_session import AIAgentQuestion, AIAgentToolCall, AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.attachment import TicketAttachment
from src.database.models.audit_log import AuditLog
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.category import CategoryCatalog
from src.database.models.dispatch import AtRiskDecision, DispatchEvent
from src.database.models.floor import Floor
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.information_request import InformationRequest
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.resident_ticket_rate_limit import ResidentTicketRateLimit
from src.database.models.scoring_rule_version import ScoringRuleVersion
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.technician_availability import TechnicianAvailabilityEvent
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.ticket_attachment_upload_session import TicketAttachmentUploadSession
from src.database.models.ticket_relation import TicketRelation
from src.database.models.ticket_status_history import TicketStatusHistory
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile

__all__ = [
    "AIAnalysisRun",
    "AIAnalysisSession",
    "AIAgentToolCall",
    "AIAgentQuestion",
    "AtRiskDecision",
    "AuditLog",
    "AutoAssignmentSetting",
    "CategoryCatalog",
    "DispatchEvent",
    "Floor",
    "IncidentCase",
    "IncidentCaseMember",
    "InformationRequest",
    "Location",
    "LocationType",
    "Notification",
    "ResidentTicketRateLimit",
    "ResidentProfile",
    "ScoringRuleVersion",
    "Ticket",
    "TicketAttachment",
    "TicketAttachmentUploadSession",
    "TicketAssignment",
    "TicketStatusHistory",
    "TicketRelation",
    "TechnicianAvailabilityEvent",
    "TechnicianProfile",
    "TechnicianSkill",
    "Unit",
    "UserProfile",
]
