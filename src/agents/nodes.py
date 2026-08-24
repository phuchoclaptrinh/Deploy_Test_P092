"""Graph nodes implementing steps C through J of Logic_xử_lý_chính_v3.

Each node closes over a single SQLAlchemy `Session` and the backend-owned
`AgentBackendService`, which is the trusted boundary that enforces the tool
budget (5 calls / 3 ask-resident rounds / 300s), validates every tool
request, and performs the final scoring/state mutation in `finalize()`. Node
code here only decides *what* to do; the Backend decides whether it is
allowed and what it means.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from langgraph.types import interrupt

from src.agents.llm_client import ActionDecision, AgentLLMClient, ExtractionResult
from src.agents.state import AgentState
from src.database.models.ai_agent_session import AIAgentQuestion
from src.models.agent_schemas import (
    AgentAnalysisResultV3,
    AgentExitReason,
    AgentGroupingResult,
    AgentSeveritySource,
    AgentToolUsage,
)
from src.models.api.errors import AGENT_BUDGET_EXHAUSTED, DomainError
from src.models.enums import Severity
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_common import MAX_ASK_ROUNDS, MAX_TOOL_CALLS, MAX_WAIT_SECONDS

logger = logging.getLogger(__name__)

MAX_LOOP_ITERATIONS = 12
GROUPING_DISPLAY_NAME_HINTS = {"rò nước", "chập điện", "water leak", "electrical short"}


def _map_names_to_ids(names: list[str] | None, catalog: list[dict[str, object]]) -> list[str] | None:
    if names is None:
        return None
    by_name = {str(item["display_name"]).strip().lower(): str(item["category_id"]) for item in catalog}
    mapped = []
    for name in names:
        category_id = by_name.get(name.strip().lower())
        if category_id and category_id not in mapped:
            mapped.append(category_id)
        elif not category_id:
            logger.warning("Agent returned Category name outside catalog snapshot: %r", name)
    return mapped


def _category_names(ids: list[str], catalog: list[dict[str, object]]) -> list[str]:
    by_id = {str(item["category_id"]): str(item["display_name"]) for item in catalog}
    return [by_id[item] for item in ids if item in by_id]


def _grouping_eligible(state: AgentState) -> bool:
    names = _category_names(state.get("text_categories") or [], state.get("catalog") or [])
    return any(name.strip().lower() in GROUPING_DISPLAY_NAME_HINTS for name in names)


class AgentNodes:
    def __init__(self, db, llm: AgentLLMClient) -> None:
        self.db = db
        self.backend = AgentBackendService(db)
        self.llm = llm

    # ---- C: extraction (also reused for the E3 re-extraction loop-back) ----

    def extract(self, state: AgentState) -> dict[str, object]:
        catalog = state["catalog"]
        catalog_names = [str(item["display_name"]) for item in catalog]
        result: ExtractionResult = self.llm.extract(
            description=state.get("description", ""),
            image_urls=state.get("image_urls") or [],
            catalog_names=catalog_names,
            context_notes=state.get("answer_notes") or [],
        )
        text_ids = _map_names_to_ids(result.text_categories, catalog) or []
        image_ids = _map_names_to_ids(result.image_categories, catalog)
        has_image = bool(state.get("image_urls"))
        # red_flag_signal và is_relevant khai báo default=None để phục vụ ca
        # không có ảnh, nên khi model bỏ trống chúng thì Pydantic điền None —
        # kể cả khi ticket CÓ ảnh. AgentAnalysisResultV3 lại bắt buộc hai
        # trường này khác None mỗi khi image_categories khác None, nên một lần
        # model quên trả lời sẽ làm cả ticket chết ở node kết thúc (đã gặp:
        # session 10052a14 hỏng ở exit_confident sau khi mọi bước trước đó
        # đều đúng). Chốt giá trị ngay tại đây để hợp đồng luôn nhất quán.
        red_flag_signal = bool(result.red_flag_signal) if has_image else None
        is_relevant = (result.is_relevant if result.is_relevant is not None else True) if has_image else None
        return {
            "text_categories": text_ids,
            "red_flag_text": result.red_flag_text,
            "text_understandable": result.text_understandable,
            "image_categories": image_ids if has_image else None,
            "red_flag_signal": red_flag_signal,
            "is_relevant": is_relevant,
            "severity": result.severity,
            "severity_source": "IMAGE" if has_image else "TEXT",
            "is_confident": result.is_confident and bool(text_ids),
            "confidence_notes": result.confidence_notes,
        }

    # ---- E: decide whether to use a tool, and which ----

    def decide_action(self, state: AgentState) -> dict[str, object]:
        session = self.backend._session(UUID(state["session_id"]))
        iterations = state.get("iterations", 0) + 1

        budget_exhausted = (
            session.total_tool_calls >= MAX_TOOL_CALLS
            or session.ask_resident_rounds >= MAX_ASK_ROUNDS
            or session.ask_resident_elapsed_seconds >= MAX_WAIT_SECONDS
            or iterations > MAX_LOOP_ITERATIONS
        )
        related_tickets = state.get("related_tickets") or []
        grouping_eligible = _grouping_eligible(state)

        if budget_exhausted:
            return {"next_action": "CONCLUDE", "iterations": iterations}

        available: list[str] = ["SEARCH_RELATED"]
        if grouping_eligible and related_tickets:
            available.append("PROPOSE_GROUPING")
        available.append("ASK_RESIDENT")
        available.append("CONCLUDE")

        decision: ActionDecision = self.llm.decide_next_action(
            description=state.get("description", ""),
            extraction=ExtractionResult(
                text_categories=_category_names(state.get("text_categories") or [], state["catalog"]),
                red_flag_text=state.get("red_flag_text", False),
                text_understandable=state.get("text_understandable", True),
                image_categories=(
                    _category_names(state["image_categories"], state["catalog"])
                    if state.get("image_categories") is not None
                    else None
                ),
                red_flag_signal=state.get("red_flag_signal"),
                is_relevant=state.get("is_relevant"),
                severity=state.get("severity") or Severity.LOW.value,
                severity_source=state.get("severity_source") or "TEXT",
                is_confident=state.get("is_confident", False),
                confidence_notes=state.get("confidence_notes"),
            ),
            available_actions=available,
            related_tickets=related_tickets,
            grouping_eligible=grouping_eligible,
            answer_notes=state.get("answer_notes") or [],
            ask_rounds_used=session.ask_resident_rounds,
            max_ask_rounds=MAX_ASK_ROUNDS,
        )
        return {
            "next_action": decision.action,
            "action_reason": decision.reason,
            "action_grouping_ticket_ids": decision.grouping_related_ticket_ids,
            "action_question_text": decision.question_text,
            "action_question_options": decision.question_options,
            "action_allow_free_text": decision.allow_free_text_fallback,
            "iterations": iterations,
        }

    # ---- E1: search_related_tickets ----

    def tool_search(self, state: AgentState) -> dict[str, object]:
        category_ids = state.get("text_categories") or []
        if state.get("image_categories"):
            category_ids = list({*category_ids, *state["image_categories"]})
        try:
            response = self.backend.search_related_tickets(
                UUID(state["session_id"]),
                ticket_id=UUID(state["ticket_id"]),
                category_ids=[UUID(item) for item in category_ids],
                floor=state.get("floor_label"),
                location=state.get("location_label"),
            )
        except DomainError:
            logger.warning("search_related_tickets rejected by Backend.", exc_info=True)
            return {}
        existing = {item["ticket_id"]: item for item in state.get("related_tickets") or []}
        for item in response["related_tickets"]:
            existing[item["ticket_id"]] = item
        return {"related_tickets": list(existing.values())}

    # ---- E2: propose_case_grouping (water leak / electrical short only) ----

    def tool_group(self, state: AgentState) -> dict[str, object]:
        candidate_ids = state.get("action_grouping_ticket_ids")
        if not candidate_ids:
            candidate_ids = [item["ticket_id"] for item in (state.get("related_tickets") or [])]
        if not candidate_ids:
            return {}
        try:
            response = self.backend.propose_case_grouping(
                UUID(state["session_id"]),
                ticket_id=UUID(state["ticket_id"]),
                related_ticket_ids=[UUID(item) for item in candidate_ids],
                reason=state.get("action_reason") or "Agent proposed grouping.",
            )
        except DomainError:
            logger.warning("propose_case_grouping rejected by Backend.", exc_info=True)
            return {}
        if not response["accepted"]:
            return {}
        return {
            "grouping": {
                "grouped": True,
                "density": response["density"],
                "related_ticket_ids": response["related_ticket_ids"],
                "category_id": response["category_id"],
                "reason": state.get("action_reason") or "Agent proposed grouping.",
            }
        }

    # ---- E3: ask_resident (split so the DB write never replays on resume) ----

    def tool_ask_prepare(self, state: AgentState) -> dict[str, object]:
        options = state.get("action_question_options")
        question_text = state.get("action_question_text") or "Bạn có thể mô tả rõ hơn vấn đề đang gặp phải không?"
        try:
            question = self.backend.create_question(
                UUID(state["session_id"]),
                ticket_id=UUID(state["ticket_id"]),
                question_type="MULTIPLE_CHOICE" if options else "FREE_TEXT",
                question_text=question_text,
                options=options,
                allow_free_text_fallback=bool(state.get("action_allow_free_text")) or not options,
            )
        except DomainError as exc:
            if exc.code != AGENT_BUDGET_EXHAUSTED:
                # Lỗi trạng thái/lập trình (session đã đóng, sai question_type,
                # trắc nghiệm thiếu options). Nuốt những lỗi này rồi báo
                # LIMIT_REACHED sẽ tạo ra kết quả mâu thuẫn với bộ đếm và bị
                # AgentAnalysisResultV3 từ chối, khiến ticket treo. Để nó nổ ra.
                raise
            logger.warning("ask_resident dừng lại: đã hết ngân sách hỏi cư dân.")
            return {"ask_prepare_failed": True}
        return {"pending_question_id": str(question.id), "ask_prepare_failed": False}

    def tool_ask_wait(self, state: AgentState) -> dict[str, object]:
        interrupt({"question_id": state.get("pending_question_id"), "reason": "waiting_for_resident_answer"})
        return {}

    def tool_ask_finalize(self, state: AgentState) -> dict[str, object]:
        question_id = state.get("pending_question_id")
        question = self.db.get(AIAgentQuestion, UUID(question_id)) if question_id else None
        if question is None or question.status != "ANSWERED":
            return {}
        notes = list(state.get("answer_notes") or [])
        updates: dict[str, object] = {}
        if question.answer_type == "NEW_PHOTO":
            attachment = self.backend.attachments.get_latest_resident_supplement(UUID(state["ticket_id"]))
            if attachment is not None:
                try:
                    signed_url = self.backend.storage.create_signed_download_url(attachment.object_path)
                    updates["image_urls"] = [*(state.get("image_urls") or []), signed_url]
                    updates["image_paths"] = [*(state.get("image_paths") or []), attachment.object_path]
                except DomainError:
                    logger.warning("Unable to sign resident-supplement image URL.", exc_info=True)
            notes.append(f"Cư dân đã gửi ảnh bổ sung (vòng {question.round_number}).")
        else:
            notes.append(f"Trả lời của cư dân (vòng {question.round_number}): {question.answer_text}")
        updates["answer_notes"] = notes
        updates["reextraction"] = True
        return updates

    # ---- Terminal nodes: build the v3 contract and call finalize() ----

    def _base_usage(self, session_id: str) -> AgentToolUsage:
        return self.backend._backend_tool_usage(self.backend._session(UUID(session_id)))

    def _build_result(
        self,
        state: AgentState,
        *,
        exit_reason: AgentExitReason,
        insufficient: bool = False,
    ) -> AgentAnalysisResultV3:
        session = self.backend._session(UUID(state["session_id"]))
        if insufficient:
            return AgentAnalysisResultV3(
                ticket_id=UUID(state["ticket_id"]),
                analysis_session_id=UUID(state["session_id"]),
                exit_reason=exit_reason,
                text_categories=None,
                red_flag_text=False,
                image_categories=None,
                red_flag_signal=None,
                is_relevant=state.get("is_relevant"),
                severity=None,
                severity_source=None,
                is_confident=False,
                confidence_notes=state.get("confidence_notes"),
                grouping=None,
                tool_usage=self._base_usage(state["session_id"]),
                category_catalog_version=session.category_catalog_version,
                model_version=state["model_version"],
                analyzed_at=datetime.now(UTC),
            )

        grouping = state.get("grouping")
        return AgentAnalysisResultV3(
            ticket_id=UUID(state["ticket_id"]),
            analysis_session_id=UUID(state["session_id"]),
            exit_reason=exit_reason,
            text_categories=[UUID(item) for item in (state.get("text_categories") or [])],
            red_flag_text=state.get("red_flag_text", False),
            image_categories=(
                [UUID(item) for item in state["image_categories"]] if state.get("image_categories") is not None else None
            ),
            red_flag_signal=state.get("red_flag_signal"),
            is_relevant=state.get("is_relevant"),
            severity=Severity(state["severity"]) if state.get("severity") else Severity.LOW,
            severity_source=AgentSeveritySource(state["severity_source"]) if state.get("severity_source") else AgentSeveritySource.TEXT,
            is_confident=exit_reason != AgentExitReason.LIMIT_REACHED and bool(state.get("is_confident")),
            confidence_notes=state.get("confidence_notes"),
            grouping=(
                AgentGroupingResult(
                    grouped=grouping["grouped"],
                    density=grouping["density"],
                    related_ticket_ids=[UUID(item) for item in grouping["related_ticket_ids"]],
                    reason=grouping["reason"],
                )
                if grouping
                else None
            ),
            tool_usage=self._base_usage(state["session_id"]),
            category_catalog_version=session.category_catalog_version,
            model_version=state["model_version"],
            analyzed_at=datetime.now(UTC),
        )

    def exit_red_flag(self, state: AgentState) -> dict[str, object]:
        result = self._build_result(state, exit_reason=AgentExitReason.RED_FLAG)
        self.backend.finalize(result)
        return {"exit_reason": AgentExitReason.RED_FLAG.value}

    def exit_insufficient(self, state: AgentState) -> dict[str, object]:
        result = self._build_result(state, exit_reason=AgentExitReason.INSUFFICIENT_INPUT, insufficient=True)
        self.backend.finalize(result)
        return {"exit_reason": AgentExitReason.INSUFFICIENT_INPUT.value}

    def exit_limit(self, state: AgentState) -> dict[str, object]:
        result = self._build_result(state, exit_reason=AgentExitReason.LIMIT_REACHED)
        self.backend.finalize(result)
        return {"exit_reason": AgentExitReason.LIMIT_REACHED.value}

    def exit_mismatch(self, state: AgentState) -> dict[str, object]:
        result = self._build_result(state, exit_reason=AgentExitReason.CATEGORY_MISMATCH)
        self.backend.finalize(result)
        return {"exit_reason": AgentExitReason.CATEGORY_MISMATCH.value}

    def exit_confident(self, state: AgentState) -> dict[str, object]:
        result = self._build_result(state, exit_reason=AgentExitReason.CONFIDENT_MATCH)
        self.backend.finalize(result)
        return {"exit_reason": AgentExitReason.CONFIDENT_MATCH.value}
