"""LangGraph wiring for the Agent v4 analysis pipeline.

Built as a second graph rather than an edit of `src.agents.graph`, so a V3
session that is currently parked on `interrupt()` waiting for a resident keeps
running on the graph and the checkpointer it started with.

Flow, in priority order (Logic_xử_lý_chính_v4 §4–§7):

    validate_input
      -> extract_text -> extract_image -> merge_extraction   (two sources, independent)
    merge_extraction
      -> judge_red_flag_relation -> exit_red_flag   red flag with candidates already in hand
      -> exit_red_flag            red flag on either source, checked before duplicate
      -> exit_insufficient        input cannot be understood (intake pass only)
      -> search_duplicates        DUPLICATE lookup for this evidence revision
      -> judge_duplicate          candidates in hand, judged against newer evidence
      -> decide_action
    search_duplicates -> judge_duplicate
      -> exit_duplicate_existing   SAME_INCIDENT
      -> exit_duplicate_uncertain  UNCERTAIN
      -> decide_action             DIFFERENT_INCIDENT
    decide_action
      -> search_grouping / propose_grouping -> decide_action
      -> ask_prepare -> ask_wait -> ask_finalize -> extract_text  (re-checks red flag)
      -> conclude: exit_insufficient / exit_limit / exit_analysis_complete

Two V3 exits are gone, and with them their nodes: `exit_confident`
(CONFIDENT_MATCH) and `exit_mismatch` (CATEGORY_MISMATCH). A normal extraction
round now ends at `exit_analysis_complete`; Backend compares the two Category
sources afterwards.

`abort_technical` is a seventh terminal that is deliberately *not* a business
exit: it ends the run with no `AgentAnalysisResultV4` at all, so a failing tool
can never be mistaken for a conclusion about the ticket.

Re-entry after a resident answer goes back through extraction, and
`merge_extraction` decides whether the evidence actually changed. If it did,
the revision advances and the duplicate/grouping lookups run again against the
new facts; if it did not, they are skipped. Budget counters are never reset.

`interrupt()` inside `ask_wait` parks the graph while the resident answers,
possibly minutes later across separate HTTP requests. The v4 checkpointer is
its own `MemorySaver` instance keyed by session_id, and — like V3 — is
in-process only; a durable checkpointer is Backend work outside this scope.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agents.v4.llm_client import AnalysisLLMClientV4
from src.agents.v4.nodes import AgentNodesV4, has_technical_failure, is_input_insufficient
from src.agents.v4.state import (
    BUDGET_MAX_ASK_ROUNDS,
    BUDGET_MAX_TOOL_CALLS,
    BUDGET_MAX_WAIT_SECONDS,
    AgentStateV4,
    budget_exhausted,
    needs_duplicate_judgement,
    needs_duplicate_search,
    severity_established,
)
from src.agents.v4.tools import AnalysisToolPortV4

# Deliberately not shared with the V3 graph: a thread id is a session id, and a
# V3 session must never resume into V4 state (or the reverse).
_CHECKPOINTER_V4 = MemorySaver()


def _has_red_flag(state: AgentStateV4) -> bool:
    return bool(state.get("red_flag_text")) or bool(state.get("red_flag_signal"))


def _limit_actually_reached(state: AgentStateV4) -> bool:
    """Contract §1.7.5: LIMIT_REACHED is only truthful when a real budget ran
    out. The loop-iteration safety net is not a business limit and must not be
    reported as one."""
    return (
        state.get("tool_calls_used", 0) >= BUDGET_MAX_TOOL_CALLS
        or state.get("ask_rounds_used", 0) >= BUDGET_MAX_ASK_ROUNDS
        or state.get("ask_elapsed_seconds", 0) >= BUDGET_MAX_WAIT_SECONDS
    )


def _red_flag_destination(state: AgentStateV4) -> str:
    """Where a red flag goes.

    Straight out when nothing was ever searched — a red flag stops all lookups
    (§6), so no search may be started now. But when a DUPLICATE search already
    ran earlier in this session and the evidence has moved since, the candidates
    in hand are re-read against the new evidence to decide whether §1.5a applies.
    """
    if needs_duplicate_judgement(state):
        return "judge_red_flag_relation"
    return "exit_red_flag"


def _route_after_extract_text(state: AgentStateV4) -> str:
    return "abort_technical" if has_technical_failure(state) else "extract_image"


def _route_after_extract_image(state: AgentStateV4) -> str:
    return "abort_technical" if has_technical_failure(state) else "merge_extraction"


def _route_after_extraction(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    # Red flag wins over everything, including duplicate (§6, §7.2.2).
    if _has_red_flag(state):
        return _red_flag_destination(state)
    # The intake check only gates the original submission; once the resident has
    # answered a targeted question, only the red-flag re-check applies.
    if not state.get("reextraction") and is_input_insufficient(state):
        return "exit_insufficient"
    if needs_duplicate_search(state) and not budget_exhausted(state):
        return "search_duplicates"
    if needs_duplicate_judgement(state):
        # Candidates found before the resident clarified; re-judge them against
        # the new evidence without spending another lookup.
        return "judge_duplicate"
    return "decide_action"


def _route_after_search_duplicates(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    return "judge_duplicate"


def _route_after_duplicate(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    verdict = state.get("duplicate_verdict")
    if verdict == "SAME_INCIDENT" and state.get("duplicate_master_ticket_id"):
        return "exit_duplicate_existing"
    if verdict == "UNCERTAIN":
        return "exit_duplicate_uncertain"
    return "decide_action"


def _route_conclude(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    if _has_red_flag(state):
        return _red_flag_destination(state)
    if is_input_insufficient(state):
        return "exit_insufficient"
    if not severity_established(state):
        # §1.7.7 requires a severity on every exit except INSUFFICIENT_INPUT, so
        # LIMIT_REACHED cannot carry a null one. Having asked the resident for
        # it while the budget allowed and still not having it, the honest exit
        # is that the report could not be understood well enough to score —
        # not a fabricated LOW.
        return "exit_insufficient"
    if not state.get("is_confident") and _limit_actually_reached(state):
        return "exit_limit"
    # Extraction finished. Whether the two Category sources agree is Backend's
    # question, not an exit reason.
    return "exit_analysis_complete"


def _route_after_decision(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    action = state.get("next_action")
    if action == "SEARCH_GROUPING":
        return "search_grouping"
    if action == "PROPOSE_GROUPING":
        return "propose_grouping"
    if action == "ASK_RESIDENT":
        return "ask_prepare"
    return _route_conclude(state)


def _route_after_tool(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    return "decide_action"


def _route_after_ask_prepare(state: AgentStateV4) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    if state.get("ask_prepare_failed"):
        return _route_conclude({**state, "is_confident": False})  # type: ignore[typeddict-item]
    return "ask_wait"


_CONCLUSION_NODES = [
    "exit_insufficient",
    "exit_limit",
    "exit_analysis_complete",
    "exit_red_flag",
    "judge_red_flag_relation",
    "abort_technical",
]

_TERMINALS = (
    "exit_red_flag",
    "exit_duplicate_existing",
    "exit_duplicate_uncertain",
    "exit_analysis_complete",
    "exit_limit",
    "exit_insufficient",
    "abort_technical",
)


def build_analysis_graph_v4(
    db,
    llm: AnalysisLLMClientV4,
    tools: AnalysisToolPortV4 | None = None,
    clock=None,
):
    """Compile the v4 analysis graph. V3 stays untouched and keeps its own.

    `llm`, `tools` and `clock` are all injectable so the graph can be exercised
    without a real model, a real Backend or a real wall clock.
    """
    nodes = AgentNodesV4(db, llm, tools, clock)
    graph = StateGraph(AgentStateV4)

    graph.add_node("validate_input", nodes.validate_input)
    graph.add_node("extract_text", nodes.extract_text)
    graph.add_node("extract_image", nodes.extract_image)
    graph.add_node("merge_extraction", nodes.merge_extraction)
    graph.add_node("search_duplicates", nodes.search_duplicates)
    graph.add_node("judge_duplicate", nodes.judge_duplicate)
    graph.add_node("judge_red_flag_relation", nodes.judge_red_flag_relation)
    graph.add_node("decide_action", nodes.decide_action)
    graph.add_node("search_grouping", nodes.search_grouping)
    graph.add_node("propose_grouping", nodes.propose_grouping)
    graph.add_node("ask_prepare", nodes.ask_prepare)
    graph.add_node("ask_wait", nodes.ask_wait)
    graph.add_node("ask_finalize", nodes.ask_finalize)
    graph.add_node("exit_red_flag", nodes.exit_red_flag)
    graph.add_node("exit_duplicate_existing", nodes.exit_duplicate_existing)
    graph.add_node("exit_duplicate_uncertain", nodes.exit_duplicate_uncertain)
    graph.add_node("exit_analysis_complete", nodes.exit_analysis_complete)
    graph.add_node("exit_limit", nodes.exit_limit)
    graph.add_node("exit_insufficient", nodes.exit_insufficient)
    graph.add_node("abort_technical", nodes.abort_technical)

    graph.set_entry_point("validate_input")
    graph.add_edge("validate_input", "extract_text")
    # An extraction that could not satisfy its own schema stops the run before
    # the next model call; nothing downstream may invent the missing field.
    graph.add_conditional_edges("extract_text", _route_after_extract_text, ["extract_image", "abort_technical"])
    graph.add_conditional_edges("extract_image", _route_after_extract_image, ["merge_extraction", "abort_technical"])

    graph.add_conditional_edges(
        "merge_extraction",
        _route_after_extraction,
        [
            "exit_red_flag",
            "judge_red_flag_relation",
            "exit_insufficient",
            "search_duplicates",
            "judge_duplicate",
            "decide_action",
            "abort_technical",
        ],
    )
    graph.add_conditional_edges(
        "search_duplicates",
        _route_after_search_duplicates,
        ["judge_duplicate", "abort_technical"],
    )
    graph.add_conditional_edges(
        "judge_duplicate",
        _route_after_duplicate,
        ["exit_duplicate_existing", "exit_duplicate_uncertain", "decide_action", "abort_technical"],
    )
    # The red-flag re-read never routes into a duplicate exit: a red flag keeps
    # the new ticket on its own P3 path (§1.5a).
    graph.add_edge("judge_red_flag_relation", "exit_red_flag")

    graph.add_conditional_edges(
        "decide_action",
        _route_after_decision,
        ["search_grouping", "propose_grouping", "ask_prepare", *_CONCLUSION_NODES],
    )
    graph.add_conditional_edges("search_grouping", _route_after_tool, ["decide_action", "abort_technical"])
    graph.add_conditional_edges("propose_grouping", _route_after_tool, ["decide_action", "abort_technical"])
    graph.add_conditional_edges("ask_prepare", _route_after_ask_prepare, ["ask_wait", *_CONCLUSION_NODES])
    graph.add_edge("ask_wait", "ask_finalize")
    graph.add_edge("ask_finalize", "extract_text")

    for terminal in _TERMINALS:
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=_CHECKPOINTER_V4)
