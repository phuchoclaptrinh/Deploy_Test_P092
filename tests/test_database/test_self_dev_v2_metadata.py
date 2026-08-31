import src.database.models  # noqa: F401
from src.database.base import Base


def test_canonical_tables_present_with_technician_workflow():
    tables = set(Base.metadata.tables)
    assert {
        "user_profiles", "resident_profiles", "floors", "units",
        "location_types", "locations", "categories", "ticket_risk_assessments",
        "tickets", "ticket_attachments", "ai_analysis_runs", "ai_analysis_sessions",
        "ai_agent_tool_calls", "ai_agent_questions", "ticket_status_history",
        "information_requests", "incident_cases", "incident_case_members",
            "notifications", "audit_logs", "technician_profiles",
            "technician_skills", "ticket_assignments",
        } <= tables
    # `buildings` went with the single-building catalog: there is one building
    # and it is not a domain entity, so nothing keys on it any more.
    # `scoring_rule_versions` went with risk scoring v2: a versioned JSON blob
    # of base scores and severity weights is a second, editable definition of a
    # priority, which is exactly what the rubric replaced.
    assert not {"bql_staff", "residents", "buildings", "scoring_rule_versions"} & tables


def test_ticket_has_separate_business_and_classification_state():
    columns = Base.metadata.tables["tickets"].c
    assert "status" in columns
    assert "classification_status" in columns
    assert "priority" in columns
    assert "reporter_user_id" in columns
    assert "source_unit_id" in columns
    assert "version" in columns
