"""Deterministic action-only evaluation for the concrete v4 golden dataset.

The model judgements are replayed from scripted golden evidence.  The code
under evaluation is the current Agent orchestration: graph routing, tool use,
resident pause/resume, duplicate/grouping policy, and assignment fallback.
Image-dependent rows and Backend-only pre-filter rows are intentionally out of
scope for this runner.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import traceback
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.database.models  # noqa: F401,E402
from src.agents.v4.graph import build_analysis_graph_v4  # noqa: E402
from src.agents.v4.llm_client import (  # noqa: E402
    ActionDecisionV4,
    DuplicateJudgementV4,
    TextExtractionV4,
)
from src.agents.v4.service import _build_initial_state  # noqa: E402
from src.agents.v4.state import NEVER_RAN, AgentStateV4  # noqa: E402
from src.agents.v4.tools import BackendAnalysisToolAdapterV4  # noqa: E402
from src.assignment_agent.model_client import AssignmentModelError  # noqa: E402
from src.assignment_agent.schemas import (  # noqa: E402
    AssignmentProposalBatchRequestV4,
    DirectAssignmentBatchRequestV4,
)
from src.assignment_agent.service import AssignmentAgentService  # noqa: E402
from src.database.base import Base  # noqa: E402
from src.database.models.ai_agent_session import AIAgentQuestion, AIAgentToolCall  # noqa: E402
from src.database.models.building import Building  # noqa: E402
from src.database.models.category import CategoryCatalog  # noqa: E402
from src.database.models.floor import Floor  # noqa: E402
from src.database.models.location import Location  # noqa: E402
from src.database.models.location_type import LocationType  # noqa: E402
from src.database.models.resident_profile import ResidentProfile  # noqa: E402
from src.database.models.ticket import Ticket  # noqa: E402
from src.database.models.unit import Unit  # noqa: E402
from src.database.models.user_profile import UserProfile  # noqa: E402
from src.models.agent_schemas_v4 import (  # noqa: E402
    AgentSearchPurpose,
    ProposeCaseGroupingResponseV4,
    SearchRelatedTicketsResponseV4,
)
from src.models.enums import ClassificationStatus, Priority, TicketStatus, UserRole  # noqa: E402
from src.services.agent_backend_service import AgentBackendService  # noqa: E402

DATASET_PATH = ROOT / "Self_Dev_Docs" / "pairwise_v4" / "concrete_golden_cases_v4.json"
DEFAULT_OUTPUT_DIR = ROOT / "eval" / "results" / "agent_action_v4"
FIXED_NOW = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)

A1_READY = {
    *(f"A1-{index:03d}" for index in range(1, 14)),
    "A1-033",
    "A1-034",
    "A1-035",
    "A1-036",
}
A3_READY = {
    *(f"A3-{index:03d}" for index in range(1, 8)),
    "A3-015",
    "A3-016",
    "A3-018",
    "A3-019",
    "A3-020",
    "A3-021",
    "A3-022",
    "A3-023",
    "A3-026",
}
BACKEND_ONLY = {"B1-005", "B1-007", "B2-005", "B2-006"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


class ScriptedAnalysisLLM:
    def __init__(
        self,
        text_extractions: list[TextExtractionV4],
        decisions: list[ActionDecisionV4],
        duplicate_judgements: list[DuplicateJudgementV4],
    ) -> None:
        self.text_extractions = list(text_extractions)
        self.decisions = list(decisions)
        self.duplicate_judgements = list(duplicate_judgements)
        self.calls: list[dict[str, Any]] = []

    def extract_text(self, **kwargs) -> TextExtractionV4:
        if not self.text_extractions:
            raise AssertionError("extract_text được gọi nhiều hơn script")
        result = self.text_extractions.pop(0)
        self.calls.append(
            {
                "operation": "extract_text",
                "context_notes": kwargs.get("context_notes", []),
                "result": result.model_dump(mode="json"),
            }
        )
        return result

    def extract_image(self, **_kwargs):
        raise AssertionError("Case action-only không được gọi extract_image")

    def judge_duplicate(self, **kwargs) -> DuplicateJudgementV4:
        if not self.duplicate_judgements:
            raise AssertionError("judge_duplicate được gọi nhiều hơn script")
        result = self.duplicate_judgements.pop(0)
        self.calls.append(
            {
                "operation": "judge_duplicate",
                "candidate_ids": [str(item.get("ticket_id")) for item in kwargs.get("candidates", [])],
                "result": result.model_dump(mode="json"),
            }
        )
        return result

    def decide_next_action(self, **kwargs) -> ActionDecisionV4:
        if not self.decisions:
            raise AssertionError("decide_next_action được gọi nhiều hơn script")
        result = self.decisions.pop(0)
        self.calls.append(
            {
                "operation": "decide_next_action",
                "available_actions": kwargs.get("available_actions", []),
                "budget_note": kwargs.get("budget_note"),
                "result": result.model_dump(mode="json"),
            }
        )
        return result


class ScriptedToolPort:
    supported_purposes = frozenset({AgentSearchPurpose.DUPLICATE, AgentSearchPurpose.GROUPING})

    def __init__(self, backend: AgentBackendService, scripts: list[dict[str, Any]]) -> None:
        self.backend = backend
        self.scripts = list(scripts)
        self.ask_adapter = BackendAnalysisToolAdapterV4(backend)
        self.calls: list[dict[str, Any]] = []

    def _script(self, tool: str, purpose: str | None = None) -> dict[str, Any] | None:
        matching = [
            item
            for item in self.scripts
            if item.get("tool") == tool and (purpose is None or item.get("purpose") == purpose)
        ]
        return matching[-1] if matching else None

    def _record_backend_call(self, session_id: UUID, tool: str, request: BaseModel, response: BaseModel) -> None:
        session = self.backend._session(session_id)
        self.backend._increment_tool(session)
        self.backend._log_tool(
            session,
            tool,
            request.model_dump(mode="json"),
            response.model_dump(mode="json"),
        )
        self.backend.db.commit()

    def search_related_tickets(self, request):
        purpose = request.purpose.value
        script = self._script("search_related_tickets", purpose)
        payload = (
            script["response"]
            if script is not None
            else {"purpose": purpose, "related_tickets": []}
        )
        response = SearchRelatedTicketsResponseV4.model_validate(payload)
        self._record_backend_call(request.session_id, "search_related_tickets", request, response)
        self.calls.append(
            {
                "tool": "search_related_tickets",
                "purpose": purpose,
                "request": request.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "source": "dataset" if script is not None else "empty-default",
            }
        )
        return response

    def propose_case_grouping(self, request):
        script = self._script("propose_case_grouping")
        if script is None:
            raise AssertionError("Thiếu script propose_case_grouping")
        response = ProposeCaseGroupingResponseV4.model_validate(script["response"])
        self._record_backend_call(request.session_id, "propose_case_grouping", request, response)
        self.calls.append(
            {
                "tool": "propose_case_grouping",
                "request": request.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "source": "dataset",
            }
        )
        return response

    def ask_resident(self, request):
        question_id = self.ask_adapter.ask_resident(request)
        self.calls.append(
            {
                "tool": "ask_resident",
                "request": request.model_dump(mode="json"),
                "response": {"question_id": str(question_id)},
                "source": "agent-generated-question",
            }
        )
        return question_id


class ScriptedAssignmentClient:
    def __init__(self, model_version: str, script: dict[str, Any]) -> None:
        self.model_version = model_version
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def decide(self, *, system_prompt: str, user_prompt: str):
        self.calls.append(
            {
                "model_version": self.model_version,
                "script_type": self.script.get("type"),
                "user_prompt": user_prompt,
                "system_prompt_length": len(system_prompt),
            }
        )
        if self.script.get("type") == "ERROR":
            raise AssignmentModelError(str(self.script.get("error") or "SCRIPTED_ERROR"))
        return self.script.get("payload")


def _category_maps(case: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    by_id = {str(item["category_id"]): str(item["display_name"]) for item in case["input"]["category_catalog"]}
    by_code = {str(item["code"]): str(item["display_name"]) for item in case["input"]["category_catalog"]}
    return by_id, by_code


def _text(
    categories: list[str],
    *,
    red: bool = False,
    understandable: bool = True,
    severity: str | None = "MEDIUM",
    confident: bool = True,
    fact: str = "Biểu hiện theo mô tả test",
) -> TextExtractionV4:
    return TextExtractionV4(
        text_categories=categories,
        red_flag_text=red,
        text_understandable=understandable,
        symptom_facts=[fact] if fact else [],
        severity=severity,
        severity_unknown_reason=None if severity else "Chưa có mô tả đủ để xác định mức độ.",
        is_confident=confident,
        notes="Biểu hiện phù hợp category đã chọn." if confident else "Cần thêm thông tin.",
    )


def _decision(action: str, **overrides: Any) -> ActionDecisionV4:
    data: dict[str, Any] = {
        "action": action,
        "reason": "Script golden action-only",
        "grouping_related_ticket_ids": None,
        "question_text": None,
        "question_options": None,
        "allow_free_text_fallback": False,
    }
    data.update(overrides)
    return ActionDecisionV4(**data)


def _a1_scripts(case: dict[str, Any]):
    by_id, _ = _category_maps(case)
    ground = case["ground_truth"]
    extraction = ground["text_extraction"]
    names = [by_id[item] for item in extraction["text_category_ids"]]
    expected = ground.get("expected_exit_reason") or ground["expected_behavior"]
    severity = extraction["severity"]
    confident = expected in {"ANALYSIS_COMPLETE", "RED_FLAG"}
    texts = [
        _text(
            names,
            red=bool(extraction["red_flag_text"]),
            understandable=bool(extraction["text_understandable"]),
            severity=severity,
            confident=confident,
            fact=case["title"],
        )
    ]
    decisions = []
    if expected == "ASK_RESIDENT":
        decisions.append(_decision("ASK_RESIDENT", question_text="Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố."))
    elif expected == "ANALYSIS_COMPLETE":
        decisions.append(_decision("CONCLUDE"))
    return texts, decisions, []


def _a2_scripts(case: dict[str, Any]):
    _, by_code = _category_maps(case)
    issue_to_code = {
        "Rò nước": "WATER_LEAK",
        "Chập điện": "ELECTRICAL_SHORT",
        "Thấm tường": "STRUCTURAL_ISSUE",
        "Category khác": "ELEVATOR",
    }
    category = by_code[issue_to_code[case["dimensions"]["Loại vấn đề"]]]
    expected = case["ground_truth"]["expected_behavior"]
    texts = [_text([category], severity="MEDIUM", confident=True, fact=case["title"])]
    duplicate_hits = []
    grouping_hits = []
    for item in case["input"].get("tool_script") or []:
        if item.get("tool") != "search_related_tickets":
            continue
        hits = item["response"].get("related_tickets") or []
        if item.get("purpose") == "DUPLICATE":
            duplicate_hits = hits
        elif item.get("purpose") == "GROUPING":
            grouping_hits = hits

    judgements: list[DuplicateJudgementV4] = []
    if duplicate_hits:
        same = expected in {"DUPLICATE_EXISTING", "DUPLICATE_UNCERTAIN"}
        judgements.append(
            DuplicateJudgementV4(
                verdict="SAME_INCIDENT" if same else "DIFFERENT_INCIDENT",
                master_ticket_id=str(duplicate_hits[0]["ticket_id"]) if same else None,
                reason="Script xác định cùng sự cố." if same else "Script xác định khác biểu hiện.",
            )
        )

    decisions: list[ActionDecisionV4] = []
    if expected not in {"DUPLICATE_EXISTING", "DUPLICATE_UNCERTAIN"}:
        if case["ground_truth"].get("grouping_allowed"):
            decisions.append(_decision("SEARCH_GROUPING"))
            if expected == "GROUPING_ACCEPTED":
                ids = [str(item["ticket_id"]) for item in grouping_hits]
                decisions.append(_decision("PROPOSE_GROUPING", grouping_related_ticket_ids=ids))
        decisions.append(_decision("CONCLUDE"))
    return texts, decisions, judgements


def _category_for_description(description: str, by_code: dict[str, str]) -> str:
    lowered = description.lower()
    if "thang máy" in lowered:
        return by_code["ELEVATOR"]
    if "nước" in lowered or "ngập" in lowered:
        return by_code["WATER_LEAK"]
    if "ngất" in lowered or "gây rối" in lowered or "đánh nhau" in lowered:
        return by_code["SERIOUS_SECURITY_DISORDER"]
    if "cửa" in lowered or "khóa" in lowered:
        return by_code["LOCK_DOOR"]
    return by_code["ELECTRICAL_SHORT"]


def _a3_scripts(case: dict[str, Any]):
    _, by_code = _category_maps(case)
    case_id = case["case_id"]
    description = case["input"]["ticket"]["description"]
    category = _category_for_description(description, by_code)
    texts: list[TextExtractionV4]
    decisions: list[ActionDecisionV4] = []
    judgements: list[DuplicateJudgementV4] = []

    if case_id in {*(f"A3-{index:03d}" for index in range(1, 8)), "A3-018"}:
        texts = [_text([category], red=True, severity="HIGH", confident=True, fact=case["title"])]
    elif case_id == "A3-015":
        texts = [
            _text([by_code["ELECTRICAL_SHORT"]], severity=None, confident=False),
            _text([by_code["ELECTRICAL_SHORT"]], red=True, severity="HIGH", confident=True, fact="Có khói đen"),
        ]
        decisions = [_decision("ASK_RESIDENT", question_text="Hiện có khói, lửa hoặc mùi khét không?")]
    elif case_id == "A3-016":
        texts = [
            _text([by_code["ELEVATOR"]], severity="MEDIUM", confident=False),
            _text([by_code["ELEVATOR"]], red=True, severity="HIGH", confident=True, fact="Có người mắc kẹt"),
        ]
        decisions = [_decision("ASK_RESIDENT", question_text="Có người đang mắc kẹt trong thang máy không?")]
    elif case_id == "A3-019":
        texts = [
            _text([], severity=None, confident=False),
            _text([by_code["HVAC"]], severity="MEDIUM", confident=True, fact="Điều hòa không làm lạnh"),
        ]
        decisions = [_decision("ASK_RESIDENT", question_text="Thiết bị hoặc biểu hiện cụ thể là gì?"), _decision("CONCLUDE")]
    elif case_id == "A3-020":
        unknown = lambda: _text([], severity=None, confident=False)  # noqa: E731
        texts = [unknown(), unknown(), unknown(), _text([by_code["HVAC"]], severity="LOW", confident=True)]
        decisions = [
            _decision("ASK_RESIDENT", question_text="Bạn thấy thiết bị nào bất thường?"),
            _decision("ASK_RESIDENT", question_text="Bạn nghe, ngửi hoặc nhìn thấy biểu hiện gì?"),
            _decision("ASK_RESIDENT", question_text="Vui lòng xác nhận thiết bị đang không hoạt động."),
            _decision("CONCLUDE"),
        ]
    elif case_id == "A3-021":
        texts = [
            _text([], severity=None, confident=False),
            _text([], understandable=False, severity="LOW", confident=False),
        ]
        decisions = [_decision("ASK_RESIDENT", question_text="Thiết bị hoặc biểu hiện cụ thể là gì?"), _decision("CONCLUDE")]
    elif case_id == "A3-022":
        texts = [_text([], severity=None, confident=False), _text([], severity=None, confident=False)]
        decisions = [_decision("ASK_RESIDENT", question_text="Thiết bị hoặc biểu hiện cụ thể là gì?")]
    elif case_id == "A3-023":
        texts = [_text([by_code["LOCK_DOOR"]], severity="LOW", confident=True, fact=case["title"])]
        decisions = [_decision("CONCLUDE")]
    elif case_id == "A3-026":
        texts = [
            _text([by_code["ELEVATOR"]], severity="MEDIUM", confident=False, fact="Cửa rung và phát tiếng kêu"),
            _text([by_code["ELEVATOR"]], red=True, severity="HIGH", confident=True, fact="Có người mắc kẹt"),
        ]
        candidate = next(
            item
            for tool in case["input"]["tool_script"]
            for item in tool.get("response", {}).get("related_tickets", [])
        )
        judgements = [
            DuplicateJudgementV4(verdict="DIFFERENT_INCIDENT", master_ticket_id=None, reason="Biểu hiện ban đầu khác."),
            DuplicateJudgementV4(
                verdict="SAME_INCIDENT",
                master_ticket_id=str(candidate["ticket_id"]),
                reason="Thông tin bổ sung cho thấy cùng sự cố có người mắc kẹt.",
            ),
        ]
        decisions = [_decision("ASK_RESIDENT", question_text="Có người mắc kẹt bên trong không?")]
    else:
        raise AssertionError(f"Chưa có script A3 cho {case_id}")
    return texts, decisions, judgements


def _new_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_case(db, case: dict[str, Any]):
    ticket_data = case["input"]["ticket"]
    location_id = UUID(ticket_data["location_id"]) if ticket_data.get("location_id") else uuid4()
    building = Building(code="EVAL", name="Tòa nhà đánh giá")
    floor = Floor(building=building, floor_code="12", display_name="Tầng 12", adjacency_index=12)
    unit = Unit(building=building, floor=floor, unit_code="EVAL-1203")
    location_type = LocationType(code="EVAL_LOCATION", display_name="Vị trí đánh giá")
    location = Location(
        id=location_id,
        building=building,
        floor=floor,
        unit=unit,
        location_type=location_type,
        label=ticket_data.get("location_label") or "Vị trí đã chuẩn hóa",
    )
    user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT)
    resident = ResidentProfile(user=user, unit=unit, is_primary=True)
    categories = [
        CategoryCatalog(
            id=UUID(item["category_id"]),
            code=item["code"],
            display_name=item["display_name"],
            base_score=10,
            priority_ceiling=Priority.P3 if item["code"] == "SERIOUS_SECURITY_DISORDER" else None,
        )
        for item in case["input"]["category_catalog"]
    ]
    ticket = Ticket(
        id=UUID(ticket_data["ticket_id"]),
        reporter_user_id=user.user_id,
        source_unit=unit,
        location=location,
        description=ticket_data["description"],
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.PROCESSING,
        sla_started_at=FIXED_NOW,
    )
    db.add_all([building, floor, unit, location_type, location, user, resident, *categories, ticket])
    db.commit()
    backend = AgentBackendService(db)
    session = backend.start_session(ticket.id, model_version="agent-v4-action-eval")
    return resident, ticket, session, backend


def _graph_initial_state(backend: AgentBackendService, session, ticket: Ticket) -> AgentStateV4:
    """Build text-only graph state while the production attachment accessor is broken."""
    catalog = backend.get_category_catalog(session.id)
    location = ticket.location
    return AgentStateV4(
        ticket_id=str(ticket.id),
        session_id=str(session.id),
        description=ticket.description or "",
        building_label=location.building.name if location and location.building else "",
        floor_label=location.floor.floor_code if location and location.floor else "",
        location_label=location.label if location else "",
        location_id=str(ticket.location_id),
        image_paths=[],
        image_urls=[],
        model_version="agent-v4-action-eval",
        catalog=[item.model_dump() for item in catalog.categories],
        catalog_version=catalog.catalog_version,
        search_revision=0,
        incident_revision=0,
        judgement_revision=0,
        duplicate_candidates=[],
        duplicate_candidates_revision=NEVER_RAN,
        duplicate_searched_revision=NEVER_RAN,
        duplicate_judged_revision=NEVER_RAN,
        duplicate_verdict=None,
        grouping_candidates=[],
        grouping_candidates_revision=NEVER_RAN,
        grouping_searched_revision=NEVER_RAN,
        grouping_result_revision=NEVER_RAN,
        grouping_blocked_revision=NEVER_RAN,
        grouping_capability_blocked=False,
        grouping=None,
        symptom_facts=[],
        text_symptom_facts=[],
        image_symptom_facts=[],
        answer_notes=[],
        reextraction=False,
        red_flag_evidence=[],
        invalid_action_notes=[],
        technical_failure=None,
        dependency_gaps=[],
        tool_calls_used=0,
        ask_rounds_used=0,
        ask_elapsed_seconds=0,
        iterations=0,
    )


def _analysis_expected(case: dict[str, Any]) -> dict[str, Any]:
    ground = case["ground_truth"]
    expected = ground.get("expected_exit_reason") or ground.get("expected_behavior")
    if expected == "GROUPING_ACCEPTED":
        expected = "ANALYSIS_COMPLETE"
    return {
        "exit_reason": expected,
        "awaiting_resident": ground.get("expected_behavior") == "ASK_RESIDENT",
        "grouping_related_ticket_ids": ground.get("grouping_related_ticket_ids"),
        "duplicate_master_ticket_id": ground.get("duplicate_master_ticket_id"),
        "red_flag_relation": ground.get("red_flag_relation"),
        "severity": ground.get("severity") or (ground.get("text_extraction") or {}).get("severity"),
    }


def _check_analysis(case: dict[str, Any], state: dict[str, Any], questions: list[AIAgentQuestion]) -> list[str]:
    expected = _analysis_expected(case)
    errors: list[str] = []
    awaiting = "__interrupt__" in state and not state.get("result")
    if expected["awaiting_resident"]:
        if not awaiting:
            errors.append("Expected Agent dừng chờ Cư dân nhưng graph không interrupt.")
        if len(questions) != 1:
            errors.append(f"Expected 1 câu hỏi, actual {len(questions)}.")
        return errors

    result = state.get("result") or {}
    if result.get("exit_reason") != expected["exit_reason"]:
        errors.append(f"exit_reason expected={expected['exit_reason']} actual={result.get('exit_reason')}")

    ground = case["ground_truth"]
    if case["cluster_code"] == "A1":
        extraction = ground["text_extraction"]
        actual_categories = state.get("text_categories") or []
        if actual_categories != extraction["text_category_ids"]:
            errors.append(f"text_categories expected={extraction['text_category_ids']} actual={actual_categories}")
        if result and "text_understandable" in result:
            errors.append("Final payload làm lộ field nội bộ text_understandable.")

    if case["cluster_code"] == "A2":
        grouping = result.get("grouping")
        if ground["expected_behavior"] == "GROUPING_ACCEPTED":
            actual_ids = (grouping or {}).get("related_ticket_ids")
            if actual_ids != ground.get("grouping_related_ticket_ids"):
                errors.append(f"grouping ids expected={ground.get('grouping_related_ticket_ids')} actual={actual_ids}")
            if grouping and "density" in grouping:
                errors.append("AgentAnalysisResultV4 không được chứa density.")
        elif grouping is not None:
            errors.append(f"Expected grouping=null, actual={grouping}")
        if ground["expected_behavior"] == "DUPLICATE_EXISTING":
            actual_master = ((result.get("duplicate") or {}).get("master_ticket_id"))
            if actual_master != ground.get("duplicate_master_ticket_id"):
                errors.append(f"duplicate master expected={ground.get('duplicate_master_ticket_id')} actual={actual_master}")

    if case["cluster_code"] == "A3" and result:
        if result.get("severity") != ground.get("severity"):
            errors.append(f"severity expected={ground.get('severity')} actual={result.get('severity')}")
        if result.get("duplicate") is not None:
            errors.append("Red-flag/interaction case không được đóng thành duplicate.")
        expected_relation = ground.get("red_flag_relation")
        actual_relation = result.get("red_flag_relation")
        if expected_relation is None and actual_relation is not None:
            errors.append(f"Expected red_flag_relation=null, actual={actual_relation}")
        if expected_relation is not None and (actual_relation or {}).get("master_ticket_id") != expected_relation.get("master_ticket_id"):
            errors.append(f"red_flag_relation expected={expected_relation} actual={actual_relation}")
        expected_rounds = case["dimensions"].get("Số lượt hỏi Cư dân")
        if isinstance(expected_rounds, int) and len(questions) != expected_rounds:
            errors.append(f"resident rounds expected={expected_rounds} actual={len(questions)}")
    return errors


def run_analysis_case(case: dict[str, Any]) -> dict[str, Any]:
    engine, db = _new_db()
    try:
        resident, ticket, session, backend = _seed_case(db, case)
        if case["cluster_code"] == "A1":
            scripts = _a1_scripts(case)
        elif case["cluster_code"] == "A2":
            scripts = _a2_scripts(case)
        else:
            scripts = _a3_scripts(case)
        llm = ScriptedAnalysisLLM(*scripts)
        tools = ScriptedToolPort(backend, case["input"].get("tool_script") or [])
        graph = build_analysis_graph_v4(db, llm, tools, clock=lambda: FIXED_NOW)
        config = {"configurable": {"thread_id": str(session.id)}}
        state = graph.invoke(_graph_initial_state(backend, session, ticket), config=config)

        resident_script = case["input"].get("resident_script") or []
        for answer in resident_script:
            if "__interrupt__" not in state:
                break
            question = db.scalars(
                select(AIAgentQuestion)
                .where(AIAgentQuestion.session_id == session.id, AIAgentQuestion.status == "PENDING")
                .order_by(AIAgentQuestion.round_number.desc())
            ).first()
            if question is None:
                raise AssertionError("Graph interrupt nhưng không có câu hỏi PENDING")
            if answer.get("answer_text") is not None:
                backend.answer_question(
                    resident,
                    ticket.id,
                    question.id,
                    resident.user_id,
                    answer_type="FREE_TEXT",
                    answer_text=answer["answer_text"],
                )
            else:
                session.ask_resident_elapsed_seconds = int(answer.get("elapsed_seconds") or 300)
                db.commit()
            state = graph.invoke(Command(resume={"resumed": True}), config=config)

        questions = list(
            db.scalars(
                select(AIAgentQuestion)
                .where(AIAgentQuestion.session_id == session.id)
                .order_by(AIAgentQuestion.round_number)
            )
        )
        backend_calls = list(
            db.scalars(
                select(AIAgentToolCall)
                .where(AIAgentToolCall.session_id == session.id)
                .order_by(AIAgentToolCall.sequence)
            )
        )
        errors = _check_analysis(case, state, questions)
        actual = {
            "awaiting_resident": "__interrupt__" in state and not state.get("result"),
            "exit_reason": (state.get("result") or {}).get("exit_reason"),
            "result": state.get("result"),
            "technical_failure": state.get("technical_failure"),
            "dependency_gaps": state.get("dependency_gaps") or [],
            "questions": [
                {
                    "round": item.round_number,
                    "question_type": item.question_type,
                    "question_text": item.question_text,
                    "status": item.status,
                    "answer_type": item.answer_type,
                    "answer_text": item.answer_text,
                }
                for item in questions
            ],
            "llm_calls": llm.calls,
            "tool_calls": tools.calls,
            "backend_tool_log": [
                {
                    "sequence": item.sequence,
                    "tool_name": item.tool_name,
                    "request": item.sanitized_request,
                    "response": item.sanitized_response,
                    "success": item.success,
                }
                for item in backend_calls
            ],
            "unconsumed_scripts": {
                "text_extractions": len(llm.text_extractions),
                "decisions": len(llm.decisions),
                "duplicate_judgements": len(llm.duplicate_judgements),
            },
        }
        return {
            "case_id": case["case_id"],
            "cluster_code": case["cluster_code"],
            "title": case["title"],
            "status": "PASS" if not errors else "FAIL",
            "expected": _analysis_expected(case),
            "actual": actual,
            "mismatches": errors,
            "error": None,
        }
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _assignment_actual_business(mode: str, request, decisions: list[Any]) -> dict[str, str]:
    by_id = {str(item.decision_id): item for item in decisions}
    missing_result = "MANUAL_REQUIRED" if mode == "DIRECT" else "EMPTY"
    actual: dict[str, str] = {}
    for item in request.work_items:
        decision = by_id.get(str(item.decision_id))
        actual[str(item.decision_id)] = (
            "SELECTED" if decision is not None and decision.decision == "SELECTED" else missing_result
        )
    return actual


def run_assignment_case(case: dict[str, Any]) -> dict[str, Any]:
    mode = case["dimensions"]["Chế độ"]
    request_type = DirectAssignmentBatchRequestV4 if mode == "DIRECT" else AssignmentProposalBatchRequestV4
    request = request_type.model_validate(case["input"]["request_to_model"])
    primary = ScriptedAssignmentClient("primary", case["input"]["primary_model_script"])
    fallback_script = case["input"].get("fallback_model_script")
    fallback = ScriptedAssignmentClient("fallback", fallback_script) if fallback_script else None
    service = AssignmentAgentService(primary, fallback, clock=lambda: FIXED_NOW)
    outcome = service.decide_direct(request) if mode == "DIRECT" else service.decide_proposal(request)
    decisions = outcome.result.decisions
    actual_business = _assignment_actual_business(mode, request, decisions)
    actual_versions = {str(item.decision_id): item.model_version for item in decisions}
    ground = case["ground_truth"]
    expected_business = ground["expected_business_result_by_decision_id"]
    expected_versions = ground["expected_model_version_by_decision_id"]
    errors: list[str] = []
    if actual_business != expected_business:
        errors.append(f"business result expected={expected_business} actual={actual_business}")
    if actual_versions != expected_versions:
        errors.append(f"model versions expected={expected_versions} actual={actual_versions}")

    actual = {
        "mode": mode,
        "fallback_used": outcome.fallback_used,
        "decisions": [item.model_dump(mode="json") for item in decisions],
        "business_result_by_decision_id": actual_business,
        "model_version_by_decision_id": actual_versions,
        "failures": [
            {
                "decision_id": str(item.decision_id),
                "work_item_id": str(item.work_item_id),
                "error_code": item.error_code,
                "detail": item.error_detail,
            }
            for item in outcome.failures
        ],
        "model_calls": [*primary.calls, *(fallback.calls if fallback else [])],
    }
    return {
        "case_id": case["case_id"],
        "cluster_code": case["cluster_code"],
        "title": case["title"],
        "status": "PASS" if not errors else "FAIL",
        "expected": {
            "business_result_by_decision_id": expected_business,
            "model_version_by_decision_id": expected_versions,
            "fallback_scope": ground.get("fallback_scope") or [],
        },
        "actual": actual,
        "mismatches": errors,
        "error": None,
    }


def _selected(case: dict[str, Any]) -> bool:
    cluster = case["cluster_code"]
    if case.get("image_assets_pending"):
        return False
    if cluster == "A1":
        return case["case_id"] in A1_READY
    if cluster == "A2":
        return True
    if cluster == "A3":
        return case["case_id"] in A3_READY
    return case["case_id"] not in BACKEND_ONLY and case["input"].get("request_to_model") is not None


def _production_entrypoint_diagnostic(case: dict[str, Any]) -> dict[str, Any]:
    engine, db = _new_db()
    try:
        _resident, ticket, session, backend = _seed_case(db, case)
        try:
            _build_initial_state(backend, session, ticket)
        except Exception as exc:  # noqa: BLE001 - diagnostic must preserve the actual blocker
            return {
                "status": "BLOCKED",
                "stage": "src.agents.v4.service._build_initial_state",
                "error": f"{type(exc).__name__}: {exc}",
                "impact": "Entrypoint phân tích production dừng trước khi graph chạy.",
            }
        return {"status": "PASS", "stage": "src.agents.v4.service._build_initial_state", "error": None}
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _report(summary: dict[str, Any], results: list[dict[str, Any]], exclusions: list[dict[str, str]]) -> str:
    lines = [
        "# Báo cáo chạy Agent v4 — action-only",
        "",
        f"Thời điểm chạy: `{summary['run_at']}`",
        "",
        "Phạm vi: dùng extraction/judgement/model response đã script theo golden data; chạy code Agent hiện tại để đo routing, tool, pause/resume, duplicate, grouping và primary/fallback. Đây chưa phải điểm chất lượng suy luận của model và chưa áp rubric.",
        "",
        "## Tổng quan",
        "",
        f"- Đã chạy: **{summary['total_run']}** case",
        f"- PASS kỹ thuật: **{summary['passed']}**",
        f"- FAIL so với golden: **{summary['failed']}**",
        f"- ERROR khi thực thi: **{summary['errors']}**",
        f"- Chưa chạy trong phạm vi này: **{summary['excluded']}**",
        f"- Entrypoint production: **{summary['production_entrypoint_diagnostic']['status']}** — {summary['production_entrypoint_diagnostic'].get('error') or 'không có lỗi'}",
        "",
        "| Cụm | Chạy | PASS | FAIL | ERROR |",
        "|---|---:|---:|---:|---:|",
    ]
    for cluster, data in summary["by_cluster"].items():
        lines.append(f"| {cluster} | {data['total']} | {data['pass']} | {data['fail']} | {data['error']} |")
    lines += [
        "",
        "## Hành vi thực tế đã quan sát",
        "",
        "| Exit/hành vi Agent phân tích | Số case |",
        "|---|---:|",
    ]
    for name, count in summary["observed"]["analysis_outcomes"].items():
        lines.append(f"| {name} | {count} |")
    lines += ["", "| Tool call | Số lần |", "|---|---:|"]
    for name, count in summary["observed"]["tool_calls"].items():
        lines.append(f"| {name} | {count} |")
    lines += [
        "",
        f"- Tổng câu hỏi đã tạo: **{summary['observed']['resident_questions']}**",
        f"- Assignment dùng fallback: **{summary['observed']['assignment_fallback_cases']} / {summary['observed']['assignment_cases']}** case",
        f"- Assignment decisions hợp lệ trả ra: **{summary['observed']['assignment_decisions']}**",
        f"- Assignment failures còn lại sau fallback: **{summary['observed']['assignment_failures']}**",
    ]
    lines += ["", "## Case chưa khớp", ""]
    failing = [item for item in results if item["status"] != "PASS"]
    if not failing:
        lines.append("Không có.")
    else:
        for item in failing:
            detail = item.get("error") or "; ".join(item.get("mismatches") or [])
            lines.append(f"- `{item['case_id']}` — {item['title']}: {detail}")
    lines += [
        "",
        "## Dữ liệu để xây rubric",
        "",
        "Mỗi dòng JSONL giữ expected, actual final payload, chuỗi model-call đã script, tool-call thực tế, câu hỏi Cư dân, fallback và lỗi validation. Có thể dùng các trường này để định nghĩa rubric mà không phải suy ngược từ bảng tổng hợp.",
        "",
        "Các trục quan sát có sẵn: `exit_reason`, thứ tự/loại `tool_calls`, số lượt hỏi, `grouping`, `duplicate`, `red_flag_relation`, `severity`, `fallback_used`, `model_version_by_decision_id`, `failures`.",
        "",
        "## Case loại khỏi lần chạy",
        "",
        "- 29 case cần ảnh: chờ fixture ảnh từ người dùng.",
        "- 4 case Backend-only: pre-filter trước khi request tới Assignment Agent (`B1-005`, `B1-007`, `B2-005`, `B2-006`).",
        "",
        "Danh sách chi tiết:",
        "",
    ]
    lines.extend(f"- `{item['case_id']}` — {item['reason']}" for item in exclusions)
    return "\n".join(lines) + "\n"


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _short(value: Any) -> str:
    return str(value)[:8] if value else "—"


def _analysis_output_summary(item: dict[str, Any]) -> str:
    actual = item.get("actual") or {}
    result = actual.get("result") or {}
    extractions = [
        call["result"]
        for call in actual.get("llm_calls") or []
        if call.get("operation") == "extract_text"
    ]
    latest = extractions[-1] if extractions else {}
    exit_reason = result.get("exit_reason") or ("AWAITING_RESIDENT" if actual.get("awaiting_resident") else "—")
    categories = latest.get("text_categories") or []
    severity = result.get("severity") if result else latest.get("severity")
    parts = [
        f"exit={exit_reason}",
        f"category={','.join(categories) if categories else '[]'}",
        f"severity={severity or 'null'}",
        f"red_flag={result.get('red_flag_text', latest.get('red_flag_text'))}",
    ]
    grouping = result.get("grouping")
    duplicate = result.get("duplicate")
    relation = result.get("red_flag_relation")
    if grouping:
        parts.append("grouping=" + ",".join(_short(value) for value in grouping.get("related_ticket_ids") or []))
    if duplicate:
        parts.append("duplicate=" + _short(duplicate.get("master_ticket_id")))
    if relation:
        parts.append("red_relation=" + _short(relation.get("master_ticket_id")))
    return "; ".join(parts)


def _assignment_output_summary(item: dict[str, Any]) -> str:
    actual = item.get("actual") or {}
    decisions = actual.get("decisions") or []
    business = Counter((actual.get("business_result_by_decision_id") or {}).values())
    models = Counter((actual.get("model_version_by_decision_id") or {}).values())
    selected = [
        f"{_short(row.get('decision_id'))}→{_short(row.get('selected_technician_id'))}({row.get('model_version')})"
        for row in decisions
        if row.get("decision") == "SELECTED"
    ]
    parts = [
        "business=" + ",".join(f"{key}:{value}" for key, value in sorted(business.items())),
        "models=" + (",".join(f"{key}:{value}" for key, value in sorted(models.items())) or "none"),
    ]
    if selected:
        parts.append("selected=" + ", ".join(selected))
    failures = actual.get("failures") or []
    if failures:
        parts.append("failures=" + ",".join(row["error_code"] for row in failures))
    return "; ".join(parts)


def _detailed_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Kết quả đầu ra chi tiết — Agent v4 action-only",
        "",
        "Bảng dưới đây hiển thị output thực tế, không chỉ PASS/FAIL. UUID được rút gọn còn 8 ký tự để đọc nhanh; payload đầy đủ nằm trong `results.jsonl` và `case_results.tsv`.",
    ]
    cluster_names = {
        "A1": "Agent — Nội dung, ảnh và Category",
        "A2": "Agent — Grouping và sự cố đang xử lý",
        "A3": "Agent — Red flag và tương tác Cư dân",
        "B1": "LLM — DIRECT",
        "B2": "LLM — PROPOSAL",
    }
    for cluster in ("A1", "A2", "A3", "B1", "B2"):
        rows = [item for item in results if item["cluster_code"] == cluster]
        lines += ["", f"## {cluster} — {cluster_names[cluster]}", ""]
        if cluster in {"A1", "A2", "A3"}:
            lines += [
                "| Case | Tên case | Expected | Actual output | Action model | Tool sequence | Tương tác Cư dân |",
                "|---|---|---|---|---|---|---|",
            ]
            for item in rows:
                actual = item.get("actual") or {}
                expected = item.get("expected") or {}
                expected_text = f"exit={expected.get('exit_reason')}"
                if expected.get("severity") is not None:
                    expected_text += f"; severity={expected['severity']}"
                if expected.get("grouping_related_ticket_ids"):
                    expected_text += "; grouping=" + ",".join(
                        _short(value) for value in expected["grouping_related_ticket_ids"]
                    )
                if expected.get("duplicate_master_ticket_id"):
                    expected_text += "; duplicate=" + _short(expected["duplicate_master_ticket_id"])
                if expected.get("red_flag_relation"):
                    expected_text += "; red_relation=" + _short(expected["red_flag_relation"].get("master_ticket_id"))
                actions = [
                    call["result"]["action"]
                    for call in actual.get("llm_calls") or []
                    if call.get("operation") == "decide_next_action"
                ]
                tools = [
                    call["tool"] + (f":{call['purpose']}" if call.get("purpose") else "")
                    for call in actual.get("tool_calls") or []
                ]
                questions = actual.get("questions") or []
                resident = "; ".join(
                    f"vòng {row['round']}={row['status']}: {row['question_text']}"
                    + (f" → {row['answer_text']}" if row.get("answer_text") else "")
                    for row in questions
                ) or "—"
                values = [
                    f"`{item['case_id']}`",
                    item["title"],
                    expected_text,
                    _analysis_output_summary(item),
                    " → ".join(actions) or "—",
                    " → ".join(tools) or "—",
                    resident,
                ]
                lines.append("| " + " | ".join(_md_cell(value) for value in values) + " |")
        else:
            lines += [
                "| Case | Tên case | Expected business output | Actual output | Fallback | Failure còn lại |",
                "|---|---|---|---|---|---|",
            ]
            for item in rows:
                actual = item.get("actual") or {}
                expected_counts = Counter((item.get("expected") or {}).get("business_result_by_decision_id", {}).values())
                expected_text = ", ".join(f"{key}:{value}" for key, value in sorted(expected_counts.items()))
                failures = actual.get("failures") or []
                failure_text = "; ".join(
                    f"{row['error_code']}: {row['detail']}" for row in failures
                ) or "—"
                values = [
                    f"`{item['case_id']}`",
                    item["title"],
                    expected_text,
                    _assignment_output_summary(item),
                    "Có" if actual.get("fallback_used") else "Không",
                    failure_text,
                ]
                lines.append("| " + " | ".join(_md_cell(value) for value in values) + " |")
    return "\n".join(lines) + "\n"


def _tsv(results: list[dict[str, Any]]) -> str:
    columns = [
        "case_id",
        "cluster_code",
        "title",
        "status",
        "expected_output",
        "actual_output",
        "model_actions",
        "tool_sequence",
        "resident_interaction",
        "fallback_used",
        "failure_codes",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for item in results:
        actual = item.get("actual") or {}
        if item["cluster_code"] in {"A1", "A2", "A3"}:
            output = actual.get("result") or {
                "awaiting_resident": actual.get("awaiting_resident"),
                "questions": actual.get("questions") or [],
            }
        else:
            output = {
                "decisions": actual.get("decisions") or [],
                "business_result_by_decision_id": actual.get("business_result_by_decision_id") or {},
                "model_version_by_decision_id": actual.get("model_version_by_decision_id") or {},
                "failures": actual.get("failures") or [],
            }
        writer.writerow(
            {
                "case_id": item["case_id"],
                "cluster_code": item["cluster_code"],
                "title": item["title"],
                "status": item["status"],
                "expected_output": _compact_json(item.get("expected")),
                "actual_output": _compact_json(output),
                "model_actions": " -> ".join(
                    call["result"]["action"]
                    for call in actual.get("llm_calls") or []
                    if call.get("operation") == "decide_next_action"
                ),
                "tool_sequence": " -> ".join(
                    call["tool"] + (f":{call['purpose']}" if call.get("purpose") else "")
                    for call in actual.get("tool_calls") or []
                ),
                "resident_interaction": _compact_json(actual.get("questions") or []),
                "fallback_used": actual.get("fallback_used"),
                "failure_codes": ",".join(row["error_code"] for row in actual.get("failures") or []),
            }
        )
    return stream.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    selected = [case for case in payload["cases"] if _selected(case)]
    entrypoint_diagnostic = _production_entrypoint_diagnostic(
        next(case for case in selected if case["cluster_code"] in {"A1", "A2", "A3"})
    )
    exclusions: list[dict[str, str]] = []
    for case in payload["cases"]:
        if case in selected:
            continue
        reason = "Cần fixture ảnh" if case.get("image_assets_pending") else "Backend pre-filter, không vào Agent"
        exclusions.append({"case_id": case["case_id"], "reason": reason})

    results: list[dict[str, Any]] = []
    for case in selected:
        try:
            if case["cluster_code"] in {"A1", "A2", "A3"}:
                result = run_analysis_case(case)
            else:
                result = run_assignment_case(case)
        except Exception as exc:  # noqa: BLE001 - per-case raw evaluation result
            result = {
                "case_id": case["case_id"],
                "cluster_code": case["cluster_code"],
                "title": case["title"],
                "status": "ERROR",
                "expected": case["ground_truth"],
                "actual": None,
                "mismatches": [],
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        results.append(_jsonable(result))
        print(f"{result['status']:5} {case['case_id']} {case['title']}")

    grouped: dict[str, Counter] = defaultdict(Counter)
    status_counts = Counter(item["status"] for item in results)
    for item in results:
        grouped[item["cluster_code"]][item["status"].lower()] += 1
        grouped[item["cluster_code"]]["total"] += 1
    analysis_rows = [item for item in results if item["cluster_code"] in {"A1", "A2", "A3"}]
    assignment_rows = [item for item in results if item["cluster_code"] in {"B1", "B2"}]
    analysis_outcomes = Counter(
        item["actual"]["exit_reason"] or "AWAITING_RESIDENT" for item in analysis_rows if item.get("actual")
    )
    tool_calls = Counter()
    for item in analysis_rows:
        for call in (item.get("actual") or {}).get("tool_calls") or []:
            name = call["tool"]
            if call.get("purpose"):
                name += f":{call['purpose']}"
            tool_calls[name] += 1
    summary = {
        "dataset_version": payload.get("dataset_version"),
        "run_at": datetime.now(UTC).isoformat(),
        "scope": "agent-action-only-scripted-model-evidence",
        "total_run": len(results),
        "passed": status_counts["PASS"],
        "failed": status_counts["FAIL"],
        "errors": status_counts["ERROR"],
        "excluded": len(exclusions),
        "production_entrypoint_diagnostic": entrypoint_diagnostic,
        "by_cluster": {
            key: {
                "total": value["total"],
                "pass": value["pass"],
                "fail": value["fail"],
                "error": value["error"],
            }
            for key, value in sorted(grouped.items())
        },
        "observed": {
            "analysis_outcomes": dict(sorted(analysis_outcomes.items())),
            "tool_calls": dict(sorted(tool_calls.items())),
            "resident_questions": sum(
                len((item.get("actual") or {}).get("questions") or []) for item in analysis_rows
            ),
            "assignment_cases": len(assignment_rows),
            "assignment_fallback_cases": sum(
                bool((item.get("actual") or {}).get("fallback_used")) for item in assignment_rows
            ),
            "assignment_decisions": sum(
                len((item.get("actual") or {}).get("decisions") or []) for item in assignment_rows
            ),
            "assignment_failures": sum(
                len((item.get("actual") or {}).get("failures") or []) for item in assignment_rows
            ),
        },
        "excluded_cases": exclusions,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results), encoding="utf-8"
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "report.md").write_text(_report(summary, results, exclusions), encoding="utf-8")
    (args.output_dir / "detailed_results.md").write_text(_detailed_report(results), encoding="utf-8")
    (args.output_dir / "case_results.tsv").write_text(_tsv(results), encoding="utf-8-sig")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not status_counts["ERROR"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
