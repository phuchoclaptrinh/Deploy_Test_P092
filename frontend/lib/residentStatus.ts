import { formatShortDateTime } from "@/lib/residentDate";
import type { ResidentTicket } from "@/types/api";

/** Resident-facing status vocabulary (docs/ui/COMPONENT_STATES.md C-13).
 *  The backend returns friendly Vietnamese text already; this layer only
 *  aligns the wording with approved product copy and adds tone.
 *
 *  Lifecycle grouping is NOT decided here — `ticket.lifecycle_group` comes from
 *  the backend, which is also what the Requests list filters on. */
export type ResidentStatusTone = "info" | "attention" | "positive" | "critical" | "neutral";
export type ResidentStatusView = { label: string; tone: ResidentStatusTone };

const CHECKING: ResidentStatusView = { label: "Đang kiểm tra", tone: "info" };
const MANUAL_REVIEW: ResidentStatusView = { label: "Đang xem xét", tone: "info" };
const WAITING_ANSWER: ResidentStatusView = { label: "Cần trả lời", tone: "attention" };

const backendStatusViews: Record<string, ResidentStatusView> = {
  "đang phân tích...": CHECKING,
  "đang phân tích": CHECKING,
  "ban quản lý đang xử lý khẩn cấp": { label: "Khẩn cấp", tone: "critical" },
  "mới": { label: "Mới", tone: "neutral" },
  "chờ bổ sung thông tin": { label: "Cần bổ sung", tone: "attention" },
  "đã duyệt": { label: "Đã duyệt", tone: "positive" },
  "đã gán kỹ thuật viên": { label: "Có kỹ thuật viên", tone: "positive" },
  "đang xử lý": { label: "Đang xử lý", tone: "info" },
  "hoàn thành": { label: "Hoàn thành", tone: "positive" },
  "không xử lý được": { label: "Không xử lý", tone: "critical" },
  "đã hủy": { label: "Đã hủy", tone: "neutral" },
  "không hợp lệ": { label: "Chưa tiếp nhận", tone: "critical" },
  "đã gộp phản ánh": { label: "Đã ghi nhận", tone: "info" },
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
  ticket: Pick<ResidentTicket, "display_status" | "progress_text">,
  options: { waitingForAnswer?: boolean } = {},
): ResidentStatusView {
  if (options.waitingForAnswer) return WAITING_ANSWER;
  // Manual review is only visible through the progress wording today.
  if (normalize(ticket.progress_text).startsWith("đang chờ ban quản lý")) return MANUAL_REVIEW;
  return backendStatusViews[normalize(ticket.display_status)]
    || { label: ticket.display_status, tone: "neutral" };
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

/** Priority is already friendly text; the list and fact table want it shorter.
 *
 *  Keyed on the backend's own sentence rather than on the band, because a
 *  resident is never shown "P4" and never shown a score. What they get is what
 *  happens next; `priority_description` is the backend's wording for that, and
 *  these are the same sentences at list length. An unrecognised sentence falls
 *  through unshortened rather than being dropped. */
const shortPriorities: Record<string, string> = {
  "sự cố khẩn cấp, ban quản lý đang xử lý thủ công": "Khẩn cấp",
  "cần xử lý ngay trong ca": "Trong ca",
  "cần xử lý sớm": "Ưu tiên",
  "xử lý theo lịch thường": "Theo lịch",
  "mức xử lý thông thường": "Thông thường",
};

export function shortPriority(text: string | null | undefined) {
  if (!text?.trim()) return null;
  return shortPriorities[text.trim().toLocaleLowerCase("vi")] || text.trim();
}

/** §4: the one forward-looking line a resident is shown.
 *
 *  It answers "when will someone start?", never "when will this be finished?".
 *  The completion estimate this replaced is gone from the payload entirely, so
 *  there is nothing here that could fall back to one -- which is the point, and
 *  so is the absence of any acceptance wording: there is no longer a step where
 *  a technician confirms, so the resident is never told to wait for one.
 *
 *  `expected_start_at` arrives as soon as a technician is assigned and the
 *  backend drops it once work begins, so this line turns itself into the
 *  progress wording -- "Kỹ thuật viên đang xử lý" -- without a branch here.
 */
export function residentExpectedLabel(
  ticket: Pick<ResidentTicket, "display_status" | "progress_text" | "lifecycle_group" | "expected_start_at">,
  now?: Date,
) {
  if (isFinishedReport(ticket)) return null;
  if (ticket.expected_start_at) return `Dự kiến ${formatShortDateTime(ticket.expected_start_at)}`;
  const text = ticket.progress_text?.trim();
  if (!text || normalize(text).startsWith("phản ánh không hợp lệ")) return null;
  return compactResidentProgress(text);
}

function compactResidentProgress(text: string) {
  const normalized = normalize(text);
  if (normalized.startsWith("đang chờ ban quản lý")) return null;
  if (normalized.includes("kỹ thuật viên") && normalized.includes("xử lý")) return null;
  if (normalized.includes("gộp") || normalized.includes("cùng một sự cố")) return "Cùng sự cố";
  return text
    .replace(/ban quản lý/gi, "BQL")
    .replace(/kỹ thuật viên/gi, "KTV");
}
