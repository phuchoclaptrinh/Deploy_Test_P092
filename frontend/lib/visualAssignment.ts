/** Visual Assignment board rules, kept out of the component that renders them.
 *
 *  Everything here is a pure function over data the backend already decided.
 *  Nothing re-derives business state the API is authoritative about — in
 *  particular, whether a drop is allowed is read from `preview.blocked`, never
 *  recomputed from skills and shifts on the client. A second implementation of
 *  §3 living in the browser would eventually disagree with the one that matters.
 *
 *  No runtime dependencies, so the rules can be exercised directly
 *  (`frontend/tests/assignment.test.ts`).
 */
import type {
  AtRiskDecision,
  AutoAssignmentToggle,
  BoardPlacementPreview,
  BoardTechnician,
  BoardUnit,
  VisualBoard,
  VisualPlacement,
  VisualPlacementFailure,
} from "@/types/api";

/** Where an unplaced unit lives. Not a technician id, so it cannot collide. */
export const POOL = "__pool__";

/** §2, verbatim. The modal must say exactly this before autonomy is switched on. */
export const AUTO_ASSIGNMENT_CONFIRMATION =
  "Những phản ánh đã được AI phân loại xác định, không trùng lặp và không phải phản ánh khẩn cấp " +
  "sẽ được tự động duyệt, bỏ qua bước gộp nhóm và được phân công ngay lập tức. " +
  "Những phản ánh không đáp ứng các điều kiện này sẽ được chuyển cho Ban quản lý.";

export const AUTO_ASSIGNMENT_OFF_NOTICE =
  "Tắt phân việc tự động chỉ dừng các phân công trong tương lai. Những phản ánh đã được phân công vẫn giữ nguyên.";

/** The three §3 hard constraints, and the two advisory signals.
 *
 *  Split here rather than at the call site because the difference is the whole
 *  behaviour: the first three refuse a drop, the last two colour a card. */
export const BLOCKING_WARNINGS = ["MISSING_SKILL", "TECHNICIAN_UNAVAILABLE", "OUT_OF_SHIFT"] as const;

export const WARNING_LABELS: Record<string, string> = {
  MISSING_SKILL: "Không có kỹ năng phù hợp",
  TECHNICIAN_UNAVAILABLE: "Không sẵn sàng nhận việc",
  OUT_OF_SHIFT: "Ngoài ca làm việc 08:00–18:00",
  OVERLOADED: "Lịch đã kín, việc này sẽ sang ngày hôm sau",
  SCHEDULE_RISK: "Sẽ làm trễ một công việc đã hẹn",
};

/** Codes the bulk confirm can reject with, beyond the placement warnings. */
export const FAILURE_LABELS: Record<string, string> = {
  ...WARNING_LABELS,
  TICKET_NOT_APPROVED: "Phản ánh không còn ở trạng thái đã duyệt",
  TICKET_IS_DUPLICATE: "Phản ánh đã được gộp trùng",
  EMERGENCY_REVIEW_PENDING: "Đang chờ duyệt mức khẩn cấp P5",
  GROUPING_NOT_READY: "Chưa xong bước tra trùng và gộp cụm",
  GROUPING_CASE_NOT_OPEN: "Cụm sự cố của phản ánh này đã đóng",
  ACTIVE_ASSIGNMENT_EXISTS: "Phản ánh đã có kỹ thuật viên khác",
};

export const warningLabel = (code: string) => WARNING_LABELS[code] || code;
export const failureLabel = (code: string) => FAILURE_LABELS[code] || code;

export const isBlocking = (code: string) => (BLOCKING_WARNINGS as readonly string[]).includes(code);

/** A board placement in progress: unit id -> technician id, or POOL. */
export type Draft = Record<string, string>;

/** Every unit starts in the pool. The board is computed server-side and never
 *  stored, so there is no draft to restore and nothing to reconcile. */
export const emptyDraft = (board: VisualBoard | null): Draft =>
  Object.fromEntries((board?.units || []).map((unit) => [unit.unit_id, POOL]));

/** Keep placements for units that are still on the board, drop the rest.
 *
 *  Called after a background refresh. A unit that vanished was assigned by
 *  somebody else or stopped being eligible; silently keeping its placement
 *  would mean confirming something the manager can no longer see. */
export const reconcileDraft = (draft: Draft, board: VisualBoard | null): Draft => {
  const next: Draft = {};
  for (const unit of board?.units || []) next[unit.unit_id] = draft[unit.unit_id] || POOL;
  return next;
};

export const previewFor = (unit: BoardUnit, technicianId: string): BoardPlacementPreview | undefined =>
  unit.previews.find((preview) => preview.technician_id === technicianId);

/** Whether the board may accept this drop.
 *
 *  Read straight from the server's verdict. `eligible_technician_ids` is the
 *  same answer from the other direction and is used as the fallback for a
 *  technician the previews did not cover at all. */
export const canPlace = (unit: BoardUnit, technicianId: string): boolean => {
  if (technicianId === POOL) return true;
  const preview = previewFor(unit, technicianId);
  if (preview) return !preview.blocked;
  return unit.eligible_technician_ids.includes(technicianId);
};

/** The advisory warnings to show on a placed card. Blocking ones are absent by
 *  construction: a blocked placement never gets this far. */
export const advisoryWarnings = (unit: BoardUnit, technicianId: string): string[] =>
  (previewFor(unit, technicianId)?.warnings || []).filter((code) => !isBlocking(String(code))).map(String);

export const blockingWarnings = (unit: BoardUnit, technicianId: string): string[] =>
  (previewFor(unit, technicianId)?.warnings || []).filter((code) => isBlocking(String(code))).map(String);

export const unitsInColumn = (board: VisualBoard | null, draft: Draft, columnId: string): BoardUnit[] =>
  (board?.units || []).filter((unit) => (draft[unit.unit_id] || POOL) === columnId);

/** What will actually be sent. Pool entries are not placements. */
export const placementsOf = (draft: Draft): VisualPlacement[] =>
  Object.entries(draft)
    .filter(([, technicianId]) => technicianId !== POOL)
    .map(([unit_id, technician_id]) => ({ unit_id, technician_id }));

export const placedCount = (draft: Draft) => placementsOf(draft).length;

/** Tickets, not units: a group of three counts as three reports for the
 *  manager's summary, even though it is one drag. */
export const placedTicketCount = (board: VisualBoard | null, draft: Draft): number =>
  placementsOf(draft).reduce((total, placement) => {
    const unit = (board?.units || []).find((item) => item.unit_id === placement.unit_id);
    return total + (unit?.ticket_ids.length || 0);
  }, 0);

export const canConfirm = (board: VisualBoard | null, draft: Draft): boolean =>
  Boolean(board?.within_working_shift) && placedCount(draft) > 0;

/** One line under the confirm button, saying what is about to happen. */
export const confirmSummary = (board: VisualBoard | null, draft: Draft): string => {
  const units = placedCount(draft);
  if (!units) return "Chưa có công việc nào được xếp vào kỹ thuật viên.";
  const tickets = placedTicketCount(board, draft);
  const technicians = new Set(placementsOf(draft).map((placement) => placement.technician_id)).size;
  const label = units === tickets ? `${tickets} phản ánh` : `${units} nhóm việc (${tickets} phản ánh)`;
  return `Sẽ phân ${label} cho ${technicians} kỹ thuật viên trong một lần xác nhận.`;
};

/** How many risky-but-allowed placements the manager is about to confirm. */
export const riskyPlacements = (board: VisualBoard | null, draft: Draft): number =>
  placementsOf(draft).filter((placement) => {
    const unit = (board?.units || []).find((item) => item.unit_id === placement.unit_id);
    return unit ? advisoryWarnings(unit, placement.technician_id).length > 0 : false;
  }).length;

/** Technician columns, most available first, so a manager drags rightwards into
 *  a fuller day rather than hunting for the free person. */
export const orderedTechnicians = (board: VisualBoard | null): BoardTechnician[] =>
  [...(board?.technicians || [])].sort(
    (a, b) =>
      Number(b.is_active && b.is_available) - Number(a.is_active && a.is_available) ||
      a.active_assignment_count - b.active_assignment_count ||
      (a.display_name || "").localeCompare(b.display_name || "", "vi"),
  );

/** Live workload for a column header: what they hold, plus what is being added. */
export const columnLoad = (board: VisualBoard | null, draft: Draft, technician: BoardTechnician) => {
  const adding = unitsInColumn(board, draft, technician.technician_id);
  return {
    current: technician.active_assignment_count,
    adding: adding.reduce((total, unit) => total + unit.ticket_ids.length, 0),
    hours: adding.reduce((total, unit) => total + unit.p80_seconds, 0) / 3600,
  };
};

/** A 4.5-hour estimate reads better than 16200 seconds. */
export const hoursLabel = (seconds: number): string => {
  const hours = seconds / 3600;
  return Number.isInteger(hours) ? `${hours} giờ` : `${hours.toFixed(1).replace(".", ",")} giờ`;
};

/** Negative slack is the signal; the sign is the whole message. */
export const slackLabel = (seconds: number | null | undefined): string => {
  if (seconds == null) return "Chưa có lịch hẹn nào";
  if (seconds < 0) return `Trễ ${hoursLabel(Math.abs(seconds))} so với lịch đã hẹn`;
  return `Còn dư ${hoursLabel(seconds)}`;
};

/** Turn a 409 from the bulk confirm into something a manager can act on. */
export const confirmFailureMessage = (failures: VisualPlacementFailure[] | undefined): string => {
  if (!failures?.length) return "Không phân việc được. Không có thay đổi nào được lưu.";
  const reasons = [...new Set(failures.flatMap((failure) => failure.codes.map(failureLabel)))];
  return `Không phân việc được: ${reasons.join("; ")}. Không có thay đổi nào được lưu.`;
};

/** Which unit ids a rejected confirm named, so the board can mark those cards. */
export const failedUnitIds = (failures: VisualPlacementFailure[] | undefined): string[] =>
  [...new Set((failures || []).map((failure) => failure.unit_id))];

/** The toggle's one-line state, for the button next to it. */
export const toggleSummary = (toggle: AutoAssignmentToggle | null): string => {
  if (!toggle?.enabled) return "Phân việc tự động đang tắt. Mọi phản ánh chờ Ban quản lý phân công.";
  const waiting = toggle.open_event_count;
  const queue = waiting ? ` Đang có ${waiting} phản ánh trong hàng đợi.` : "";
  return `Phân việc tự động đang bật${toggle.enabled_by_name ? ` (bật bởi ${toggle.enabled_by_name})` : ""}.${queue}`;
};

/** §7: `AGENT` means a model weighed the trade-off; `SCHEDULER_FALLBACK` means
 *  it did not answer and the least-late option was taken. Two different things
 *  for a reviewer, so they never share a label. */
export const decisionSourceLabel = (source: string | null | undefined): string =>
  ({
    AGENT: "Agent quyết định",
    SCHEDULER_FALLBACK: "Agent không phản hồi — hệ thống chọn phương án ít trễ nhất",
    SCHEDULER: "Bộ lập lịch",
  })[String(source)] || "Không xác định";

export const atRiskNeedsAttention = (decision: AtRiskDecision): boolean =>
  decision.decision_source === "SCHEDULER_FALLBACK" || (decision.slack_seconds ?? 0) < 0;
