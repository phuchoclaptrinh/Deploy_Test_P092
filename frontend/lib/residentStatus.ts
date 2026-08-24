import { formatDeadline } from "@/lib/residentDate";
import type { ResidentTicket } from "@/types/api";

/** Resident-facing status vocabulary (docs/ui/COMPONENT_STATES.md C-13).
 *  The backend returns friendly Vietnamese text already; this layer only
 *  aligns the wording with approved product copy and adds tone.
 *
 *  Lifecycle grouping is NOT decided here — `ticket.lifecycle_group` comes from
 *  the backend, which is also what the Requests list filters on. */
export type ResidentStatusTone = "info" | "attention" | "positive" | "critical" | "neutral";
export type ResidentStatusView = { label: string; tone: ResidentStatusTone };

const CHECKING: ResidentStatusView = { label: "Đang kiểm tra phản ánh", tone: "info" };
const MANUAL_REVIEW: ResidentStatusView = { label: "Ban quản lý đang xem xét", tone: "info" };
const WAITING_ANSWER: ResidentStatusView = { label: "Đang chờ bạn trả lời", tone: "attention" };

const backendStatusViews: Record<string, ResidentStatusView> = {
  "đang phân tích...": CHECKING,
  "đang phân tích": CHECKING,
  "mới": { label: "Mới", tone: "neutral" },
  "chờ bổ sung thông tin": { label: "Đang chờ xử lý", tone: "attention" },
  "đã duyệt": { label: "Đã duyệt", tone: "positive" },
  "đã gán kỹ thuật viên": { label: "Đã có kỹ thuật viên", tone: "positive" },
  "đang xử lý": { label: "Đang xử lý", tone: "info" },
  "hoàn thành": { label: "Hoàn thành", tone: "positive" },
  "không xử lý được": { label: "Không xử lý được", tone: "critical" },
  "đã hủy": { label: "Đã hủy", tone: "neutral" },
  "không hợp lệ": { label: "Chưa được tiếp nhận", tone: "critical" },
  "đã gộp phản ánh": { label: "Sự cố đã được báo và đang được xử lý", tone: "info" },
};

const normalize = (value: string) => value.trim().toLocaleLowerCase("vi");

/** Classification is still running, so the report is in its Checking state. */
export function isCheckingReport(ticket: Pick<ResidentTicket, "display_status">) {
  return normalize(ticket.display_status).startsWith("đang phân tích");
}

/** The report ended without being accepted. The only way forward is a new one. */
export function isRejectedReport(ticket: Pick<ResidentTicket, "display_status">) {
  return normalize(ticket.display_status) === "không hợp lệ";
}

export function isLinkedReport(ticket: Pick<ResidentTicket, "display_status" | "duplicate_of_ticket_id">) {
  return Boolean(ticket.duplicate_of_ticket_id) || normalize(ticket.display_status) === "đã gộp phản ánh";
}

export function isFinishedReport(ticket: Pick<ResidentTicket, "lifecycle_group">) {
  return ticket.lifecycle_group === "FINISHED";
}

export function residentStatusView(
  ticket: Pick<ResidentTicket, "display_status" | "estimated_resolution_text">,
  options: { waitingForAnswer?: boolean } = {},
): ResidentStatusView {
  if (options.waitingForAnswer) return WAITING_ANSWER;
  // Manual review is only visible through the expected-time text today.
  if (ticket.estimated_resolution_text === "Đang chờ Ban quản lý xác nhận") return MANUAL_REVIEW;
  return backendStatusViews[normalize(ticket.display_status)]
    || { label: ticket.display_status, tone: "neutral" };
}

/** Expected-time block copy (C-14). Returns null when no estimate applies. */
function residentExpectedTime(
  ticket: Pick<ResidentTicket, "display_status" | "estimated_resolution_text" | "lifecycle_group">,
) {
  if (isFinishedReport(ticket)) return null;
  const text = ticket.estimated_resolution_text?.trim();
  if (!text || normalize(text).startsWith("đang phân tích")) return "Đang cập nhật thời gian xử lý";
  if (normalize(text).startsWith("phản ánh không hợp lệ")) return null;
  return text;
}

/** Sender label for a report card. Only apartment members reach this screen. */
export function residentSenderLabel(ticket: Pick<ResidentTicket, "is_reporter" | "reporter_name">) {
  if (ticket.is_reporter) return "Bạn";
  return ticket.reporter_name?.trim() || "Thành viên trong căn hộ";
}

export function residentLocationLabel(ticket: Pick<ResidentTicket, "location_label">) {
  return ticket.location_label?.trim() || "Chưa cập nhật vị trí";
}

export function residentCategoryLabel(ticket: Pick<ResidentTicket, "category_display_name">) {
  return ticket.category_display_name?.trim() || "Đang xác định loại sự cố";
}

/** Compact the backend's sentence-form estimate for list rows and fact tables.
 *  "Dự kiến xử lý trong vòng 72 giờ" reads as "Trong 72 giờ". */
function shortExpectedTime(text: string | null) {
  if (!text) return null;
  const compact = text
    .replace(/^Dự kiến xử lý\s+/i, "")
    .replace(/^trong vòng\s+/i, "Trong ")
    .trim();
  return compact.charAt(0).toLocaleUpperCase("vi") + compact.slice(1);
}

/** Priority is already friendly text; the list and fact table want it shorter. */
const shortPriorities: Record<string, string> = {
  "mức khẩn cấp cao nhất": "Khẩn cấp",
  "cần xử lý sớm": "Ưu tiên",
  "mức xử lý thông thường": "Thông thường",
};

export function shortPriority(text: string | null | undefined) {
  if (!text?.trim()) return null;
  return shortPriorities[text.trim().toLocaleLowerCase("vi")] || text.trim();
}

/** The single expected-time line shown on a report card and in its fact table.
 *  An absolute date answers "when will this be done?" directly, so the deadline
 *  wins whenever the backend has one; the wording stands in while it does not
 *  (still being analysed, or awaiting Building Management). */
export function residentExpectedLabel(
  ticket: Pick<
    ResidentTicket,
    "display_status" | "estimated_resolution_text" | "lifecycle_group" | "expected_resolution_at"
  >,
  now?: Date,
) {
  const wording = residentExpectedTime(ticket);
  if (!wording) return null;
  if (ticket.expected_resolution_at) return formatDeadline(ticket.expected_resolution_at, now);
  return shortExpectedTime(wording);
}
