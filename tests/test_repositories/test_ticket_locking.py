from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.technician_repository import TechnicianRepository
from src.repositories.ticket_repository import TicketRepository


class CapturingSession:
    def __init__(self):
        self.query = None

    def scalar(self, query):
        self.query = query
        return None


def test_coordinator_ticket_lock_targets_only_ticket_table():
    session = CapturingSession()
    TicketRepository(session).get_coordinator_ticket(uuid4(), lock=True)

    sql = str(session.query.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE OF tickets" in sql


def test_resident_ticket_lock_targets_only_ticket_table():
    session = CapturingSession()
    TicketRepository(session).get_resident_ticket(uuid4(), uuid4(), lock=True)

    sql = str(session.query.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE OF tickets" in sql


def test_technician_lock_targets_only_technician_profile_table():
    session = CapturingSession()
    TechnicianRepository(session).get_technician(uuid4(), lock=True)

    sql = str(session.query.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE OF technician_profiles" in sql


def test_assignment_lock_targets_only_assignment_table():
    session = CapturingSession()
    AssignmentRepository(session).get_for_technician(uuid4(), uuid4(), lock=True)

    sql = str(session.query.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE OF ticket_assignments" in sql
