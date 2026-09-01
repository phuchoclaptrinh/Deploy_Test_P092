"""LangGraph wiring for the one ticket-analysis pipeline.

    classify
      -> abort_technical          the model could not satisfy its own schema
      -> exit_insufficient        the report cannot be understood
      -> ask_prepare              a Category / criterion / location confirmation
      -> warn_emergency           the ticket scores P5: warn, then keep going
      -> search_duplicates        Category, criteria and location are settled

    warn_emergency -> search_duplicates | judge_duplicate | exit_emergency_review
      -> judge_duplicate          a valid snapshot is already in hand
      -> exit_limit               the budget ran out mid-conversation

    search_duplicates -> judge_duplicate
      -> exit_duplicate_existing  SAME_INCIDENT
      -> exit_duplicate_uncertain UNCERTAIN
      -> exit_analysis_complete   DIFFERENT_INCIDENT
      -> ask_recent_completion    the match was closed within the hour

    ask_prepare -> ask_wait -> ask_finalize -> classify   (re-checks everything)
    ask_recent_completion -> ask_prepare -> ... -> settle_recent_completion

Every edge above is a plain function over the structured classification result.
There is no model call whose job is to pick the next step: routing is
deterministic, which is both one less round trip per round and one less thing
that can answer unpredictably.

The emergency check runs after every classification pass, including the ones
caused by a resident answer. P5 is the five-minute-SLA priority, and it takes a
path of its own: the warning is raised immediately, and *then* the round runs the
duplicate stage. Only grouping is skipped.

That ordering is deliberate and it is the reverse of v1, which stopped a P3 dead
before duplicate retrieval to save the minutes it costs. The minutes are now
spent after the alarm rather than before it, which costs the coordinator nothing
and answers the question that actually matters: is this the fourth person
reporting the fire we already know about, or a second fire? A confident duplicate
links to its master and pulls the master up to P5; anything less certain leaves
the ticket standing on its own at the gate. `docs/risk_scoring_v2.md` §7.1.

A downgrade re-enters this same graph at `search_duplicates` -- see
`service.resume_after_emergency_downgrade`.

There is no separate danger terminal any more. `exit_red_flag` existed because
v1 answered danger with a priority that bypassed scoring entirely; in v2 a fire
is the blocker code `FIRE_OR_SMOKE`, which floors the priority at P5 through the
same calculator every other ticket goes through and lands on the same exit. One
emergency path, not two.

Grouping is absent by design. It is a background stage that runs only after
duplicate processing is final and the resident has already been notified --
see `service.run_case_grouping`.

`abort_technical` is a terminal that is deliberately *not* a business exit: it
ends the run with no result at all, so a failing tool or a broken model reply
can never be mistaken for a conclusion about the ticket.

`interrupt()` inside `ask_wait` parks the graph while the resident answers,
possibly minutes later across separate HTTP requests. The checkpointer is a
`MemorySaver` keyed by session id and is in-process only; a durable
checkpointer is Backend work outside this scope.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agents.llm_client import AgentLLMClient
from src.agents.nodes import AgentNodes, duplicate_stage_ready
from src.agents.state import (
    AgentState,
    ask_budget_available,
    budget_actually_spent,
    duplicate_candidates_valid,
    emergency_review_required,
    has_technical_failure,
    input_insufficient,
    needs_duplicate_search,
)
from src.agents.trace import NullTracer, Tracer
from src.agents.tracing import TracingLLMClient, traced_node, traced_router

_CHECKPOINTER = MemorySaver()


def _route_after_classify(state: AgentState) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    if input_insufficient(state):
        return "exit_insufficient"
    if emergency_review_required(state) and not state.get("emergency_warned"):
        # Ahead of the question check on purpose. An emergency is answered by
        # speed, not by a clarification round: holding "someone is being
        # attacked in the hallway" open while the resident is asked to confirm
        # a location is not a trade anybody would make. `warn_emergency`
        # raises the alarm now and `_route_after_warn_emergency` then routes to
        # the gate -- a pending `requested_question` makes `duplicate_stage_ready`
        # false -- where a coordinator settles whatever the question was.
        return "warn_emergency"
    if state.get("requested_question"):
        if ask_budget_available(state):
            return "ask_prepare"
        # There is a question worth asking and no budget left to ask it. That
        # is what LIMIT_REACHED means; a coordinator finishes the job.
        return "exit_limit"
    if not duplicate_stage_ready(state):
        # The classification contract should refuse a readable report that
        # commits to no Category / no criteria and asks no question, but the
        # model still produces one occasionally. That is a broken payload, not
        # a spent budget: name it as the technical fault it is instead of
        # laundering it through a LIMIT_REACHED that finalize then rejects.
        # (If a budget genuinely ran out first, LIMIT_REACHED is still the
        # honest exit.)
        return "exit_limit" if budget_actually_spent(state) else "abort_technical"
    if needs_duplicate_search(state):
        return "search_duplicates"
    if duplicate_candidates_valid(state):
        # Candidates found before the resident clarified something that did not
        # move the evidence: re-judge them rather than paying for a new lookup.
        return "judge_duplicate"
    return "exit_analysis_complete"


def _route_after_warn_emergency(state: AgentState) -> str:
    """Straight into the duplicate stage, or to the gate if it cannot run.

    Deliberately not `ask_prepare`: the alarm has been raised, and holding an
    emergency open for three minutes waiting on a clarification is not a
    trade anybody would make. Whatever is missing, a coordinator settles it.
    """
    if has_technical_failure(state):
        return "abort_technical"
    if not duplicate_stage_ready(state):
        return "exit_emergency_review"
    if needs_duplicate_search(state):
        return "search_duplicates"
    if duplicate_candidates_valid(state):
        return "judge_duplicate"
    return "exit_emergency_review"


def _route_after_search(state: AgentState) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    return "judge_duplicate"


def _route_after_judgement(state: AgentState) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    if state.get("recent_completion_master_id"):
        if ask_budget_available(state):
            return "ask_recent_completion"
        # The recurrence question cannot be asked, so the one thing that would
        # settle it is missing. Auto-linking on a just-closed ticket without it
        # is exactly what the rule exists to prevent.
        return "exit_duplicate_uncertain"
    return _duplicate_outcome(state)


def _duplicate_outcome(state: AgentState) -> str:
    """Where a settled duplicate verdict goes, emergency or not.

    Shared by the judgement router and the recurrence router because the two
    reach the same three conclusions and drifting apart is how one of them ends
    up publishing an emergency.

    For a P5 the only verdict that changes the destination is a confident
    match: it links to the master, which is then pulled up to P5 as well.
    Uncertain and different both leave the ticket standing on its own at the
    gate -- an emergency nobody is sure about is still an emergency.
    """
    verdict = state.get("duplicate_verdict")
    emergency = emergency_review_required(state)
    if verdict == "SAME_INCIDENT" and state.get("duplicate_master_ticket_id"):
        return "exit_duplicate_existing"
    if emergency:
        return "exit_emergency_review"
    if verdict == "UNCERTAIN":
        return "exit_duplicate_uncertain"
    return "exit_analysis_complete"


def _route_after_ask_prepare(state: AgentState) -> str:
    if has_technical_failure(state):
        return "abort_technical"
    if state.get("ask_prepare_failed"):
        return "exit_limit" if budget_actually_spent(state) else "exit_analysis_complete"
    return "ask_wait"


def _route_after_ask_finalize(state: AgentState) -> str:
    """A recurrence answer settles the duplicate stage; anything else
    reclassifies with the enlarged evidence package."""
    if has_technical_failure(state):
        return "abort_technical"
    if state.get("recent_completion_master_id"):
        return "settle_recent_completion"
    return "classify"


def _route_after_recent_completion(state: AgentState) -> str:
    return _duplicate_outcome(state)


_TERMINALS = (
    "exit_emergency_review",
    "exit_duplicate_existing",
    "exit_duplicate_uncertain",
    "exit_analysis_complete",
    "exit_limit",
    "exit_insufficient",
    "abort_technical",
)


def build_graph(
    db,
    llm: AgentLLMClient,
    tracer: Tracer | None = None,
    clock=None,
    *,
    entry_point: str = "classify",
):
    """Compile the pipeline, optionally wrapped in JSONL tracing.

    When `tracer` is omitted (or is a `NullTracer`) the graph is built from the
    bare callables, so a traced and an untraced run execute the same code with
    no per-node branching.

    `entry_point` exists for one caller: a P3 downgrade resumes at
    `search_duplicates`, because the classification is already settled and
    re-running it could only produce the P3 a coordinator has just overruled.
    """
    tracer = tracer or NullTracer()
    tracing = not isinstance(tracer, NullTracer)

    nodes = AgentNodes(db, TracingLLMClient(llm, tracer) if tracing else llm, clock)
    graph = StateGraph(AgentState)

    def add(name: str, fn):
        graph.add_node(name, traced_node(name, fn, tracer) if tracing else fn)

    def route(name: str, fn):
        return traced_router(name, fn, tracer) if tracing else fn

    add("classify", nodes.classify)
    add("ask_prepare", nodes.ask_prepare)
    add("ask_wait", nodes.ask_wait)
    add("ask_finalize", nodes.ask_finalize)
    add("search_duplicates", nodes.search_duplicates)
    add("judge_duplicate", nodes.judge_duplicate)
    add("ask_recent_completion", nodes.ask_recent_completion)
    add("settle_recent_completion", nodes.settle_recent_completion)
    add("warn_emergency", nodes.warn_emergency)
    add("exit_emergency_review", nodes.exit_emergency_review)
    add("exit_duplicate_existing", nodes.exit_duplicate_existing)
    add("exit_duplicate_uncertain", nodes.exit_duplicate_uncertain)
    add("exit_analysis_complete", nodes.exit_analysis_complete)
    add("exit_limit", nodes.exit_limit)
    add("exit_insufficient", nodes.exit_insufficient)
    add("abort_technical", nodes.abort_technical)

    graph.set_entry_point(entry_point)
    graph.add_conditional_edges(
        "classify",
        route("route_after_classify", _route_after_classify),
        [
            "abort_technical",
            "warn_emergency",
            "exit_insufficient",
            "exit_limit",
            "ask_prepare",
            "search_duplicates",
            "judge_duplicate",
            "exit_analysis_complete",
        ],
    )
    graph.add_conditional_edges(
        "warn_emergency",
        route("route_after_warn_emergency", _route_after_warn_emergency),
        ["abort_technical", "search_duplicates", "judge_duplicate", "exit_emergency_review"],
    )
    graph.add_conditional_edges(
        "search_duplicates",
        route("route_after_search", _route_after_search),
        ["judge_duplicate", "abort_technical"],
    )
    graph.add_conditional_edges(
        "judge_duplicate",
        route("route_after_judgement", _route_after_judgement),
        [
            "ask_recent_completion",
            "exit_duplicate_existing",
            "exit_duplicate_uncertain",
            "exit_emergency_review",
            "exit_analysis_complete",
            "abort_technical",
        ],
    )
    graph.add_edge("ask_recent_completion", "ask_prepare")
    graph.add_conditional_edges(
        "ask_prepare",
        route("route_after_ask_prepare", _route_after_ask_prepare),
        ["ask_wait", "exit_limit", "exit_analysis_complete", "abort_technical"],
    )
    graph.add_edge("ask_wait", "ask_finalize")
    graph.add_conditional_edges(
        "ask_finalize",
        route("route_after_ask_finalize", _route_after_ask_finalize),
        ["classify", "settle_recent_completion", "abort_technical"],
    )
    graph.add_conditional_edges(
        "settle_recent_completion",
        route("route_after_recent_completion", _route_after_recent_completion),
        ["exit_duplicate_existing", "exit_duplicate_uncertain", "exit_emergency_review", "exit_analysis_complete"],
    )

    for terminal in _TERMINALS:
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=_CHECKPOINTER)


__all__ = ["build_graph"]
