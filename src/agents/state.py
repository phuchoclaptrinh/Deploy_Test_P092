"""LangGraph state for the FixIt Agent v3 pipeline .

Kept as plain JSON-serializable primitives (no ORM/Session objects) so the
state can be safely checkpointed by LangGraph across the pause between
E3 (ask_resident) and the resident's answer arriving in a later request.
"""

from __future__ import annotations

from typing import Literal, TypedDict

Severity = Literal["LOW", "MEDIUM", "HIGH"]
SeveritySource = Literal["IMAGE", "TEXT"]


class AgentState(TypedDict, total=False):
    # Fixed input for this analysis session.
    ticket_id: str
    session_id: str
    description: str
    floor_label: str
    location_label: str
    image_paths: list[str]
    image_urls: list[str]
    model_version: str

    # Category catalog pinned to this session (id/display_name/ceiling/base_score).
    catalog: list[dict[str, object]]
    catalog_version: str

    # C: initial (and re-run) extraction.
    text_categories: list[str]
    red_flag_text: bool
    text_understandable: bool
    image_categories: list[str] | None
    red_flag_signal: bool | None
    is_relevant: bool | None
    severity: Severity | None
    severity_source: SeveritySource | None
    is_confident: bool
    confidence_notes: str | None

    # E/F loop bookkeeping.
    related_tickets: list[dict[str, object]]
    grouping: dict[str, object] | None
    iterations: int
    pending_question_id: str | None
    answer_notes: list[str]
    reextraction: bool

    # E: the action chosen by decide_action for the tool-dispatch edge to read.
    next_action: str
    action_reason: str
    action_grouping_ticket_ids: list[str] | None
    action_question_text: str | None
    action_question_options: list[str] | None
    action_allow_free_text: bool
    ask_prepare_failed: bool

    # Routing outcome, set by the terminal node that ran.
    exit_reason: str | None
