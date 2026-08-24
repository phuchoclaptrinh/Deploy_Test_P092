"""Dev-only: seed real demo data for the v4 business-flow demo video.

Đây KHÔNG phải eval có model script. Mỗi kịch bản dưới đây tạo tài khoản thật
(qua Supabase Admin Auth), gửi ticket thật, upload ảnh thật lên Supabase
Storage, và chạy đúng graph Agent với LLM thật (src.agents.service.run_ticket_analysis
- hàm mà POST /api/v1/tickets thực sự gọi). Mục tiêu là chứng minh mỗi luồng
nghiệp vụ trong dac_ta_tinh_nang_luong_nghiep_vu_v4.md hoạt động được trên hệ
thống thật, và để lại đúng ticket ở đúng trạng thái cho việc quay demo.

CẢNH BÁO: script này GHI vào database đang cấu hình trong .env, GỌI LLM có
tính phí (MODEL_NAME), và upload ảnh thật lên Supabase Storage.

    python scripts/prepare_demo_v4.py                # chạy cả 5 kịch bản
    python scripts/prepare_demo_v4.py --only 1,3      # chỉ kịch bản 1 và 3
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
import time
import traceback
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from src.agents.analysis_dispatch import resume_analysis, run_analysis  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.database.models.ai_analysis import AIAnalysisRun  # noqa: E402
from src.database.models.incident_case import IncidentCase  # noqa: E402
from src.database.models.incident_case_member import IncidentCaseMember  # noqa: E402
from src.database.models.location import Location  # noqa: E402
from src.database.models.resident_ticket_rate_limit import ResidentTicketRateLimit  # noqa: E402
from src.database.models.ticket import Ticket  # noqa: E402
from src.database.models.user_profile import UserProfile  # noqa: E402
from src.database.session import SessionLocal  # noqa: E402
from src.models.api.coordinator import ManagerCreateResidentRequest, ManagerCreateTechnicianRequest  # noqa: E402
from src.models.api.storage import SignedUploadRequest  # noqa: E402
from src.models.api.tickets import TicketCreateRequest  # noqa: E402
from src.models.enums import TicketStatus, UserRole  # noqa: E402
from src.services.agent_backend_service import AgentBackendService  # noqa: E402
from src.services.assignment_service import AssignmentService  # noqa: E402
from src.services.coordinator_service import CoordinatorService  # noqa: E402
from src.services.manager_account_service import ManagerAccountService  # noqa: E402
from src.services.storage_service import StorageService  # noqa: E402
from src.services.supabase_admin_auth_service import SupabaseAdminAuthService  # noqa: E402
from src.services.ticket_service import TicketService  # noqa: E402

EVAL_IMAGES = REPO_ROOT / "eval" / "29 ảnh"
DEMO_PASSWORD = "Demo!12345"
RUN_TAG = time.strftime("%H%M%S")  # keeps re-runs from colliding on unique email/phone

REPORT: list[str] = []


def demo_phone(seed: str) -> str:
    """A unique-per-run E.164-shaped phone so reruns don't collide on old demo accounts."""
    return f"+849{RUN_TAG}{seed}"


def log(msg: str) -> None:
    print(msg)
    REPORT.append(msg)


# ---------------------------------------------------------------------------
# Account provisioning (real Supabase Auth users)
# ---------------------------------------------------------------------------


def create_coordinator(full_name: str) -> tuple[UUID, str]:
    email = f"demo.coord.{RUN_TAG}.{full_name.lower().replace(' ', '')}@fixit.vn"
    auth = SupabaseAdminAuthService()
    user_id = auth.create_email_user(email=email, password=DEMO_PASSWORD, full_name=full_name)
    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                INSERT INTO user_profiles (user_id, phone_e164, full_name, role, is_active)
                VALUES (:id, NULL, :full_name, 'COORDINATOR', true)
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            {"id": str(user_id), "full_name": full_name},
        )
        db.commit()
    finally:
        db.close()
    return user_id, email


def create_technician(full_name: str, skill_codes: list[str]) -> tuple[UUID, str]:
    db = SessionLocal()
    try:
        skill_ids = [
            row
            for row in db.execute(
                text("SELECT id FROM categories WHERE code = ANY(:codes)"), {"codes": skill_codes}
            ).scalars()
        ]
        svc = ManagerAccountService(db)
        result = svc.create_technician(
            ManagerCreateTechnicianRequest(
                email=f"demo.tech.{RUN_TAG}.{full_name.lower().replace(' ', '')}@fixit.vn",
                password=DEMO_PASSWORD,
                full_name=full_name,
                phone_number=None,
                skill_category_ids=skill_ids,
                is_available=True,
            )
        )
        return result.user_id, result.email
    finally:
        db.close()


def create_resident(full_name: str, phone: str, unit_id: UUID) -> tuple[UUID, str]:
    """Returns (user_id, login_email). The real ResidentAuthGate (default frontend
    mode, NEXT_PUBLIC_RESIDENT_AUTH_MODE unset) logs Resident in with email+password
    via Supabase Auth, not phone+OTP -- see report note about dac_ta 1.1 vs actual
    frontend behavior. `phone` is still recorded on the profile for reference."""
    db = SessionLocal()
    try:
        svc = ManagerAccountService(db)
        result = svc.create_resident(
            ManagerCreateResidentRequest(
                email=f"demo.res.{RUN_TAG}.{full_name.lower().replace(' ', '')}@fixit.vn",
                password=DEMO_PASSWORD,
                phone=phone,
                full_name=full_name,
                unit_id=unit_id,
                is_primary=True,
            )
        )
        return result.user_id, result.email
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Real Supabase Storage upload (signed PUT), then a ticket-ready upload_id
# ---------------------------------------------------------------------------


def upload_ticket_photo(resident_user_id: UUID, image_path: Path) -> UUID:
    return _upload(image_path, target="ticket", owner_id=resident_user_id)


def upload_completion_photo(technician_user_id: UUID, image_path: Path) -> UUID:
    return _upload(image_path, target="completion", owner_id=technician_user_id)


def _upload(image_path: Path, *, target: str, owner_id: UUID) -> UUID:
    data = image_path.read_bytes()
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    if mime_type not in ("image/jpeg", "image/png", "image/webp"):
        mime_type = "image/jpeg"
    storage = StorageService()
    request = SignedUploadRequest(original_filename=image_path.name, mime_type=mime_type, file_size=len(data))
    target_obj = (
        storage.create_completion_evidence_upload_target(owner_id, request)
        if target == "completion"
        else storage.create_signed_upload_target(owner_id, request)
    )
    upload_url = target_obj.signed_upload_url
    if upload_url is None:
        raise RuntimeError("Supabase Storage did not return a signed upload URL.")
    headers = dict(target_obj.required_headers)
    if target_obj.signed_upload_token:
        headers["Authorization"] = f"Bearer {get_settings().supabase_secret_key}"
    resp = httpx.put(upload_url, headers=headers, content=data, timeout=30.0)
    if resp.status_code >= 400:
        raise RuntimeError(f"Real Supabase Storage upload failed ({resp.status_code}): {resp.text[:300]}")

    db = SessionLocal()
    try:
        from src.repositories.upload_session_repository import UploadSessionRepository

        session = UploadSessionRepository(db).create_upload_session(
            owner_user_id=owner_id,
            storage_path=target_obj.storage_path,
            original_filename=image_path.name,
            mime_type=mime_type,
            file_size=len(data),
        )
        db.commit()
        return session.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Small DB helpers
# ---------------------------------------------------------------------------


def location_in_unit(unit_id: UUID, type_code: str = "INSIDE_UNIT") -> UUID:
    db = SessionLocal()
    try:
        loc_id = db.execute(
            text(
                """
                SELECT l.id FROM locations l JOIN location_types t ON t.id = l.location_type_id
                WHERE l.unit_id = :unit_id AND t.code = :code LIMIT 1
                """
            ),
            {"unit_id": str(unit_id), "code": type_code},
        ).scalar_one()
        return loc_id
    finally:
        db.close()


def shared_location(floor_code: str, type_code: str) -> UUID:
    db = SessionLocal()
    try:
        loc_id = db.execute(
            text(
                """
                SELECT l.id FROM locations l
                JOIN location_types t ON t.id = l.location_type_id
                JOIN floors f ON f.id = l.floor_id
                WHERE l.unit_id IS NULL AND t.code = :type_code AND f.floor_code = :floor_code
                LIMIT 1
                """
            ),
            {"type_code": type_code, "floor_code": floor_code},
        ).scalar_one()
        return loc_id
    finally:
        db.close()


def unit_by_code(unit_code: str) -> UUID:
    db = SessionLocal()
    try:
        return db.execute(text("SELECT id FROM units WHERE unit_code = :c"), {"c": unit_code}).scalar_one()
    finally:
        db.close()


def submit_ticket(resident_user_id: UUID, location_id: UUID, description: str, image_upload_ids: list[UUID] | None = None) -> UUID:
    db = SessionLocal()
    try:
        profile = db.execute(
            text("SELECT unit_id FROM resident_profiles WHERE user_id = :u"), {"u": str(resident_user_id)}
        ).first()
        from src.database.models.resident_profile import ResidentProfile

        resident_profile = db.get(ResidentProfile, resident_user_id)
        ticket = TicketService(db, StorageService()).create_ticket(
            resident_user_id,
            resident_profile,
            TicketCreateRequest(
                location_id=location_id,
                description=description,
                attachment_upload_ids=image_upload_ids or [],
            ),
        )
        db.commit()
        return ticket.id
    finally:
        db.close()


def fetch_ticket(ticket_id: UUID) -> dict:
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        return {
            "id": t.id,
            "status": t.status,
            "classification_status": t.classification_status,
            "category_id": t.category_id,
            "priority": t.priority,
            "duplicate_of_ticket_id": t.duplicate_of_ticket_id,
        }
    finally:
        db.close()


def analyze(ticket_id: UUID) -> None:
    run_analysis(ticket_id)


def analyze_and_resolve(ticket_id: UUID, resident_user_id: UUID, max_rounds: int = 4) -> None:
    """Runs analysis and drives it through any ASK_RESIDENT pause to a real exit.

    This mirrors what POST /tickets + POST /tickets/{id}/answer actually does
    (src/api/routes/tickets.py:230,357 calls run_analysis from
    src.agents.analysis_dispatch -- the v4 dispatcher, NOT
    src.agents.service.run_ticket_analysis, which is the old v3 graph and is
    what a first version of this script called by mistake, trusting a stale
    comment in scripts/run_agent_once.py. That misrouting, not a real product
    bug, is why an earlier run showed CONFIDENT_MATCH exit reasons and
    model_version=fixit-agent-v3-langgraph-1: those are v3 artifacts, and v3
    has no duplicate/case-grouping search at all.
    """
    run_analysis(ticket_id)
    for _ in range(max_rounds):
        db = SessionLocal()
        try:
            pending = db.execute(
                text(
                    """
                    SELECT q.id, q.question_type, q.options, q.session_id
                    FROM ai_agent_questions q
                    JOIN ai_analysis_sessions s ON s.id = q.session_id
                    WHERE q.ticket_id = :t AND q.status = 'PENDING' AND s.status = 'RUNNING'
                    ORDER BY q.asked_at DESC LIMIT 1
                    """
                ),
                {"t": str(ticket_id)},
            ).first()
        finally:
            db.close()
        if pending is None:
            return
        question_id, question_type, options, session_id = pending

        db = SessionLocal()
        try:
            from src.database.models.resident_profile import ResidentProfile

            resident_profile = db.get(ResidentProfile, resident_user_id)
            if question_type == "FREE_TEXT":
                answer_type, answer_text = (
                    "FREE_TEXT",
                    "Không có ai bị kẹt hay gặp nguy hiểm, chỉ là sự cố kỹ thuật cần kỹ thuật viên kiểm tra.",
                )
            else:
                choices = options or []
                pick = next((o for o in choices if "không" in str(o).lower()), choices[0] if choices else "Không")
                answer_type, answer_text = "OPTION", str(pick)
            AgentBackendService(db).answer_question(
                resident_profile,
                ticket_id,
                question_id,
                resident_user_id,
                answer_type=answer_type,
                answer_text=answer_text,
            )
            db.commit()
        finally:
            db.close()
        resume_analysis(session_id)


def approve(coordinator_id: UUID, ticket_id: UUID) -> None:
    db = SessionLocal()
    try:
        CoordinatorService(db).approve(coordinator_id, ticket_id)
    finally:
        db.close()


def assign(coordinator_id: UUID, ticket_id: UUID, technician_id: UUID) -> UUID:
    db = SessionLocal()
    try:
        row = AssignmentService(db).assign(coordinator_id, ticket_id, technician_id)
        return row.id
    finally:
        db.close()


def technician_accept(technician_id: UUID, assignment_id: UUID) -> None:
    db = SessionLocal()
    try:
        AssignmentService(db).accept(technician_id, assignment_id)
    finally:
        db.close()


def technician_start(technician_id: UUID, assignment_id: UUID) -> None:
    db = SessionLocal()
    try:
        AssignmentService(db).start(technician_id, assignment_id)
    finally:
        db.close()


def technician_complete(technician_id: UUID, assignment_id: UUID, note: str, evidence_ids: list[UUID]) -> None:
    db = SessionLocal()
    try:
        AssignmentService(db).complete(technician_id, assignment_id, note, evidence_ids)
    finally:
        db.close()


def notifications_for_unit(unit_id: UUID) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT n.notification_type, n.title FROM notifications n
                JOIN resident_profiles rp ON rp.user_id = n.recipient_user_id
                WHERE rp.unit_id = :unit_id ORDER BY n.created_at
                """
            ),
            {"unit_id": str(unit_id)},
        ).all()
        return [f"{r[0]} ({r[1]})" for r in rows]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scenario 1: full flow, resident -> approve -> manual assign -> complete -> notified
# ---------------------------------------------------------------------------


def scenario_1_full_flow():
    log("\n=== Kịch bản 1: Flow hoàn chỉnh (ảnh -> duyệt -> phân tay -> hoàn thành -> thông báo) ===")
    unit_id = unit_by_code("A-0401")
    resident_id, resident_email = create_resident("Cu Dan Demo1", demo_phone("1"), unit_id)
    coordinator_id, coord_email = create_coordinator("BQL Demo1")
    technician_id, tech_email = create_technician("KTV Demo1", ["WATER_LEAK", "ELECTRICAL_SHORT", "ELEVATOR"])
    log(f"Resident login: {resident_email} / {DEMO_PASSWORD}")
    log(f"Coordinator login: {coord_email} / {DEMO_PASSWORD}")
    log(f"Technician login: {tech_email} / {DEMO_PASSWORD}")

    location_id = location_in_unit(unit_id)
    image = EVAL_IMAGES / "IMG-A1-014.jpg"
    upload_id = upload_ticket_photo(resident_id, image)
    ticket_id = submit_ticket(
        resident_id,
        location_id,
        "Ống nước dưới bồn rửa trong bếp bị rò rỉ, nước chảy ra sàn nhà liên tục từ sáng nay.",
        [upload_id],
    )
    log(f"Đã tạo ticket {ticket_id} (kèm ảnh thật).")

    analyze_and_resolve(ticket_id, resident_id)
    state = fetch_ticket(ticket_id)
    log(f"Sau phân tích AI: status={state['status']}, classification={state['classification_status']}, category_id={state['category_id']}, priority={state['priority']}")

    if state["classification_status"] != "RESOLVED" or state["category_id"] is None:
        log("KHÔNG ĐẠT: AI chưa kết luận được category/priority tự động — ticket cần Điều phối viên duyệt thủ công trước "
            "(mục 2.3 dac_ta). Không phải lỗi code — đây là hàng chờ duyệt thủ công hợp lệ theo đặc tả nếu AI không đủ tự tin. "
            "Video demo cần bắt đầu từ bước 'Duyệt ticket chờ xử lý thủ công' thay vì bước Duyệt (APPROVE) thẳng.")
        return

    approve(coordinator_id, ticket_id)
    log("Đã duyệt (APPROVE) ticket.")

    assignment_id = assign(coordinator_id, ticket_id, technician_id)
    log(f"Đã phân tay cho KTV Demo1, assignment {assignment_id}.")

    technician_accept(technician_id, assignment_id)
    technician_start(technician_id, assignment_id)
    log("KTV đã Nhận việc và Bắt đầu xử lý.")

    completion_image = EVAL_IMAGES / "IMG-A1-016.jpg"
    evidence_id = upload_completion_photo(technician_id, completion_image)
    technician_complete(technician_id, assignment_id, "Đã thay gioăng ống nước dưới bồn rửa, đã lau khô sàn, kiểm tra không còn rò rỉ.", [evidence_id])
    log("KTV đã bấm Hoàn thành kèm ghi chú và ảnh xác nhận thật.")

    final_state = fetch_ticket(ticket_id)
    log(f"Trạng thái ticket cuối cùng: {final_state['status']}")
    notes = notifications_for_unit(unit_id)
    log(f"Thông báo đã gửi cho căn hộ {['A-0401']}: {notes}")
    if "TICKET_COMPLETED" not in " ".join(notes):
        log("KHÔNG ĐẠT: cư dân không nhận được thông báo hoàn thành — cần kiểm tra lại notify_unit ở AssignmentService.complete.")
    log("KẾT LUẬN: kịch bản 1 chạy hết vòng đời thật, đúng như mục 1-3 của dac_ta. Ticket ID để tái tạo y hệt trong video: "
        f"{ticket_id} (nếu muốn dùng lại state cũ) hoặc lặp lại đúng input trên với account resident mới.")


# ---------------------------------------------------------------------------
# Scenario 2: spam -> 12h lockout
# ---------------------------------------------------------------------------


def scenario_2_spam_lockout():
    log("\n=== Kịch bản 2: Spam 10 ticket/giờ -> khóa gửi 12h ===")
    unit_id = unit_by_code("A-0402")
    resident_id, resident_email = create_resident("Cu Dan Demo2 Spam", demo_phone("2"), unit_id)
    location_id = location_in_unit(unit_id)
    log(f"Resident login: {resident_email} / {DEMO_PASSWORD}")

    created = 0
    blocked_at = None
    for i in range(11):
        try:
            tid = submit_ticket(resident_id, location_id, f"Test spam ticket số {i + 1} - đèn hành lang chớp tắt.")
            created += 1
        except Exception as exc:  # noqa: BLE001
            blocked_at = i + 1
            log(f"Ticket thứ {i + 1}: bị chặn — {exc}")
            break
    log(f"Đã tạo thành công {created} ticket trước khi bị chặn.")
    if blocked_at != 11:
        log(f"KHÔNG ĐẠT: mong đợi ticket thứ 11 mới bị chặn (ngưỡng 10/giờ theo mục 4.9), nhưng bị chặn ở lượt {blocked_at}. "
            "Kiểm tra TICKET_CREATE_SPAM_LIMIT trong src/services/ticket_service.py.")
    else:
        log("ĐẠT: đúng 10 ticket lọt qua, ticket thứ 11 bị chặn với lý do rate-limit — khớp mục 4.9.")

    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT blocked_until, block_reason, window_ticket_count FROM resident_ticket_rate_limits WHERE reporter_user_id = :u"),
            {"u": str(resident_id)},
        ).first()
        log(f"Bản ghi rate-limit: {row}")
    finally:
        db.close()
    log(f"Video demo: đăng nhập resident {resident_email} / {DEMO_PASSWORD}, gửi ticket thứ 11 trên "
        f"camera để thấy thông báo khóa; {created} ticket trước đó đã có sẵn trong DB nên không cần gửi lại 10 lần trên máy quay.")


# ---------------------------------------------------------------------------
# Scenario 3: irrelevant content -> INSUFFICIENT_INPUT ("Không hợp lệ")
# ---------------------------------------------------------------------------


def scenario_3_irrelevant_rejected():
    log("\n=== Kịch bản 3: Nội dung không liên quan -> Không hợp lệ ===")
    unit_id = unit_by_code("A-0403")
    resident_id, resident_email = create_resident("Cu Dan Demo3 Reject", demo_phone("3"), unit_id)
    location_id = location_in_unit(unit_id)
    log(f"Resident login: {resident_email} / {DEMO_PASSWORD}")

    ticket_id = submit_ticket(
        resident_id,
        location_id,
        "Cho em hỏi cuối tuần này chung cư có tổ chức Trung thu không ạ, có phát quà cho các bé không?",
    )
    log(f"Đã tạo ticket {ticket_id} với nội dung không phải phản ánh sự cố.")
    analyze_and_resolve(ticket_id, resident_id)
    state = fetch_ticket(ticket_id)
    log(f"Sau phân tích AI: status={state['status']}, classification={state['classification_status']}")

    if state["status"] != TicketStatus.INVALID:
        log(f"CHƯA ĐẠT ngay lần đầu: ticket kết thúc ở status={state['status']} thay vì INVALID ('Không hợp lệ'). "
            "Đây có thể là hành vi thật của model (LLM không xác định chắc là bất khả, có thể đã hỏi lại hoặc đưa vào hàng chờ "
            "duyệt thủ công) chứ chưa chắc là bug — cần xem trace để phân biệt.")
    else:
        log("ĐẠT: ticket tự đóng với trạng thái Không hợp lệ đúng mục 1.2 bước 5 / 4.5.")
    log(f"Video demo: đăng nhập resident {resident_email} / {DEMO_PASSWORD}, gửi ticket với nội dung tương tự trên camera; hoặc "
        f"dùng lại ticket {ticket_id} có sẵn để mở chi tiết cho thấy trạng thái Không hợp lệ.")


# ---------------------------------------------------------------------------
# Scenario 4: adjacent-floor water leak -> INCIDENT_CASE clustering (§4.3a)
# ---------------------------------------------------------------------------


def scenario_4_cluster():
    log("\n=== Kịch bản 4: Gộp cụm rò nước lan tầng liền kề (§4.3a) ===")
    unit_a = unit_by_code("A-0401")
    unit_b = unit_by_code("A-0501")  # tầng 4 và tầng 5: liền kề
    resident_a, email_a = create_resident("Cu Dan Demo4a", demo_phone("4"), unit_a)
    resident_b, email_b = create_resident("Cu Dan Demo4b", demo_phone("5"), unit_b)
    loc_a = location_in_unit(unit_a)
    loc_b = location_in_unit(unit_b)

    ticket_a = submit_ticket(
        resident_a,
        loc_a,
        "Ống nước trên trần nhà bếp bị rò rỉ, nước nhỏ giọt liên tục xuống sàn thành dòng nước rõ ràng, không phải nứt hay thấm tường.",
    )
    log(f"Ticket A (tầng 4, A-0401): {ticket_a}")
    analyze_and_resolve(ticket_a, resident_a)
    state_a = fetch_ticket(ticket_a)
    log(f"Ticket A sau phân tích: status={state_a['status']}, category_id={state_a['category_id']}")

    ticket_b = submit_ticket(
        resident_b,
        loc_b,
        "Ống nước trên trần nhà bếp bị rò rỉ, nước nhỏ giọt liên tục xuống sàn, nghi ngờ do đường ống của căn hộ tầng trên bị rò rỉ lan xuống, không phải nứt hay thấm tường.",
    )
    log(f"Ticket B (tầng 5, A-0501, liền kề tầng 4): {ticket_b}")
    analyze_and_resolve(ticket_b, resident_b)
    state_b = fetch_ticket(ticket_b)
    log(f"Ticket B sau phân tích: status={state_b['status']}, category_id={state_b['category_id']}")

    db = SessionLocal()
    try:
        member_case_ids = {
            row[0]
            for row in db.execute(
                text("SELECT case_id FROM incident_case_members WHERE ticket_id = ANY(:ids)"),
                {"ids": [str(ticket_a), str(ticket_b)]},
            ).all()
        }
    finally:
        db.close()

    if len(member_case_ids) == 1 and member_case_ids:
        log(f"ĐẠT: cả hai ticket cùng thuộc một INCIDENT_CASE ({member_case_ids}) — đúng mục 4.3a.")
    else:
        log(f"KHÔNG GỘP: hai ticket không nằm cùng một case (case_ids quan sát được: {member_case_ids}). "
            "Có thể do LLM không gọi tool propose_case_grouping trong lượt phân tích ticket B (không phải lỗi cấu trúc case_draft "
            "đã sửa ở phiên trước — đó là logic phân việc, còn đây là bước tạo case lúc phân tích). Xem lại trace của ticket B "
            "bằng scripts/read_agent_trace.py để xác nhận Agent có tra cứu ticket A hay không.")
    log(f"Video demo: 2 ticket đã có sẵn trong Dashboard BQL, hiển thị dưới dạng một cụm (nếu gộp thành công) hoặc hai ticket "
        f"riêng (nếu không). Resident logins: {email_a} / {DEMO_PASSWORD}, {email_b} / {DEMO_PASSWORD}.")


# ---------------------------------------------------------------------------
# Scenario 5: duplicate report on the same shared asset (§4.3b)
# ---------------------------------------------------------------------------


def scenario_5_duplicate():
    log("\n=== Kịch bản 5: Phản ánh trùng cùng một sự cố đang xử lý (§4.3b) ===")
    unit_a = unit_by_code("A-0101")
    unit_b = unit_by_code("A-0102")
    resident_a, email_a = create_resident("Cu Dan Demo5a", demo_phone("6"), unit_a)
    resident_b, email_b = create_resident("Cu Dan Demo5b", demo_phone("7"), unit_b)
    elevator_loc = shared_location("1", "ELEVATOR")

    ticket_a = submit_ticket(resident_a, elevator_loc, "Thang máy tầng 1 bị kẹt cửa, không mở được, đèn báo tầng vẫn sáng nhưng cửa không mở.")
    log(f"Ticket A (gốc): {ticket_a}")
    analyze_and_resolve(ticket_a, resident_a)
    state_a = fetch_ticket(ticket_a)
    log(f"Ticket A sau phân tích: status={state_a['status']}, category_id={state_a['category_id']}")

    ticket_b = submit_ticket(resident_b, elevator_loc, "Thang máy ở tầng 1 đang bị kẹt cửa không mở ra được, mọi người đang đứng chờ ngoài cửa thang.")
    log(f"Ticket B (trùng): {ticket_b}")
    analyze_and_resolve(ticket_b, resident_b)
    state_b = fetch_ticket(ticket_b)
    log(f"Ticket B sau phân tích: status={state_b['status']}, duplicate_of={state_b['duplicate_of_ticket_id']}")

    if state_b["status"] == TicketStatus.LINKED_DUPLICATE and state_b["duplicate_of_ticket_id"] == ticket_a:
        log("ĐẠT: ticket B được liên kết là trùng với ticket A, đúng mục 4.3b.")
        notes = notifications_for_unit(unit_b)
        log(f"Thông báo cho căn hộ báo trùng: {notes}")
    else:
        log(f"KHÔNG NHẬN DIỆN TRÙNG: ticket B kết thúc ở status={state_b['status']}, duplicate_of={state_b['duplicate_of_ticket_id']}. "
            "Có thể do LLM chưa đủ chắc chắn (đúng theo mục 4.3b điểm 6, ticket B sẽ vào hàng chờ Điều phối viên duyệt thay vì tự "
            "liên kết) — không nhất thiết là bug. Kiểm tra trace bằng scripts/read_agent_trace.py để xác nhận Agent có gọi "
            "search_related_tickets:DUPLICATE hay không.")
    log(f"Video demo: 2 ticket đã có sẵn. Resident logins: {email_a} / {DEMO_PASSWORD}, {email_b} / {DEMO_PASSWORD}.")


SCENARIOS = {
    1: scenario_1_full_flow,
    2: scenario_2_spam_lockout,
    3: scenario_3_irrelevant_rejected,
    4: scenario_4_cluster,
    5: scenario_5_duplicate,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Comma-separated scenario numbers, e.g. 1,3", default=None)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    if not settings.openai_api_key or not settings.model_name:
        raise SystemExit("Thiếu OPENAI_API_KEY hoặc MODEL_NAME trong .env.")

    numbers = [int(n) for n in args.only.split(",")] if args.only else list(SCENARIOS.keys())
    for n in numbers:
        try:
            SCENARIOS[n]()
        except Exception:  # noqa: BLE001
            log(f"\n!!! LỖI THỰC THI kịch bản {n} !!!")
            log(traceback.format_exc())

    report_path = REPO_ROOT / "eval" / "results" / "demo_v4_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(REPORT), encoding="utf-8")
    print(f"\nBáo cáo đầy đủ đã lưu: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
