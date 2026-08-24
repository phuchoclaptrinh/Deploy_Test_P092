"""LLM-facing structured contracts for the Agent v4 analysis pipeline.

Four separate calls instead of V3 two, because V4 needs each one scoped to what
it is allowed to see:

* `extract_text` sees the description and nothing else.
* `extract_image` sees the photos and nothing else. Splitting the two is how
  "Category from text and Category from image stay independent" is enforced
  structurally rather than by asking the model nicely.
* `judge_duplicate` sees the full sanitized evidence and returns one of three
  verdicts.
* `decide_next_action` picks the next tool.

The model reasons about Category using the human-readable display names from
the session catalog snapshot, never the Backend-internal `code`. Node code maps
display name → category_id afterwards, so a hallucinated name is dropped rather
than becoming an invented UUID.

The client is deliberately pure I/O plus one repair loop. Every policy decision
— is this action well formed, is this master inside the candidate list, is this
severity good enough to finalize — lives in the graph nodes, so it is decided
once and is testable without a model.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from src.agents.v4.prompts import (
    ACTION_DECISION_SYSTEM_PROMPT_V4,
    DUPLICATE_JUDGEMENT_SYSTEM_PROMPT_V4,
    EXTRACTION_REPAIR_HINT_V4,
    IMAGE_EXTRACTION_SYSTEM_PROMPT_V4,
    TEXT_EXTRACTION_SYSTEM_PROMPT_V4,
)

logger = logging.getLogger(__name__)

ActionV4 = Literal["SEARCH_GROUPING", "PROPOSE_GROUPING", "ASK_RESIDENT", "CONCLUDE"]
DuplicateVerdictV4 = Literal["SAME_INCIDENT", "DIFFERENT_INCIDENT", "UNCERTAIN"]
SeverityV4 = Literal["LOW", "MEDIUM", "HIGH"]

MAX_QUESTION_OPTIONS = 6
MAX_QUESTION_TEXT_LENGTH = 1000
MAX_EXTRACTION_ATTEMPTS = 2


class ExtractionContractError(RuntimeError):
    """The model could not produce a contract-valid extraction.

    A technical failure, not a business outcome: the run stops with no
    `AgentAnalysisResultV4` rather than inventing the missing field.
    """

    def __init__(self, schema_name: str, attempts: list[str]) -> None:
        super().__init__(f"{schema_name} invalid after {len(attempts)} attempt(s): {attempts}")
        self.schema_name = schema_name
        self.attempts = attempts


def _check_severity_invariants(severity: str | None, reason: str | None, red_flag: bool, flag_name: str) -> None:
    """Severity rules shared by both extraction passes.

    The red-flag rule exists because a red flag routes straight to the
    `RED_FLAG` exit, and contract §1.7.7 requires a severity on every exit
    except `INSUFFICIENT_INPUT`. Letting the model report danger without a
    severity would build a payload that Backend must reject — and the fix is
    never to invent HIGH, which no document authorises.
    """
    if red_flag and severity is None:
        raise ValueError(f"{flag_name}=true requires a severity; danger cannot be reported without a level.")
    if severity is None and not (reason or "").strip():
        raise ValueError("severity=null requires severity_unknown_reason naming the missing detail.")
    if severity is not None and (reason or "").strip():
        raise ValueError("severity_unknown_reason must be null once a severity is established.")


class TextExtractionV4(BaseModel):
    """Extraction from the resident description alone."""

    text_categories: list[str] = Field(default_factory=list, description="Category display names supported by the text.")
    red_flag_text: bool = Field(description="Danger in the text: smoke, fire, bare wire, wide flooding, fainting, disorder, trapped in lift.")
    text_understandable: bool = Field(description="Is the text alone enough to roughly understand the problem?")
    symptom_facts: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Short observable facts about the incident, e.g. 'thang máy dừng giữa tầng'. Used to notice when a clarification changed the problem.",
    )
    severity: SeverityV4 | None = Field(
        default=None,
        description="Severity implied by the text, or null when the text gives no basis at all for judging it. Never guess LOW. Required when red_flag_text is true.",
    )
    severity_unknown_reason: str | None = Field(
        default=None,
        max_length=300,
        description="Required when severity is null: the one concrete detail that is missing. Must be null otherwise.",
    )
    is_confident: bool = Field(description="Confident enough in Category and severity from the text to stop investigating.")
    # Required, not optional: the coordinator-facing note must always explain
    # the concrete evidence behind the Category/severity choice, not only fire
    # when the model is unsure. Capped well under the 500-char DB column
    # (`ai_analysis_runs.confidence_notes`) so this note plus the image note
    # can both survive the `" | ".join(...)[:500]` merge in
    # `merge_extraction` without either one being cut off mid-sentence.
    notes: str = Field(
        min_length=1,
        max_length=220,
        description=(
            "Always required, even when confident. 1-2 short sentences naming the concrete detail(s) in the "
            "text that justify the chosen Category and severity — not a restatement of the Category name. "
            "When not confident, also say what is missing or ambiguous."
        ),
    )

    @model_validator(mode="after")
    def validate_severity(self):
        _check_severity_invariants(self.severity, self.severity_unknown_reason, self.red_flag_text, "red_flag_text")
        return self


class ImageExtractionV4(BaseModel):
    """Extraction from the photos alone."""

    image_categories: list[str] = Field(default_factory=list, description="Category display names visible in the image.")
    red_flag_signal: bool = Field(description="Physically visible danger in the image.")
    is_relevant: bool = Field(description="Does the image actually show an apartment-building incident?")
    symptom_facts: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Short observable facts visible in the photo.",
    )
    severity: SeverityV4 | None = Field(
        default=None,
        description="Severity implied by what is visible, or null when the image is too unclear to judge. Never guess LOW. Required when red_flag_signal is true.",
    )
    severity_unknown_reason: str | None = Field(default=None, max_length=300)
    # See TextExtractionV4.notes: same reasoning, same 220-char budget so the
    # two merge safely into the 500-char DB column.
    notes: str = Field(
        min_length=1,
        max_length=220,
        description=(
            "Always required. 1-2 short sentences naming the concrete detail(s) visible in the image that "
            "justify the chosen Category and severity — not a restatement of the Category name. Note any "
            "remaining ambiguity in the image."
        ),
    )

    @model_validator(mode="after")
    def validate_severity(self):
        _check_severity_invariants(self.severity, self.severity_unknown_reason, self.red_flag_signal, "red_flag_signal")
        return self


class DuplicateJudgementV4(BaseModel):
    """Verdict on whether this report repeats one live incident (§1.5)."""

    verdict: DuplicateVerdictV4
    master_ticket_id: str | None = Field(
        default=None,
        description="Ticket id of the master, copied verbatim from the candidate list. Null unless verdict is SAME_INCIDENT.",
    )
    reason: str = Field(max_length=500, description="Why this is or is not the same incident.")


class ActionDecisionV4(BaseModel):
    """Which tool to use next, if any.

    The fields are shared across actions, so an action carrying fields that
    belong to a different action is a contract violation — see
    `validate_action_decision`.
    """

    action: ActionV4
    reason: str = Field(max_length=300)
    grouping_related_ticket_ids: list[str] | None = Field(
        default=None,
        description="Required for PROPOSE_GROUPING; must be a non-empty subset of the ids the GROUPING search returned. Null otherwise.",
    )
    question_text: str | None = Field(
        default=None,
        description="Required for ASK_RESIDENT, at most 1000 characters. Null otherwise.",
    )
    question_options: list[str] | None = Field(
        default=None,
        description="Only for ASK_RESIDENT: 1 to 6 non-empty, distinct options. Null otherwise.",
    )
    allow_free_text_fallback: bool = Field(
        default=False,
        description="Only meaningful for ASK_RESIDENT. Must stay false for every other action.",
    )


def _absent_text(value: str | None) -> bool:
    return not (value or "").strip()


def _absent_list(value: list | None) -> bool:
    return not value


def validate_action_decision(
    decision: ActionDecisionV4,
    *,
    available_actions: list[str],
    grouping_candidate_ids: set[str],
) -> str | None:
    """Check one action decision against the rules for its own action.

    Every field is judged, not just the one the action needs: a `CONCLUDE`
    carrying a half-written question, or a `SEARCH_GROUPING` carrying ticket
    ids, means the model was not answering the question that was asked.

    Returns a violation message, or None when the decision is usable. Nothing
    is repaired here: filling in a missing question, trimming an over-long one
    or inventing a grouping id would be the Agent fabricating business data on
    the model's behalf.
    """
    action = decision.action
    if action not in available_actions:
        return f"Action {action} was not offered in this round."

    ids = decision.grouping_related_ticket_ids
    question_text = decision.question_text
    options = decision.question_options
    allow_free_text = decision.allow_free_text_fallback

    if action == "PROPOSE_GROUPING":
        if _absent_list(ids):
            return "PROPOSE_GROUPING requires a non-empty grouping_related_ticket_ids list."
        if len(set(ids)) != len(ids):
            return "PROPOSE_GROUPING repeated a ticket id."
        outside = [item for item in ids if item not in grouping_candidate_ids]
        if outside:
            return f"PROPOSE_GROUPING named ticket ids outside the current GROUPING search result: {outside}."
        if not _absent_text(question_text):
            return "PROPOSE_GROUPING must not carry question_text."
        if not _absent_list(options):
            return "PROPOSE_GROUPING must not carry question_options."
        if allow_free_text:
            return "PROPOSE_GROUPING must not set allow_free_text_fallback."
        return None

    if action == "ASK_RESIDENT":
        if _absent_text(question_text):
            return "ASK_RESIDENT requires question_text."
        trimmed = question_text.strip()
        if len(trimmed) > MAX_QUESTION_TEXT_LENGTH:
            # Truncating would send the resident a question the model did not
            # write, so the action is rejected and the model gets to redo it.
            return f"ASK_RESIDENT question_text is {len(trimmed)} characters; the limit is {MAX_QUESTION_TEXT_LENGTH}."
        if options is not None:
            if not options:
                return "ASK_RESIDENT question_options must be null rather than an empty list."
            if len(options) > MAX_QUESTION_OPTIONS:
                return f"ASK_RESIDENT allows at most {MAX_QUESTION_OPTIONS} options."
            cleaned = [item.strip() for item in options]
            if any(not item for item in cleaned):
                return "ASK_RESIDENT options must not be empty."
            if len({item.casefold() for item in cleaned}) != len(cleaned):
                return "ASK_RESIDENT options must be distinct."
        if not _absent_list(ids):
            return "ASK_RESIDENT must not carry grouping ids."
        return None

    # SEARCH_GROUPING and CONCLUDE take no payload of their own.
    if not _absent_list(ids):
        return f"{action} must not carry grouping ids."
    if not _absent_text(question_text):
        return f"{action} must not carry question_text."
    if not _absent_list(options):
        return f"{action} must not carry question_options."
    if allow_free_text:
        return f"{action} must not set allow_free_text_fallback; it is only meaningful for ASK_RESIDENT."
    return None


class AnalysisLLMClientV4(Protocol):
    def extract_text(
        self,
        *,
        description: str,
        catalog_names: list[str],
        context_notes: list[str],
    ) -> TextExtractionV4: ...

    def extract_image(
        self,
        *,
        image_urls: list[str],
        catalog_names: list[str],
    ) -> ImageExtractionV4: ...

    def judge_duplicate(self, *, evidence: dict[str, object], candidates: list[dict[str, object]]) -> DuplicateJudgementV4: ...

    def decide_next_action(
        self,
        *,
        description: str,
        text_category_names: list[str],
        image_category_names: list[str] | None,
        severity: str | None,
        severity_gap_note: str | None,
        is_confident: bool,
        confidence_notes: str | None,
        available_actions: list[ActionV4],
        grouping_candidates: list[dict[str, object]],
        budget_note: str,
        retry_note: str | None = None,
    ) -> ActionDecisionV4: ...


class OpenAIAnalysisLLMClientV4:
    """Default implementation backed by the configured chat model.

    `src.services.llm.get_llm()` already composes the provider-level fallback
    where it is configured, so binding a structured-output schema here gets the
    retry for free. Inject a different client to run the graph without a model.
    """

    def __init__(self, llm=None) -> None:
        if llm is None:
            from src.services.llm import get_llm

            llm = get_llm()
        self._llm = llm

    def _invoke_with_repair(self, schema, messages: list[dict[str, object]], *, repair_hint: str):
        """Ask once; on a contract violation, show the rule and ask again.

        A second failure is a technical failure, surfaced as
        `ExtractionContractError` — never smoothed over with a default value.
        """
        attempts: list[str] = []
        for attempt in range(MAX_EXTRACTION_ATTEMPTS):
            payload = messages if attempt == 0 else [*messages, {"role": "user", "content": repair_hint}]
            try:
                result = self._llm.with_structured_output(schema).invoke(payload)
            except Exception as exc:  # noqa: BLE001 - schema violations arrive in several shapes
                attempts.append(f"{type(exc).__name__}: {exc}")
                logger.warning("Agent v4 %s attempt %d rejected: %s", schema.__name__, attempt + 1, exc)
                continue
            if result is None:
                attempts.append("model returned no structured output")
                continue
            return result
        raise ExtractionContractError(schema.__name__, attempts)

    def extract_text(
        self,
        *,
        description: str,
        catalog_names: list[str],
        context_notes: list[str],
    ) -> TextExtractionV4:
        prompt = (
            f"Danh mục Category hợp lệ: {', '.join(catalog_names)}\n\n"
            f"Mô tả của cư dân: {description or '(không có mô tả)'}\n"
        )
        if context_notes:
            prompt += "\nThông tin bổ sung đã thu thập được:\n" + "\n".join(context_notes)
        messages = [
            {"role": "system", "content": TEXT_EXTRACTION_SYSTEM_PROMPT_V4},
            {"role": "user", "content": prompt},
        ]
        return self._invoke_with_repair(TextExtractionV4, messages, repair_hint=EXTRACTION_REPAIR_HINT_V4)

    def extract_image(
        self,
        *,
        image_urls: list[str],
        catalog_names: list[str],
    ) -> ImageExtractionV4:
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    f"Danh mục Category hợp lệ: {', '.join(catalog_names)}\n\n"
                    "Hãy đánh giá riêng những gì nhìn thấy trong ảnh dưới đây."
                ),
            }
        ]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": IMAGE_EXTRACTION_SYSTEM_PROMPT_V4},
            {"role": "user", "content": content},
        ]
        return self._invoke_with_repair(ImageExtractionV4, messages, repair_hint=EXTRACTION_REPAIR_HINT_V4)

    def judge_duplicate(self, *, evidence: dict[str, object], candidates: list[dict[str, object]]) -> DuplicateJudgementV4:
        structured = self._llm.with_structured_output(DuplicateJudgementV4)
        lines = [
            "Phản ánh mới:",
            f"- Mô tả gốc: {evidence.get('description') or '(không có)'}",
        ]
        for note in evidence.get("answer_notes") or []:
            lines.append(f"- Thông tin cư dân bổ sung: {note}")
        lines += [
            f"- Biểu hiện ghi nhận được: {', '.join(evidence.get('symptom_facts') or []) or '(không có)'}",
            f"- Category suy ra từ chữ: {', '.join(evidence.get('text_category_names') or []) or '(chưa xác định)'}",
            f"- Category suy ra từ ảnh: {evidence.get('image_category_names') if evidence.get('image_category_names') is not None else '(không có ảnh)'}",
            f"- Mức nghiêm trọng: {evidence.get('severity') or '(chưa xác định)'} (nguồn: {evidence.get('severity_source') or 'chưa có'})",
            f"- Dấu hiệu nguy hiểm ghi nhận được: {', '.join(evidence.get('red_flag_evidence') or []) or '(không có)'}",
            f"- Vị trí cư dân đã chọn: {evidence.get('location_label') or '(không rõ)'}",
            f"- Mã định danh tài sản/vị trí: {evidence.get('location_id') or '(không có)'}",
            "",
            "Ticket ứng viên do hệ thống tra cứu trả về (chỉ được chọn trong danh sách này):",
            str(candidates),
        ]
        messages = [
            {"role": "system", "content": DUPLICATE_JUDGEMENT_SYSTEM_PROMPT_V4},
            {"role": "user", "content": "\n".join(lines)},
        ]
        return structured.invoke(messages)

    def decide_next_action(
        self,
        *,
        description: str,
        text_category_names: list[str],
        image_category_names: list[str] | None,
        severity: str | None,
        severity_gap_note: str | None,
        is_confident: bool,
        confidence_notes: str | None,
        available_actions: list[ActionV4],
        grouping_candidates: list[dict[str, object]],
        budget_note: str,
        retry_note: str | None = None,
    ) -> ActionDecisionV4:
        structured = self._llm.with_structured_output(ActionDecisionV4)
        prompt = (
            f"Mô tả: {description}\n"
            f"Category từ chữ: {text_category_names}\n"
            f"Category từ ảnh: {image_category_names if image_category_names is not None else '(không có ảnh)'}\n"
            f"Mức nghiêm trọng hiện tại: {severity or 'CHƯA XÁC ĐỊNH'}\n"
            f"Vì sao chưa xác định được mức nghiêm trọng: {severity_gap_note or '(không có)'}\n"
            f"Đã đủ tự tin chưa: {is_confident}\n"
            f"Điều còn mơ hồ: {confidence_notes or '(không có)'}\n"
            f"Hành động được phép chọn: {available_actions}\n"
            f"Ticket ứng viên cho gộp cụm: {grouping_candidates}\n"
            f"Ngân sách còn lại: {budget_note}\n"
        )
        if retry_note:
            prompt += f"\nLượt trước bị từ chối vì: {retry_note}\nHãy trả lời lại cho đúng quy tắc của action bạn chọn.\n"
        messages = [
            {"role": "system", "content": ACTION_DECISION_SYSTEM_PROMPT_V4},
            {"role": "user", "content": prompt},
        ]
        return structured.invoke(messages)
