import type { CoordinatorTicket } from "@/types/api";

/** The management-side rules for a ticket held at the emergency P3 gate.
 *
 *  P3 is the emergency Priority in this system — a five-minute SLA — and a
 *  ticket classified into it is not published on the model's say-so. It parks
 *  with `latest_analysis.p3_review_status === "PENDING"` until a coordinator
 *  either confirms the emergency or downgrades it with a reason.
 *
 *  The reason this lives in one module rather than inline in each component:
 *  `classification_status === "MANUAL_REVIEW"` is where two different things
 *  wait — a report the analysis could not classify, and one it classified as an
 *  emergency. Every surface that keys on that alone offers the generic
 *  resolve/reject form for an emergency, which the backend then refuses. One
 *  predicate, used everywhere, is what keeps the surfaces agreeing.
 *
 *  None of this is authorization. `src/services/p3_review_guard.py` refuses the
 *  same actions server-side; this only decides what is worth putting on screen.
 */

export const P3_REVIEW_ACTION = "REVIEW_P3";

export const P3_PENDING_LABEL = "P3 chờ duyệt khẩn cấp";

export const P3_PENDING_NOTICE =
  "P3 đang chờ Ban quản lý xác nhận trước khi công bố.";

export const P3_PENDING_LOCKED_HINT =
  "Chỉ có thể xác nhận P3 hoặc hạ xuống P1/P2 kèm lý do.";

type P3Ticket = Pick<CoordinatorTicket, "latest_analysis" | "available_actions">;

/** Whether this ticket is waiting on the emergency review.
 *
 *  Two sources, deliberately. `available_actions` is what the backend computed,
 *  and the analysis field is the fact behind it — reading both means a cached
 *  or partially-refreshed payload cannot put the generic controls back on
 *  screen for a ticket the server will refuse them for.
 */
export function isP3ReviewPending(ticket: P3Ticket | null | undefined): boolean {
  if (!ticket) return false;
  if (ticket.latest_analysis?.p3_review_status === "PENDING") return true;
  return (ticket.available_actions || []).includes(P3_REVIEW_ACTION);
}

/** Which management controls a surface may render for this ticket.
 *
 *  Every flag is false while the gate is open, whatever `available_actions`
 *  says, so a surface cannot offer an action that can only come back 409.
 */
export function managerControls(ticket: P3Ticket | null | undefined) {
  const actions = ticket?.available_actions || [];
  const p3Pending = isP3ReviewPending(ticket);
  return {
    p3Pending,
    canReviewP3: p3Pending,
    canApprove: !p3Pending && actions.includes("APPROVE"),
    canAssign: !p3Pending && actions.includes("ASSIGN"),
    canOverride: !p3Pending && actions.includes("OVERRIDE_CLASSIFICATION"),
    canManualReview: !p3Pending && actions.includes("RESOLVE_MANUAL_REVIEW"),
    canRejectManualReview: !p3Pending && actions.includes("REJECT_MANUAL_REVIEW"),
    /** The duplicate-uncertain panel. A separate gate, held by the same person
     *  but answering a different question, and never open at the same time. */
    canDecideDuplicate:
      !p3Pending
      && ticket?.latest_analysis?.exit_reason === "DUPLICATE_UNCERTAIN"
      && ticket?.latest_analysis?.grouping_status === "WAITING_DUPLICATE_DECISION",
  };
}
