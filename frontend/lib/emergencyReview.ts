import type { CoordinatorTicket } from "@/types/api";

/** The management-side rules for a ticket held at the emergency gate.
 *
 *  P5 is the emergency Priority in this system — a five-minute SLA, handled by
 *  hand — and a ticket classified into it is not published on the model's
 *  say-so. It parks with `latest_analysis.emergency_review_status === "PENDING"`
 *  until a coordinator either confirms the emergency or downgrades it with a
 *  reason.
 *
 *  Renamed from `p3Review` when the scale inverted. The gate did not change;
 *  only the band behind it did.
 *
 *  The reason this lives in one module rather than inline in each component:
 *  `classification_status === "MANUAL_REVIEW"` is where two different things
 *  wait — a report the analysis could not classify, and one it classified as an
 *  emergency. Every surface that keys on that alone offers the generic
 *  resolve/reject form for an emergency, which the backend then refuses. One
 *  predicate, used everywhere, is what keeps the surfaces agreeing.
 *
 *  None of this is authorization. `src/services/emergency_review_guard.py` and
 *  `src/domain/assignment_guard.py` refuse the same actions server-side; this
 *  only decides what is worth putting on screen.
 */

export const EMERGENCY_REVIEW_ACTION = "REVIEW_EMERGENCY";

export const EMERGENCY_PENDING_LABEL = "Chờ duyệt mức khẩn cấp P5";

export const EMERGENCY_PENDING_NOTICE =
  "Phản ánh ở mức khẩn cấp P5 và đang chờ Ban quản lý duyệt. Chưa công bố, chưa gộp cụm và không phân việc cho kỹ thuật viên.";

export const EMERGENCY_PENDING_LOCKED_HINT =
  "Chỉ có thể xác nhận P5 hoặc hạ mức xuống P1–P4 kèm lý do. Các thao tác duyệt, chỉnh phân loại, phân công và gộp trùng đều bị khóa.";

/** What confirming does, and — just as importantly — what it does not.
 *
 *  Worth its own sentence on screen because the intuitive reading is wrong:
 *  confirming an emergency sounds like it should hand the work to somebody, and
 *  it does the opposite. Building Management deals with a P5 themselves.
 */
export const EMERGENCY_CONFIRM_HINT =
  "Ban quản lý xử lý trực tiếp. Xác nhận không mở lại phân việc cho kỹ thuật viên và không bật lại luồng tự động.";

type EmergencyTicket = Pick<CoordinatorTicket, "latest_analysis" | "available_actions">;

/** Whether this ticket is waiting on the emergency review.
 *
 *  Two sources, deliberately. `available_actions` is what the backend computed,
 *  and the analysis field is the fact behind it — reading both means a cached
 *  or partially-refreshed payload cannot put the generic controls back on
 *  screen for a ticket the server will refuse them for.
 */
export function isEmergencyReviewPending(ticket: EmergencyTicket | null | undefined): boolean {
  if (!ticket) return false;
  if (ticket.latest_analysis?.emergency_review_status === "PENDING") return true;
  return (ticket.available_actions || []).includes(EMERGENCY_REVIEW_ACTION);
}

/** Which management controls a surface may render for this ticket.
 *
 *  Every flag is false while the gate is open, whatever `available_actions`
 *  says, so a surface cannot offer an action that can only come back 409.
 */
export function managerControls(ticket: EmergencyTicket | null | undefined) {
  const actions = ticket?.available_actions || [];
  const emergencyPending = isEmergencyReviewPending(ticket);
  return {
    emergencyPending,
    canReviewEmergency: emergencyPending,
    canApprove: !emergencyPending && actions.includes("APPROVE"),
    // The backend drops ASSIGN from a P5's actions outright, so this needs no
    // priority check of its own: one source of truth, and it is the server's.
    canAssign: !emergencyPending && actions.includes("ASSIGN"),
    canOverride: !emergencyPending && actions.includes("OVERRIDE_CLASSIFICATION"),
    canManualReview: !emergencyPending && actions.includes("RESOLVE_MANUAL_REVIEW"),
    canRejectManualReview: !emergencyPending && actions.includes("REJECT_MANUAL_REVIEW"),
    /** The duplicate-uncertain panel. A separate gate, held by the same person
     *  but answering a different question, and never open at the same time. */
    canDecideDuplicate:
      !emergencyPending
      && ticket?.latest_analysis?.exit_reason === "DUPLICATE_UNCERTAIN"
      && ticket?.latest_analysis?.grouping_status === "WAITING_DUPLICATE_DECISION",
  };
}
