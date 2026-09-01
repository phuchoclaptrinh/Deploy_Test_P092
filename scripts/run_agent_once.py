"""Dev-only: chạy trọn một lượt phân tích Agent với LLM thật, rồi in trace.

    python scripts/run_agent_once.py --description "Nước rò rỉ từ trần nhà tắm"
    python scripts/run_agent_once.py --ticket <ticket_id>     # chạy lại ticket có sẵn
    python scripts/run_agent_once.py --list-locations

Bỏ qua tầng HTTP/Supabase Auth và gọi thẳng `run_analysis`, tức là đúng
hàm mà `POST /api/v1/tickets` đẩy vào BackgroundTasks. Toàn bộ phần đang cần
kiểm chứng — graph, tool budget, LLM thật, tracing — chạy y hệt; thứ duy nhất
không đi qua là việc lấy access token của Cư dân.

CẢNH BÁO: script này GHI vào database đang cấu hình trong .env và GỌI LLM có
tính phí. Nó chạy đồng bộ, không qua BackgroundTasks, nên bạn thấy được lỗi
ngay tại chỗ thay vì phải đi tìm trong log server.

Nó cũng gọi `setup_tracing()` như FastAPI lifespan làm, nên khi có
`BRAINTRUST_API_KEY` (môi trường, `.env.braintrust` hoặc `.env`) mỗi lượt chạy
sẽ đẩy trọn một trace `analysis.run` kèm span con cho từng node và từng lời gọi
model lên Braintrust; không có key thì các span degrade thành no-op như cũ.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from src.agents.service import run_analysis, run_case_grouping  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.database.models.ai_agent_session import AIAnalysisSession  # noqa: E402
from src.database.models.location import Location  # noqa: E402
from src.database.models.resident_profile import ResidentProfile  # noqa: E402
from src.database.models.ticket import Ticket  # noqa: E402
from src.database.session import SessionLocal  # noqa: E402
from src.models.api.tickets import TicketCreateRequest  # noqa: E402
from src.observability import BRAINTRUST_PROJECT_ID, flush_traces, setup_tracing  # noqa: E402
from src.services.storage_service import StorageService  # noqa: E402
from src.services.ticket_service import TicketService  # noqa: E402


def _bound_resident(db) -> ResidentProfile:
    profile = db.scalars(select(ResidentProfile).where(ResidentProfile.unit_id.is_not(None)).limit(1)).first()
    if profile is None:
        raise SystemExit("Không có ResidentProfile nào đã liên kết căn hộ. Hãy bind unit trước.")
    return profile


def _pick_location(db, profile: ResidentProfile, location_id: str | None) -> Location:
    if location_id:
        location = db.get(Location, UUID(location_id))
        if location is None:
            raise SystemExit(f"Không tìm thấy location {location_id}.")
        return location
    # create_ticket từ chối location khác toà, hoặc location nằm trong một căn
    # hộ khác — lọc sẵn để script không chết vì lỗi hợp lệ hoá.
    location = db.scalars(
        select(Location)
        .where(Location.building_id == profile.unit.building_id)
        .where((Location.unit_id.is_(None)) | (Location.unit_id == profile.unit_id))
        .limit(1)
    ).first()
    if location is None:
        raise SystemExit("Không có Location hợp lệ cho căn hộ này.")
    return location


def _list_locations(db) -> int:
    profile = _bound_resident(db)
    rows = db.scalars(select(Location).where(Location.building_id == profile.unit.building_id)).all()
    for row in rows:
        scope = "toàn toà" if row.unit_id is None else ("căn hộ này" if row.unit_id == profile.unit_id else "căn hộ khác")
        print(f"{row.id}  {row.label:<30} ({scope})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Chạy một lượt phân tích Agent với LLM thật.")
    parser.add_argument("--description", help="Mô tả sự cố cho ticket mới.")
    parser.add_argument("--ticket", help="Chạy lại phân tích cho một ticket đã có.")
    parser.add_argument("--location-id", help="Chỉ định Location; mặc định tự chọn một cái hợp lệ.")
    parser.add_argument("--list-locations", action="store_true", help="Liệt kê Location hợp lệ rồi thoát.")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    if not settings.openai_api_key or not settings.model_name:
        raise SystemExit("Thiếu OPENAI_API_KEY hoặc MODEL_NAME trong .env.")

    # Same call FastAPI lifespan makes. Provider clients are built lazily per
    # run, so this runs early enough for the instrumentation to catch them.
    if setup_tracing():
        print(f"Braintrust: bật (project {BRAINTRUST_PROJECT_ID}).")
    else:
        print("Braintrust: tắt (không tìm thấy BRAINTRUST_API_KEY) — span degrade thành no-op.")

    db = SessionLocal()
    try:
        if args.list_locations:
            return _list_locations(db)

        if args.ticket:
            ticket_id = UUID(args.ticket)
            if db.get(Ticket, ticket_id) is None:
                raise SystemExit(f"Không tìm thấy ticket {ticket_id}.")
        else:
            if not args.description:
                raise SystemExit("Cần --description (ticket mới) hoặc --ticket (chạy lại ticket cũ).")
            profile = _bound_resident(db)
            location = _pick_location(db, profile, args.location_id)
            ticket = TicketService(db, StorageService()).create_ticket(
                profile.user_id,
                profile,
                TicketCreateRequest(location_id=location.id, description=args.description),
            )
            db.commit()
            ticket_id = ticket.id
            print(f"Đã tạo ticket {ticket_id} tại {location.label!r}.")
    finally:
        db.close()

    print(f"Model: {settings.model_name}. Đang chạy phân tích (đồng bộ, có thể mất vài chục giây)...")
    outcome = run_analysis(ticket_id)
    # Đúng thứ tự như route thật: vòng foreground xong mới tới bước gộp cụm nền.
    run_case_grouping(ticket_id)
    # `root_span` flushes per run in its own `finally`; this is the belt-and-
    # braces call a short-lived process should still make before it exits.
    flush_traces()
    if outcome.failed_technically:
        print(f"Lỗi kỹ thuật: {outcome.technical_failure}")

    # run_analysis không trả về session id; lấy phiên mới nhất của ticket.
    db = SessionLocal()
    try:
        session = db.scalars(
            select(AIAnalysisSession)
            .where(AIAnalysisSession.ticket_id == ticket_id)
            .order_by(AIAnalysisSession.started_at.desc())
            .limit(1)
        ).first()
        print()
        if session is None:
            print("Không có analysis session nào được tạo — xem log server để biết vì sao.")
            return 1
        print(f"Session {session.id}: status={session.status}, tool_calls={session.total_tool_calls}, ask_rounds={session.ask_resident_rounds}")
        print(f"Xem trace:  python scripts/read_agent_trace.py --session {session.id}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
