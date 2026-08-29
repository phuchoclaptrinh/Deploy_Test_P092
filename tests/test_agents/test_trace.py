"""Tests for the Agent JSONL tracing layer.

The two properties that matter operationally: a trace must never leak a signed
storage URL, and a failing tracer must never fail the analysis run.
"""

from __future__ import annotations

import json

import pytest

from src.agents.trace import AgentTracer, NullTracer, list_trace_files, redact_url, sanitize, truncate
from src.agents.tracing import TracingLLMClient, traced_node, traced_router

SIGNED_URL = (
    "https://gzxohdrfxfqguabbwxku.supabase.co/storage/v1/object/sign/"
    "ticket-attachments/2026/08/abc.jpg?token=eyJhbGciOiJIUzI1NiJ9.payload.signature"
)


def _tracer(tmp_path, **kwargs) -> AgentTracer:
    return AgentTracer(session_id="sess-1", ticket_id="tk-1", directory=tmp_path, **kwargs)


def _read(tracer: AgentTracer) -> list[dict]:
    return [json.loads(line) for line in tracer.path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- sanitization ----


def test_redact_url_drops_the_signature_but_keeps_the_object_path():
    redacted = redact_url(SIGNED_URL)
    assert "token=" not in redacted
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert redacted.endswith("ticket-attachments/2026/08/abc.jpg?<redacted>")


def test_redact_url_leaves_a_plain_path_alone():
    assert redact_url("2026/08/abc.jpg") == "2026/08/abc.jpg"


def test_truncate_reports_how_much_was_cut():
    assert truncate("abcdef", 3) == "abc…(+3 ký tự)"
    assert truncate("abc", 10) == "abc"
    assert truncate("abc", 0) == "abc"


def test_sanitize_redacts_urls_anywhere_in_the_payload():
    payload = {"image_urls": [SIGNED_URL], "nested": {"whatever": SIGNED_URL}}
    result = sanitize(payload, text_limit=500)
    assert "token=" not in json.dumps(result)


def test_sanitize_truncates_resident_prose_but_not_arbitrary_short_fields():
    payload = {"description": "x" * 100, "exit_reason": "CONFIDENT_MATCH"}
    result = sanitize(payload, text_limit=10)
    assert result["description"].startswith("x" * 10)
    assert "+90 ký tự" in result["description"]
    assert result["exit_reason"] == "CONFIDENT_MATCH"


def test_sanitize_summarizes_the_pinned_catalog_instead_of_dumping_it():
    payload = {"catalog": [{"category_id": "c1", "display_name": "Rò nước"}] * 40}
    assert sanitize(payload, text_limit=500)["catalog"] == {"category_count": 40}


# ---- writer ----


def test_emit_writes_one_json_object_per_line_with_increasing_seq(tmp_path):
    tracer = _tracer(tmp_path)
    tracer.emit("run_start", kind="run")
    tracer.emit("node_enter", node="classify")

    records = _read(tracer)
    assert [item["seq"] for item in records] == [1, 2]
    assert [item["event"] for item in records] == ["run_start", "node_enter"]
    assert all(item["session_id"] == "sess-1" and item["ticket_id"] == "tk-1" for item in records)
    assert all("ts" in item for item in records)


def test_emit_sanitizes_before_writing(tmp_path):
    tracer = _tracer(tmp_path, text_limit=20)
    tracer.emit("llm_request", call="classify", image_urls=[SIGNED_URL], description="y" * 200)

    raw = tracer.path.read_text(encoding="utf-8")
    assert "token=" not in raw
    assert "y" * 200 not in raw


def test_emit_appends_across_calls_so_a_resume_joins_the_same_file(tmp_path):
    first = _tracer(tmp_path)
    first.emit("run_start", kind="run")
    second = _tracer(tmp_path)  # a later request rebuilds the tracer
    second.emit("run_start", kind="resume")

    assert [item["event"] for item in _read(first)] == ["run_start", "run_start"]


def test_a_broken_destination_disables_tracing_instead_of_raising(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    tracer = AgentTracer(session_id="s", ticket_id="t", directory=blocker)

    tracer.emit("run_start")  # must not raise
    tracer.emit("run_end")

    assert tracer._disabled is True


def test_null_tracer_swallows_everything(tmp_path):
    NullTracer().emit("run_start", kind="run")
    assert list(tmp_path.iterdir()) == []


def test_list_trace_files_returns_newest_first(tmp_path):
    import os
    import time

    older = tmp_path / "old.jsonl"
    newer = tmp_path / "new.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    time.sleep(0.01)
    newer.write_text("{}\n", encoding="utf-8")
    os.utime(older, (1, 1))

    assert [item.name for item in list_trace_files(tmp_path)] == ["new.jsonl", "old.jsonl"]


def test_list_trace_files_on_a_missing_directory_is_empty(tmp_path):
    assert list_trace_files(tmp_path / "nope") == []


# ---- wrappers ----


def test_traced_node_records_entry_exit_and_returns_the_updates(tmp_path):
    tracer = _tracer(tmp_path)
    wrapped = traced_node("classify", lambda state: {"severity": "HIGH"}, tracer)

    assert wrapped({"iterations": 2}) == {"severity": "HIGH"}

    records = _read(tracer)
    assert [item["event"] for item in records] == ["node_enter", "node_exit"]
    assert records[0]["state"] == {"iterations": 2}
    assert records[1]["updates"] == {"severity": "HIGH"}
    assert records[1]["duration_ms"] >= 0


def test_traced_node_records_the_failure_and_re_raises(tmp_path):
    tracer = _tracer(tmp_path)

    def boom(state):
        raise ValueError("nổ")

    with pytest.raises(ValueError, match="nổ"):
        traced_node("classify", boom, tracer)({})

    error = _read(tracer)[-1]
    assert error["event"] == "node_error"
    assert error["error_type"] == "ValueError"


def test_traced_node_records_langgraph_control_flow_interrupts(tmp_path):
    """interrupt() unwinds via BaseException; the pause must still be visible."""
    tracer = _tracer(tmp_path)

    def paused(state):
        raise KeyboardInterrupt("giả lập interrupt()")

    with pytest.raises(KeyboardInterrupt):
        traced_node("tool_ask_wait", paused, tracer)({})

    assert _read(tracer)[-1]["event"] == "node_error"


def test_traced_router_records_the_branch_taken(tmp_path):
    tracer = _tracer(tmp_path)
    wrapped = traced_router("route_after_classify", lambda state: "exit_red_flag", tracer)

    assert wrapped({"red_flag_text": True}) == "exit_red_flag"

    record = _read(tracer)[-1]
    assert record["event"] == "route"
    assert record["router"] == "route_after_classify"
    assert record["target"] == "exit_red_flag"


class _FakeLLM:
    def __init__(self, classification):
        self._classification = classification

    def classify(self, **kwargs):
        return self._classification


def _classify(client):
    return client.classify(
        description="vòi nước rỉ",
        image_urls=[SIGNED_URL],
        catalog_names=["Rò nước"],
        location_label="Nhà tắm",
        floor_label="F03",
        unit_code="F0301",
        conversation=[],
    )


def test_tracing_llm_client_passes_the_result_through_and_records_it(tmp_path):
    from src.agents.llm_client import UnifiedClassification

    classification = UnifiedClassification(
        category="Rò nước",
        text_category="Rò nước",
        severity="MEDIUM",
        red_flag=False,
        understandable=True,
        ai_reason="Cư dân mô tả nước rỉ liên tục từ vòi trong nhà tắm.",
    )
    tracer = _tracer(tmp_path)
    client = TracingLLMClient(_FakeLLM(classification), tracer)

    returned = _classify(client)

    assert returned is classification
    records = _read(tracer)
    assert [item["event"] for item in records] == ["llm_request", "llm_response"]
    assert records[0]["image_count"] == 1
    assert "token=" not in json.dumps(records)
    assert records[1]["result"]["severity"] == "MEDIUM"


def test_tracing_llm_client_records_a_failed_call_and_re_raises(tmp_path):
    class _Boom:
        def classify(self, **kwargs):
            raise RuntimeError("hết quota")

    tracer = _tracer(tmp_path)
    with pytest.raises(RuntimeError, match="hết quota"):
        _classify(TracingLLMClient(_Boom(), tracer))

    record = _read(tracer)[-1]
    assert record["event"] == "llm_error"
    assert record["error_type"] == "RuntimeError"
