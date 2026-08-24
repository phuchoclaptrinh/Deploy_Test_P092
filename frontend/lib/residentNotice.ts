import {
  AlertTriangle,
  Bell,
  CheckCheck,
  CheckCircle2,
  Clock,
  UserCheck,
  Wrench,
  XCircle,
} from "lucide-react";
import type { BackendNotification } from "@/types/api";

/** Notification presentation: one icon and one semantic colour per event kind.
 *  Blue is the default for ordinary updates, green for success, amber for
 *  warnings, red only for a rejection or failure. */
export type NoticeTone = "info" | "success" | "warning" | "error" | "muted";
export type NoticeLook = { Icon: typeof Bell; tone: NoticeTone };

const byType: Record<string, NoticeLook> = {
  TICKET_APPROVED: { Icon: CheckCheck, tone: "info" },
  TICKET_ASSIGNED: { Icon: UserCheck, tone: "info" },
  TICKET_IN_PROGRESS: { Icon: Wrench, tone: "info" },
  TICKET_REASSIGNED: { Icon: Clock, tone: "warning" },
  TICKET_ASSIGNMENT_OVERDUE: { Icon: Clock, tone: "warning" },
  TECHNICIAN_ACCEPTANCE_WARNING: { Icon: Clock, tone: "warning" },
  TICKET_COMPLETED: { Icon: CheckCircle2, tone: "success" },
  TICKET_RESOLVED: { Icon: CheckCircle2, tone: "success" },
  TICKET_INVALID: { Icon: XCircle, tone: "error" },
  TICKET_REJECTED: { Icon: XCircle, tone: "error" },
  TICKET_UNRESOLVABLE: { Icon: AlertTriangle, tone: "error" },
  TICKET_CANCELLED: { Icon: XCircle, tone: "muted" },
  TICKET_DUPLICATE_LINKED: { Icon: Bell, tone: "info" },
};

/** Notification types are backend-owned and can grow, so unknown types fall back
 *  to reading the title rather than showing nothing meaningful. */
const byTitleKeyword: Array<{ match: RegExp; look: NoticeLook }> = [
  { match: /hoàn thành|đã xong/i, look: { Icon: CheckCircle2, tone: "success" } },
  { match: /duyệt|tiếp nhận/i, look: { Icon: CheckCheck, tone: "info" } },
  { match: /kỹ thuật viên|phân công|tiếp nhận xử lý/i, look: { Icon: UserCheck, tone: "info" } },
  { match: /quá hạn|trễ|sắp hết|chuyển tiếp/i, look: { Icon: Clock, tone: "warning" } },
  { match: /không tiếp nhận|từ chối|không hợp lệ|thất bại/i, look: { Icon: XCircle, tone: "error" } },
  { match: /hủy/i, look: { Icon: XCircle, tone: "muted" } },
  { match: /cần bạn|trả lời|bổ sung/i, look: { Icon: AlertTriangle, tone: "warning" } },
];

export function noticeLook(notice: Pick<BackendNotification, "notification_type" | "title" | "status">): NoticeLook {
  const known = byType[notice.notification_type?.toUpperCase() || ""];
  if (known) return known;
  for (const { match, look } of byTitleKeyword) {
    if (match.test(notice.title || "")) return look;
  }
  return { Icon: Bell, tone: "info" };
}

/** A notice keeps its colour after it is read: the colour says what happened,
 *  not whether it has been seen. Unread is carried by the row tint, the bolder
 *  title and the dot instead. */
export function noticeTone(notice: Pick<BackendNotification, "notification_type" | "title" | "status">): NoticeTone {
  return noticeLook(notice).tone;
}
