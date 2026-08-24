"""LangGraph wiring for Logic_xử_lý_chính_v3 (steps C through J).

    C  extract          -> C1 route_after_extract -> C2 exit_insufficient
                                                    -> D  (red flag?)
    D  route_after_extract -> D1 exit_red_flag
                            -> E  decide_action
    E  decide_action -> tool_search / tool_group / tool_ask_prepare -> loop back to E
                      -> (CONCLUDE) -> F confidence check -> F1 exit_limit
                                                            -> G route_final
    G  route_final -> exit_mismatch (CATEGORY_MISMATCH) / exit_confident (CONFIDENT_MATCH)

`interrupt()` inside tool_ask_wait pauses the graph while the resident
answers (possibly minutes later, across separate HTTP requests); the
MemorySaver checkpoint keeps state keyed by session_id (thread_id) until
`resume_ticket_analysis` continues it. See README §11 for the accepted
in-process-only limitation of this checkpointer.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agents.llm_client import AgentLLMClient
from src.agents.nodes import AgentNodes
from src.agents.state import AgentState
from src.agents.trace import NullTracer, Tracer
from src.agents.tracing import TracingLLMClient, traced_node, traced_router

_CHECKPOINTER = MemorySaver()


def _route_after_extract(state: AgentState) -> str:
    if state.get("reextraction"):
        # C1 (insufficient-input) only gates the original submission. After the
        # resident has already answered a targeted question, only the D
        # red-flag re-check applies (per "kiểm tra lại ở MỌI bước phía sau").
        return _route_red_flag(state)
    has_image = bool(state.get("image_urls"))
    image_irrelevant = has_image and state.get("is_relevant") is False
    text_insufficient_alone = not state.get("text_understandable", True) and not has_image
    if image_irrelevant or text_insufficient_alone:
        return "exit_insufficient"
    return _route_red_flag(state)


def _route_red_flag(state: AgentState) -> str:
    if state.get("red_flag_text") or state.get("red_flag_signal"):
        return "exit_red_flag"
    return "decide_action"


def _route_after_decision(state: AgentState) -> str:
    action = state.get("next_action")
    if action == "SEARCH_RELATED":
        return "tool_search"
    if action == "PROPOSE_GROUPING":
        return "tool_group"
    if action == "ASK_RESIDENT":
        return "tool_ask_prepare"
    return _route_conclude(state)


def _route_conclude(state: AgentState) -> str:
    if not state.get("is_confident"):
        return "exit_limit"
    # Dùng đúng quy tắc của AgentResultService._resolve_confident_category:
    # chỉ kết luận khi giải ra ĐÚNG MỘT Category, còn lại đẩy sang duyệt tay.
    # Nếu ở đây chỉ cần "giao khác rỗng" thì Agent sẽ báo CONFIDENT_MATCH cho
    # ca mà Backend vẫn cho là mơ hồ, khiến exit_reason trong audit log không
    # giải thích được kết quả thật của ticket.
    # Không cần lọc theo snapshot: _map_names_to_ids đã map từ chính catalog ra.
    text_ids = set(state.get("text_categories") or [])
    image_categories = state.get("image_categories")
    resolved = text_ids if image_categories is None else text_ids & set(image_categories)
    return "exit_confident" if len(resolved) == 1 else "exit_mismatch"


def _route_after_ask_prepare(state: AgentState) -> str:
    if state.get("ask_prepare_failed"):
        return _route_conclude({**state, "is_confident": False})
    return "tool_ask_wait"


def build_graph(db, llm: AgentLLMClient, tracer: Tracer | None = None):
    """Compile the pipeline, optionally wrapped in JSONL tracing.

    When `tracer` is omitted (or is a NullTracer) the graph is built from the
    bare callables, so a traced and an untraced run execute the same code with
    no per-node branching.
    """
    tracer = tracer or NullTracer()
    tracing = not isinstance(tracer, NullTracer)

    nodes = AgentNodes(db, TracingLLMClient(llm, tracer) if tracing else llm)
    graph = StateGraph(AgentState)

    def add(name: str, fn):
        graph.add_node(name, traced_node(name, fn, tracer) if tracing else fn)

    def route(name: str, fn):
        return traced_router(name, fn, tracer) if tracing else fn

    add("extract", nodes.extract)
    add("decide_action", nodes.decide_action)
    add("tool_search", nodes.tool_search)
    add("tool_group", nodes.tool_group)
    add("tool_ask_prepare", nodes.tool_ask_prepare)
    add("tool_ask_wait", nodes.tool_ask_wait)
    add("tool_ask_finalize", nodes.tool_ask_finalize)
    add("exit_red_flag", nodes.exit_red_flag)
    add("exit_insufficient", nodes.exit_insufficient)
    add("exit_limit", nodes.exit_limit)
    add("exit_mismatch", nodes.exit_mismatch)
    add("exit_confident", nodes.exit_confident)

    graph.set_entry_point("extract")
    graph.add_conditional_edges(
        "extract",
        route("route_after_extract", _route_after_extract),
        ["exit_insufficient", "exit_red_flag", "decide_action"],
    )
    graph.add_conditional_edges(
        "decide_action",
        route("route_after_decision", _route_after_decision),
        ["tool_search", "tool_group", "tool_ask_prepare", "exit_limit", "exit_confident", "exit_mismatch"],
    )
    graph.add_edge("tool_search", "decide_action")
    graph.add_edge("tool_group", "decide_action")
    graph.add_conditional_edges(
        "tool_ask_prepare",
        route("route_after_ask_prepare", _route_after_ask_prepare),
        ["tool_ask_wait", "exit_limit", "exit_confident", "exit_mismatch"],
    )
    graph.add_edge("tool_ask_wait", "tool_ask_finalize")
    graph.add_edge("tool_ask_finalize", "extract")
    graph.add_edge("exit_red_flag", END)
    graph.add_edge("exit_insufficient", END)
    graph.add_edge("exit_limit", END)
    graph.add_edge("exit_mismatch", END)
    graph.add_edge("exit_confident", END)

    return graph.compile(checkpointer=_CHECKPOINTER)
