"use client";

import { ArrowRight, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";
import { listBackendAuditLogs } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { ManagerPagination } from "@/components/manager/ManagerPagination";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { formatTicketCode } from "@/lib/display";
import { formatDateTime } from "@/lib/mockService";
import type { BackendAuditLog } from "@/types/api";

const AUDIT_PAGE_SIZE = 8;
const actionLabels: Record<string, string> = {
  APPROVE_TICKET: "Duyệt phản ánh",
  REQUEST_INFORMATION: "Yêu cầu bổ sung",
  OVERRIDE_CLASSIFICATION: "Điều chỉnh phân loại",
  ASSIGN_TECHNICIAN: "Phân công kỹ thuật viên",
  START_ASSIGNMENT: "Bắt đầu xử lý",
  COMPLETE_ASSIGNMENT: "Hoàn thành xử lý",
  UNABLE_TO_HANDLE: "Không thể xử lý",
  CREATE_CATEGORY: "Tạo danh mục",
  UPDATE_CATEGORY: "Cập nhật danh mục",
  DEACTIVATE_CATEGORY: "Ngừng danh mục",
  FINALIZE_AGENT_RESULT: "Hoàn tất phân loại",
  RESOLVE_MANUAL_REVIEW: "Xác nhận phân loại",
  REJECT_MANUAL_REVIEW: "Từ chối phân loại",
  // Risk scoring v2. The events `docs/risk_scoring_v2.md` §9 requires a trail
  // for -- each one a moment a priority moved without anybody re-scoring the
  // ticket by hand, which is exactly when an auditor needs a sentence.
  RESOLVE_EMERGENCY_REVIEW: "Duyệt mức khẩn cấp",
  EMERGENCY_WARNING_RAISED: "Cảnh báo khẩn cấp",
  MASTER_ESCALATED_BY_EMERGENCY_DUPLICATE: "Nâng phản ánh gốc theo bản trùng khẩn cấp",
  TICKET_DETACHED_FROM_CASE: "Tách khỏi hồ sơ sự cố",
  INCIDENT_CASE_CLOSED: "Đóng hồ sơ sự cố",
  TICKET_GROUPED_INTO_CASE: "Gộp vào hồ sơ sự cố",
  ASSIGNMENT_ENDED_BY_EMERGENCY_ESCALATION: "Thu hồi phân công do nâng lên P5",
};
const actorLabels: Record<string, string> = { COORDINATOR: "Điều phối viên", TECHNICIAN: "Kỹ thuật viên", RESIDENT: "Cư dân", SYSTEM: "Hệ thống" };
const fieldLabels: Record<string, string> = {
  status: "Trạng thái",
  priority: "Mức ưu tiên",
  category_id: "Danh mục",
  classification_status: "Phân loại",
  risk_score: "Điểm rủi ro",
  technician_id: "Kỹ thuật viên",
  display_name: "Tên danh mục",
  code: "Mã danh mục",
  is_active: "Trạng thái sử dụng",
  // Risk scoring v2. `score_total`, `base_score`, `priority_ceiling` and
  // `severity` are kept below because audit rows written before the cutover
  // still carry them, and an entry rendering a raw column name is worse than a
  // stale label.
  score_total: "Điểm (thang cũ)",
  base_score: "Điểm cơ sở (đã bỏ)",
  priority_ceiling: "Mức ưu tiên tối đa (đã bỏ)",
  severity: "Mức độ nghiêm trọng (đã bỏ)",
  reason: "Lý do",
  duplicate_ticket_id: "Phản ánh trùng",
  case_id: "Hồ sơ sự cố",
  density: "Số căn trong case",
  escalated_to_emergency: "Nâng lên P5",
  blocker_codes: "Sự kiện khẩn cấp",
  technician_ids: "Kỹ thuật viên bị thu hồi",
};
const valueLabels: Record<string, string> = {
  NEW: "Mới",
  WAITING_RESIDENT_INFO: "Chờ cư dân bổ sung",
  APPROVED: "Đã duyệt",
  ASSIGNED: "Đã phân công",
  IN_PROGRESS: "Đang xử lý",
  COMPLETED: "Hoàn thành",
  UNRESOLVABLE: "Không thể xử lý",
  CANCELLED: "Đã hủy",
  INVALID: "Không hợp lệ",
  PENDING: "Chờ phân loại",
  PROCESSING: "Đang phân loại",
  RESOLVED: "Đã phân loại",
  MANUAL_REVIEW: "Chờ xác nhận thủ công",
  FAILED: "Phân loại thất bại",
};

export default function AuditPage() {
  const [all, setAll] = useState<BackendAuditLog[]>([]), [error, setError] = useState("");
  const [action, setAction] = useState("all");
  const [actor, setActor] = useState("all");
  const [auditPage, setAuditPage] = useState(1);
  const actions = [...new Set(all.map((entry) => entry.action))];
  const actors = [...new Set(all.map((entry) => entry.actor_role))];
  const entries = all.filter((entry) => (action === "all" || entry.action === action) && (actor === "all" || entry.actor_role === actor));
  const totalAuditPages = Math.max(1, Math.ceil(entries.length / AUDIT_PAGE_SIZE));
  const activeAuditPage = Math.min(auditPage, totalAuditPages);
  const visibleEntries = entries.slice((activeAuditPage - 1) * AUDIT_PAGE_SIZE, activeAuditPage * AUDIT_PAGE_SIZE);
  useEffect(() => setAuditPage(1), [action, actor]);
  useEffect(() => { listBackendAuditLogs().then(setAll).catch((reason) => setError(reason instanceof Error ? reason.message : "Không tải được audit log.")); }, []);

  const filters = <div className="auditHeaderFilters"><select value={action} onChange={(event) => setAction(event.target.value)}><option value="all">Loại hành động</option>{actions.map((value) => <option value={value} key={value}>{formatAction(value)}</option>)}</select><select value={actor} onChange={(event) => setActor(event.target.value)}><option value="all">Người thao tác</option>{actors.map((value) => <option value={value} key={value}>{actorLabels[value] || formatCode(value)}</option>)}</select></div>;

  return <RoleShell role="manager" title="Nhật ký hệ thống" subtitle="Theo dõi lịch sử thay đổi và người thực hiện.">
    <div className="managerPageStack">
      {error && <div className="alert error">{error}</div>}
      <ManagerSurface title="Lịch sử thao tác" description="Bản ghi hệ thống được lưu tự động và không thể chỉnh sửa." icon={<ScrollText size={19} />} actions={filters} bodyClassName="managerSurfaceTableBody">
        <div className="auditList managerAuditList">
          <div className="auditRow header"><span>Thời gian</span><span>Người thao tác</span><span>Hành động</span><span>Đối tượng</span><span>Nội dung thay đổi</span><span>Lý do</span></div>
          {visibleEntries.map((entry) => <div className="auditRow" key={entry.id}><span data-label="Thời gian">{formatDateTime(entry.created_at)}</span><strong data-label="Người thao tác">{actorLabels[entry.actor_role] || formatCode(entry.actor_role)}</strong><span data-label="Hành động"><b className="auditActionBadge">{formatAction(entry.action)}</b></span><span data-label="Đối tượng" className="auditEntity">{formatAuditEntity(entry)}</span><span data-label="Nội dung thay đổi" className="auditChangeCell"><AuditChanges entry={entry} /></span><span data-label="Lý do" className="tableSecondary auditReason">{formatAuditReason(entry.reason)}</span></div>)}
          <ManagerPagination page={activeAuditPage} pageSize={AUDIT_PAGE_SIZE} totalItems={entries.length} itemLabel="bản ghi" onPageChange={setAuditPage} />
        </div>
      </ManagerSurface>
    </div>
  </RoleShell>;
}

function AuditChanges({ entry }: { entry: BackendAuditLog }) {
  const before = entry.before_data || {};
  const after = entry.after_data || {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((key) => !["id", "ticket_id"].includes(key) && JSON.stringify(before[key]) !== JSON.stringify(after[key]));
  if (!keys.length) return <span className="auditNoChange">Ghi nhận thao tác</span>;
  return <span className="auditChangeList">{keys.map((key) => <span className="auditChangeLine" key={key}><small>{fieldLabels[key] || formatCode(key)}</small><span>{formatAuditValue(before[key], key)}</span><ArrowRight size={13} /><strong>{formatAuditValue(after[key], key)}</strong></span>)}</span>;
}

function formatAuditValue(value: unknown, key: string) {
  if (value === null || value === undefined || value === "") return "Chưa có";
  if (typeof value === "boolean") return value ? "Đang sử dụng" : "Ngừng sử dụng";
  if (typeof value === "number") return new Intl.NumberFormat("vi-VN").format(value);
  if (Array.isArray(value)) return value.length ? `${value.length} mục` : "Không có";
  if (typeof value === "object") return `${Object.keys(value as Record<string, unknown>).length} trường dữ liệu`;
  const text = String(value);
  if (valueLabels[text]) return valueLabels[text];
  if (key.endsWith("_id") && /^[0-9a-f-]{32,}$/i.test(text)) return text.slice(0, 8);
  return text;
}

function formatAuditEntity(entry: BackendAuditLog) {
  const ticketId = typeof entry.after_data?.ticket_id === "string" ? entry.after_data.ticket_id : entry.entity_type === "TICKET" ? entry.entity_id : null;
  if (ticketId) return `#${formatTicketCode(ticketId)}`;
  if (entry.entity_type === "TICKET_ASSIGNMENT") return `Phân công #${entry.entity_id.slice(0, 8)}`;
  if (entry.entity_type === "CATEGORY") return `Danh mục #${entry.entity_id.slice(0, 8)}`;
  return `#${entry.entity_id.slice(0, 8)}`;
}

function formatAction(value: string) { return actionLabels[value] || formatCode(value); }
function formatCode(value: string) { return value.toLowerCase().split("_").map((part) => part ? part[0].toUpperCase() + part.slice(1) : "").join(" "); }
function formatAuditReason(reason: string | null) {
  if (!reason) return "Không có";
  return ({
    "Resident created ticket.": "Cư dân tạo phản ánh.",
    "Resident cancelled ticket.": "Cư dân hủy phản ánh.",
    "Coordinator requested resident information.": "BQL yêu cầu bổ sung thông tin.",
    "Technician started work.": "Kỹ thuật viên bắt đầu xử lý.",
    "Technician completed assignment.": "Kỹ thuật viên hoàn thành xử lý.",
  } as Record<string, string>)[reason] || (valueLabels[reason] ? valueLabels[reason] : reason);
}
