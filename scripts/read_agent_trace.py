"""Pretty-print an Agent v3 session trace written by src/agents/trace.py.

    python scripts/read_agent_trace.py --list          # sessions, newest first
    python scripts/read_agent_trace.py --last          # timeline of the newest
    python scripts/read_agent_trace.py --session <id>  # one specific session
    python scripts/read_agent_trace.py --last --raw    # the JSON lines as-is

The JSONL files stay the source of truth; this only renders them. Anything it
cannot parse is printed verbatim rather than skipped, so a truncated or
half-written last line is visible instead of silently disappearing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.agents.trace import list_trace_files  # noqa: E402

DEFAULT_DIR = REPO_ROOT / ".ai-log" / "agent"

# Marker per event type, so the shape of a run is visible while scrolling.
_MARKERS = {
    "run_start": "▶",
    "run_end": "■",
    "run_paused": "⏸",
    "run_error": "✖",
    "node_enter": "  →",
    "node_exit": "  ←",
    "node_error": "  ✖",
    "route": "  ⑂",
    "llm_request": "    ↗",
    "llm_response": "    ↘",
    "llm_error": "    ✖",
}


def _fmt_duration(record: dict) -> str:
    ms = record.get("duration_ms")
    return f" [{ms}ms]" if ms is not None else ""


def _fmt_detail(record: dict) -> str:
    event = record.get("event")
    if event in {"node_enter", "node_exit", "node_error"}:
        head = str(record.get("node", ""))
    elif event == "route":
        head = f"{record.get('router')} → {record.get('target')}"
    elif event in {"llm_request", "llm_response", "llm_error"}:
        head = str(record.get("call", ""))
    else:
        head = str(record.get("kind", ""))

    # Drop the keys already rendered in `head`, so nothing is printed twice.
    skip = {"ts", "seq", "session_id", "ticket_id", "event", "node", "router", "call", "duration_ms", "kind", "target"}
    payload = {key: value for key, value in record.items() if key not in skip}
    body = json.dumps(payload, ensure_ascii=False, default=str) if payload else ""
    return f"{head}{_fmt_duration(record)} {body}".rstrip()


def render(path: Path, *, raw: bool) -> None:
    print(f"# {path}")
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            if raw:
                print(line)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"  !! dòng {line_number} không phải JSON hợp lệ: {line[:160]}")
                continue
            marker = _MARKERS.get(str(record.get("event")), "  ·")
            timestamp = str(record.get("ts", ""))[11:23]
            print(f"{timestamp} {marker} {record.get('event')}: {_fmt_detail(record)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Đọc trace hành vi của Agent.")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="Thư mục chứa trace (mặc định .ai-log/agent).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="Liệt kê các session, mới nhất trước.")
    group.add_argument("--last", action="store_true", help="Hiển thị session mới nhất.")
    group.add_argument("--session", help="Hiển thị đúng một session theo id.")
    parser.add_argument("--raw", action="store_true", help="In nguyên các dòng JSON.")
    args = parser.parse_args()

    # Traces contain Vietnamese; a cp1252 console would otherwise raise.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    files = list_trace_files(args.dir)
    if not files:
        print(f"Không có trace nào trong {args.dir}.")
        return 1

    if args.session:
        target = Path(args.dir) / f"{args.session}.jsonl"
        if not target.is_file():
            print(f"Không tìm thấy trace cho session {args.session}.")
            return 1
        render(target, raw=args.raw)
        return 0

    if args.last:
        render(files[0], raw=args.raw)
        return 0

    for item in files:
        size_kb = item.stat().st_size / 1024
        print(f"{item.stem}  {size_kb:>8.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
