/** Assignment-workspace rules, kept out of the component that renders them.
 *
 *  Everything here is a pure function over data the backend already decided:
 *  which technicians may be offered, which queue a ticket belongs in, whether a
 *  job may be cancelled, and what a failed request should tell the coordinator.
 *  None of it re-derives business state the API is authoritative about — it only
 *  arranges that state into the three queues and the proposal board.
 *
 *  This module imports no runtime dependencies on purpose, so the rules can be
 *  exercised directly (`frontend/tests/assignment.test.ts`).
 */
import type { AssignmentHistoryRecord, AssignmentJob, AssignmentProposalBatch, AssignmentProposalItem, AssignmentSchedule, AutoAssignmentDelay, AutoAssignmentSettings, CoordinatorCluster, CoordinatorTicket, ProposalScheduleChoice, TechnicianSummary } from "@/types/api";

/** Contract §7.6 lists exactly these five delays and no others. */
export const DELAY_OPTIONS: Array<{ value: AutoAssignmentDelay; label: string }> = [
  { value: "IMMEDIATE", label: "Ngay lập tức" },
  { value: "2H", label: "Sau 2 giờ" },
  { value: "5H", label: "Sau 5 giờ" },
  { value: "1D", label: "Sau 1 ngày" },
  { value: "3D", label: "Sau 3 ngày" },
];

/** What the result modal offers after a confirmation.
 *
 *  Deliberately *not* built from `DELAY_OPTIONS`. Those five values answer "how
 *  long does one approved ticket wait before the system assigns it by itself";
 *  these answer "how often does the system build me another table to review".
 *  Two features, two backing tables, two vocabularies — the word `Lặp lại` is
 *  load-bearing, because the previous UI borrowed the delay and told
 *  coordinators a repeat was coming when an assignment was. */
export const SCHEDULE_OPTIONS: Array<{ value: ProposalScheduleChoice; label: string }> = [
  { value: "NONE", label: "Không tự động" },
  { value: "2_HOURS", label: "Lặp lại mỗi 2 giờ" },
  { value: "1_DAY", label: "Lặp lại mỗi 1 ngày" },
  { value: "3_DAYS", label: "Lặp lại mỗi 3 ngày" },
];

export const scheduleLabel = (value: ProposalScheduleChoice | null | undefined) =>
  SCHEDULE_OPTIONS.find((option) => option.value === value)?.label || "Không tự động";

/** What the schedule is doing, in one line, for the State 1 banner. */
export const scheduleSummary = (schedule: AssignmentSchedule | null) =>
  schedule?.enabled && schedule.interval
    ? `Tự động tạo đề xuất mới: ${scheduleLabel(schedule.interval)}. Mỗi đợt vẫn chờ Ban quản lý duyệt.`
    : "Không tự động tạo đề xuất mới. Bạn tạo thủ công khi cần.";

export const scheduleChoiceOf = (schedule: AssignmentSchedule | null): ProposalScheduleChoice =>
  schedule?.enabled && schedule.interval ? schedule.interval : "NONE";

/** Responses carry the contract spelling; requests keep the short form. */
export const DELAY_FROM_BACKEND: Record<string, AutoAssignmentDelay> = { IMMEDIATE: "IMMEDIATE", "2_HOURS": "2H", "5_HOURS": "5H", "1_DAY": "1D", "3_DAYS": "3D", "2H": "2H", "5H": "5H", "1D": "1D", "3D": "3D" };

export const delayFromBackend = (value: string | null | undefined): AutoAssignmentDelay => DELAY_FROM_BACKEND[String(value)] || "IMMEDIATE";
export const delayLabel = (value: AutoAssignmentDelay) => DELAY_OPTIONS.find((option) => option.value === value)?.label || "Ngay lập tức";

export const BATCH_STATUS_LABELS: Record<string, string> = { BUILDING: "Đang dựng bảng", READY: "Chờ duyệt", CONFIRMED: "Đã duyệt", CANCELLED: "Đã hủy", EXPIRED: "Hết hạn" };
export const ITEM_STATUS_LABELS: Record<string, string> = { PENDING: "Đang chờ AI", PROPOSED: "Có đề xuất", EMPTY: "Chưa có ứng viên", DESELECTED: "Đã bỏ chọn", ASSIGNED: "Đã phân công", SKIPPED_MANUAL_WON: "Đã phân tay trước" };

/** §5.1: the three states in which a job is still going to do something. */
export const ACTIVE_JOB_STATUSES = ["SCHEDULED_GRACE", "PRIMARY_RUNNING", "FALLBACK_RUNNING"];
const JOB_STATUS_LABELS: Record<string, string> = {
  SCHEDULED_GRACE: "Đang chờ tới lượt AI",
  PRIMARY_RUNNING: "Đang chọn kỹ thuật viên",
  FALLBACK_RUNNING: "Đang dùng phương án dự phòng",
};
const JOB_TRIGGER_LABELS: Record<string, string> = {
  INITIAL_AUTO: "Tới lịch phân việc tự động",
  REASSIGN_REJECTED: "Kỹ thuật viên từ chối",
  REASSIGN_SILENT: "Kỹ thuật viên quá hạn nhận việc",
  COORDINATOR_PROPOSAL: "Bảng đề xuất của BQL",
};

export const jobStatusLabel = (job: AssignmentJob) => JOB_STATUS_LABELS[job.status] || job.status;
export const jobTriggerLabel = (job: AssignmentJob) => (job.trigger ? JOB_TRIGGER_LABELS[job.trigger] || job.trigger : "Không rõ nguồn");

/** §6.2: cancelling is for the P1/P2 window after a rejection and nothing else.
 *
 *  The backend sends `cancellable`; this recomputes the same rule so a response
 *  from an older deployment still renders the button correctly rather than
 *  offering it everywhere. Both have to agree — the API refuses the rest. */
export function canCancelJob(job: AssignmentJob) {
  if (typeof job.cancellable === "boolean") return job.cancellable;
  return job.mode === "DIRECT" && job.trigger === "REASSIGN_REJECTED" && job.status === "SCHEDULED_GRACE";
}

export const activeJobs = (jobs: AssignmentJob[]) => jobs.filter((job) => job.mode === "DIRECT" && ACTIVE_JOB_STATUSES.includes(job.status));

/** §4.1 / decision 3: an inactive technician is never offered, anywhere. */
export const activeTechnicians = (roster: TechnicianSummary[]) => roster.filter((technician) => technician.is_active);

export const technicianLabel = (technician: TechnicianSummary) => technician.full_name || technician.user_id.slice(0, 8);

export const AI_GROUP_LABEL = "AI đề xuất";
export const COORDINATOR_GROUP_LABEL = "BQL bổ sung";

export type TechnicianChoice = { id: string; name: string };
export type TechnicianChoiceGroup = { label: string; choices: TechnicianChoice[] };

/** The two meanings a name in the picker can have, kept visibly apart.
 *
 *  The first group is the one technician the model actually proposed for this
 *  row. The second is the rest of the active roster: legitimate choices, but
 *  the coordinator's, not the AI's (decision 3). Collapsing them would tell the
 *  coordinator the AI had vetted people it never saw. */
export function technicianChoiceGroups(item: AssignmentProposalItem, roster: TechnicianSummary[]): TechnicianChoiceGroup[] {
  const active = activeTechnicians(roster);
  const proposedId = item.proposed_technician_id;
  const proposed = active.find((technician) => technician.user_id === proposedId);
  const groups: TechnicianChoiceGroup[] = [];
  if (proposed) groups.push({ label: AI_GROUP_LABEL, choices: [{ id: proposed.user_id, name: technicianLabel(proposed) }] });
  const rest = active.filter((technician) => technician.user_id !== proposedId);
  if (rest.length > 0) groups.push({ label: COORDINATOR_GROUP_LABEL, choices: rest.map((technician) => ({ id: technician.user_id, name: technicianLabel(technician) })) });
  return groups;
}

/** True when the row now carries someone other than the model's suggestion. */
export function isCoordinatorOverride(item: AssignmentProposalItem) {
  const final = item.final_technician_id;
  if (!final) return false;
  return final !== item.proposed_technician_id;
}

export const overrideLabel = (item: AssignmentProposalItem) => (item.proposed_technician_id ? "BQL thay đổi" : COORDINATOR_GROUP_LABEL);

// ---------------------------------------------------------------------------
// The three dispatch queues (§2.5, §4.7, §4.8)
// ---------------------------------------------------------------------------

/** §6.2 / §14.3: three reassignments are allowed, the fourth is a human's job. */
export const REASSIGNMENT_CAP = 3;

export type ManualQueueRow = {
  ticket: CoordinatorTicket;
  reason: string;
  /** A ticket at the cap is an instruction, not a statistic (§14.3). */
  urgent: boolean;
};

const UNASSIGNED_STATUSES = ["APPROVED"];

/** Tickets a human has to place: the switch is off, this ticket is paused, the
 *  AI ran out of options, or the reassignment cap has been reached.
 *
 *  An approved ticket merely waiting for its configured delay is **not** here —
 *  it belongs to the automatic queue, and mixing the two would make the switch
 *  look broken every time a delay is longer than a few minutes. */
export function manualAssignmentQueue(tickets: CoordinatorTicket[], jobs: AssignmentJob[], autoEnabled: boolean): ManualQueueRow[] {
  const manualRequired = new Map<string, AssignmentJob>();
  const inFlight = new Set<string>();
  for (const job of jobs) {
    if (ACTIVE_JOB_STATUSES.includes(job.status)) {
      for (const ticketId of job.ticket_ids) inFlight.add(ticketId);
      continue;
    }
    if (job.status !== "MANUAL_REQUIRED" && job.status !== "FAILED") continue;
    for (const ticketId of job.ticket_ids) manualRequired.set(ticketId, job);
  }

  const rows: ManualQueueRow[] = [];
  for (const ticket of tickets) {
    if (!UNASSIGNED_STATUSES.includes(ticket.status) || ticket.active_assignment_id) continue;
    if (ticket.classification_status === "MANUAL_REVIEW") continue;
    // A ticket the AI is currently holding belongs to the automatic queue. It
    // may carry an older failed job, and listing it in both places would read
    // as two different tickets needing two different things.
    if (inFlight.has(ticket.id)) continue;
    const capped = (ticket.reassignment_count || 0) > REASSIGNMENT_CAP;
    const job = manualRequired.get(ticket.id);
    const paused = Boolean(ticket.auto_assignment_paused);
    if (capped) rows.push({ ticket, reason: `Đã đổi kỹ thuật viên ${ticket.reassignment_count} lần — vượt trần ${REASSIGNMENT_CAP}, bắt buộc phân tay.`, urgent: true });
    else if (job) rows.push({ ticket, reason: jobFailureReason(job), urgent: true });
    else if (paused) rows.push({ ticket, reason: ticket.auto_assignment_pause_reason || "Tự động đang tạm dừng cho ticket này.", urgent: true });
    else if (!autoEnabled) rows.push({ ticket, reason: "Phân việc tự động đang tắt.", urgent: false });
  }
  return rows.sort((a, b) => Number(b.urgent) - Number(a.urgent) || priorityRank(a.ticket) - priorityRank(b.ticket) || Date.parse(a.ticket.created_at) - Date.parse(b.ticket.created_at));
}

/** §2.12b: Priority descending, then oldest first. */
const priorityRank = (ticket: CoordinatorTicket) => (ticket.priority === "P3" ? 0 : ticket.priority === "P2" ? 1 : ticket.priority === "P1" ? 2 : 3);

const JOB_ERROR_REASONS: Record<string, string> = {
  NO_CANDIDATES: "Không còn kỹ thuật viên phù hợp — cần phân tay.",
  MODEL_FAILED: "AI không chọn được người sau cả hai lượt — cần phân tay.",
};

/** §9: a code turns into an instruction, never into raw model output. */
export function jobFailureReason(job: AssignmentJob) {
  if (job.error_code && JOB_ERROR_REASONS[job.error_code]) return JOB_ERROR_REASONS[job.error_code];
  if (job.decision_reason) return job.decision_reason;
  return "Lượt phân việc tự động không hoàn tất — cần phân tay.";
}

// ---------------------------------------------------------------------------
// The proposal board (§4.6)
// ---------------------------------------------------------------------------

/** Rows that would actually create an assignment if the batch were confirmed. */
export const assignableItems = (batch: AssignmentProposalBatch) => batch.items.filter((item) => item.status === "PROPOSED" && Boolean(item.final_technician_id));

/** Confirming assigns the placed rows and nothing else.
 *
 *  A partial confirmation is valid: rows left in the unassigned column simply
 *  stay unassigned, and the bar says so before the button is pressed. What is
 *  *not* valid is confirming a board with nothing on it — the backend refuses
 *  it (`PROPOSAL_NOTHING_TO_ASSIGN`), and offering the button anyway would ask
 *  the coordinator to discover that by being rejected.
 *
 *  This screen never carries `continue_auto_assignment`. Turning the V4 DIRECT
 *  switch on is a different decision with different consequences, and folding
 *  it into "Xác nhận và phân việc" would start assigning future tickets without
 *  a human as a side effect of confirming this one table. */
export const canConfirmBatch = (batch: AssignmentProposalBatch, expired: boolean) =>
  !expired && batch.status === "READY" && assignableItems(batch).length > 0;

export const NOTHING_PLACED_HINT = "Chưa có ticket nào được đặt vào kỹ thuật viên. Hãy kéo ít nhất một ticket, hoặc hủy đề xuất.";

export type ConfirmSummary = {
  ticketCount: number;
  itemCount: number;
  overrideCount: number;
  unassignedCount: number;
  consequence: string;
};

/** What the confirmation dialog states before anything is written (§4.6 item 5). */
export function confirmSummary(batch: AssignmentProposalBatch): ConfirmSummary {
  const live = assignableItems(batch);
  const { unplaced } = draftSummary(batch);
  return {
    ticketCount: live.reduce((total, item) => total + Math.max(item.ticket_ids.length, 1), 0),
    itemCount: live.length,
    overrideCount: live.filter(isCoordinatorOverride).length,
    unassignedCount: unplaced,
    consequence: unassignedConsequence(unplaced),
  };
}

// ---------------------------------------------------------------------------
// Errors (§9: an instruction, never an internal)
// ---------------------------------------------------------------------------

const ERROR_MESSAGES: Record<string, string> = {
  AUTO_ASSIGNMENT_PROPOSAL_REQUIRED: "Phân việc tự động trực tiếp chỉ bật được qua một đề xuất đã xác nhận.",
  PROPOSAL_EXPIRED: "Đề xuất đã quá 10 phút. Hãy bấm “Hủy đề xuất” rồi tạo đề xuất mới.",
  PROPOSAL_NOT_READY: "Đề xuất chưa sẵn sàng để thao tác. Hãy tải lại danh sách.",
  PROPOSAL_NOTHING_TO_ASSIGN: NOTHING_PLACED_HINT,
  CONFLICT_VERSION: "Đề xuất đã thay đổi từ lúc bạn mở. Hãy tải lại rồi xác nhận lại.",
  ACTIVE_ASSIGNMENT_EXISTS: "Ticket này vừa được phân tay nên không thể gán lại. Hãy tải lại danh sách.",
  ASSIGNMENT_JOB_ALREADY_ACTIVE: "Ticket này đang nằm trong một lượt phân việc chưa kết thúc.",
  TECHNICIAN_NOT_ELIGIBLE: "Kỹ thuật viên này không còn hoạt động. Hãy chọn người khác trong danh sách.",
  TECHNICIAN_NOT_FOUND: "Không tìm thấy kỹ thuật viên. Hãy tải lại danh sách nhân sự.",
  NO_CANDIDATES: "Không còn kỹ thuật viên phù hợp cho hạng mục này. Hãy phân tay hoặc bổ sung kỹ năng.",
  INVALID_STATUS_TRANSITION: "Thao tác này không còn hợp lệ ở trạng thái hiện tại. Hãy tải lại rồi thử lại.",
};

/** Maps a backend error code onto something a coordinator can act on. */
export function assignmentErrorMessage(error: unknown, fallback = "Không thực hiện được thao tác.") {
  const code = typeof error === "object" && error !== null && "code" in error ? String((error as { code?: unknown }).code) : "";
  if (code && ERROR_MESSAGES[code]) return ERROR_MESSAGES[code];
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** mm:ss, floored at zero so an expired timer never counts upward. */
export function formatCountdown(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// State 1 — the dashboard's two actions
// ---------------------------------------------------------------------------

/** A report the system may approve on its own.
 *
 *  `available_actions` is the backend's own answer to "is this approvable?",
 *  and it says APPROVE only once classification finished with a Category and a
 *  Priority. Anything still under manual review, still missing a Category, or
 *  already past NEW is therefore excluded without this having to re-derive the
 *  rule — which is the point, because getting it wrong here would approve a
 *  report nobody classified. */
export const isAutoApprovable = (ticket: CoordinatorTicket) => ticket.available_actions.includes("APPROVE");

export const autoApprovableTickets = (tickets: CoordinatorTicket[]) => tickets.filter(isAutoApprovable);

export const AUTO_APPROVE_NOTICE =
  "Hệ thống chỉ tự động duyệt những ticket đã có đủ Category và Priority từ kết quả phân loại. " +
  "Ticket chưa có kết quả phân loại sẽ tiếp tục chờ xử lý, không bị duyệt tự động.";

// ---------------------------------------------------------------------------
// State 2 — the two preparation queues
// ---------------------------------------------------------------------------

/** Not approved yet: either the analysis could not settle it, or nobody has
 *  pressed approve on it. Both need a human before they can be assigned. */
export function awaitingApprovalQueue(tickets: CoordinatorTicket[]): CoordinatorTicket[] {
  return tickets.filter((ticket) => ticket.status === "NEW").sort(byPriorityThenAge);
}

/** Approved and unassigned: exactly what a proposal batch draws from.
 *
 *  Mirrors `AssignmentCandidateService.eligible_ticket_query(include_paused=True)`
 *  on the backend: a PROPOSAL batch is the recovery path for a ticket paused by
 *  `AUTO_ASSIGNMENT_DISABLED` or `NO_CANDIDATES` (that pause is exactly what a
 *  coordinator opening this table is here to clear), so those stay in this
 *  queue. The reassignment cap is the one pause reason that survives even a
 *  PROPOSAL confirm (§11 assumption 4 / §14.3) and belongs in the manual queue
 *  instead — see `manualAssignmentQueue`. */
export function awaitingAssignmentQueue(tickets: CoordinatorTicket[]): CoordinatorTicket[] {
  return tickets
    .filter((ticket) => ticket.status === "APPROVED" && !ticket.active_assignment_id && (ticket.reassignment_count || 0) <= REASSIGNMENT_CAP)
    .sort(byPriorityThenAge);
}

// ---------------------------------------------------------------------------
// Case-aware source queues (§4.2, §7.9)
// ---------------------------------------------------------------------------

/** A source-queue row is either one standalone ticket or one *materialized*
 *  incident case.
 *
 *  A `DERIVED` cluster (`CoordinatorCluster.status === "DERIVED"`) is only a
 *  suggestion the `/manager/clusters` screen computes on the fly — it has no
 *  durable `IncidentCase` id, so `AssignmentCandidateService.case_draft` on
 *  the backend has never heard of it and cannot lock it as a work item. If
 *  this queue rendered it as one "Case · N ticket" card and let a coordinator
 *  approve/assign it as a unit, the backend would happily process each
 *  member as an unrelated standalone ticket — a UI/backend split. So only a
 *  materialized case (any other `status`, e.g. `OPEN`) is grouped here; a
 *  derived cluster's members fall through to the ordinary per-ticket rows,
 *  exactly as they would if no clustering existed.
 *
 *  A materialized case, on the other hand, is deliberately kept as one work
 *  item in both queues, mirroring `case_draft`'s own all-or-nothing rule
 *  (backend now refuses to draft a case that is not fully ready) and the
 *  atomic `/clusters/{id}/assign` endpoint: the UI must never offer to
 *  approve or assign half a case.
 */
export type AssignmentQueueEntry =
  | { kind: "ticket"; ticket: CoordinatorTicket; ticketCount: number }
  | { kind: "case"; caseRow: CoordinatorCluster; tickets: CoordinatorTicket[]; ticketCount: number };

const materializedCases = (clusters: CoordinatorCluster[]) => clusters.filter((cluster) => cluster.status !== "DERIVED");

/** Mirrors `AssignmentCandidateService.eligible_ticket_query(include_paused=True)`
 *  (see `awaitingAssignmentQueue` above) at the per-ticket level, so a case's
 *  readiness can be judged member by member before deciding the case itself
 *  is ready. */
const isReadyForAssignment = (ticket: CoordinatorTicket) =>
  ticket.status === "APPROVED"
  && ticket.classification_status === "RESOLVED"
  && Boolean(ticket.category_id)
  && Boolean(ticket.priority)
  && !ticket.duplicate_of_ticket_id
  && !ticket.active_assignment_id
  && (ticket.reassignment_count || 0) <= REASSIGNMENT_CAP;

/** Builds the two source queues, grouping each materialized case into one
 *  entry instead of listing its members as unrelated rows.
 *
 *  - A case belongs in the approval queue as long as *any* member is still
 *    `NEW`. Approving it (`onApproveCase`) only moves the members the backend
 *    itself judges approvable (`POST /clusters/{id}/approve` already skips
 *    what it can't); a member left needing manual review keeps the whole
 *    case in this queue rather than promoting it to "ready" early.
 *  - A case only reaches the ready-for-assignment queue once *every* member
 *    independently satisfies `isReadyForAssignment` — the same "complete or
 *    not at all" rule `case_draft` enforces server-side, so this queue's
 *    count and the backend's next proposal always agree.
 *
 *  A ticket already accounted for by a case (materialized or not) is never
 *  also listed as a standalone row — that would double count it and let a
 *  coordinator peel it off the case through the wrong control. */
export function assignmentSourceQueues(tickets: CoordinatorTicket[], clusters: CoordinatorCluster[]): { approval: AssignmentQueueEntry[]; ready: AssignmentQueueEntry[] } {
  const byId = new Map(tickets.map((ticket) => [ticket.id, ticket]));
  const cases = materializedCases(clusters)
    .map((caseRow) => ({
      caseRow,
      tickets: caseRow.tickets.map((member) => byId.get(member.id)).filter((ticket): ticket is CoordinatorTicket => Boolean(ticket)),
    }))
    // A case whose members are not in the ticket list this workspace was
    // given (e.g. all closed/terminal) has nothing left to coordinate here.
    .filter((entry) => entry.tickets.length > 0);
  const memberIds = new Set(cases.flatMap((entry) => entry.tickets.map((ticket) => ticket.id)));

  const approval: AssignmentQueueEntry[] = [
    ...cases
      .filter((entry) => entry.tickets.some((ticket) => ticket.status === "NEW"))
      .map((entry) => ({ kind: "case" as const, ...entry, ticketCount: entry.tickets.length })),
    ...tickets
      .filter((ticket) => !memberIds.has(ticket.id) && ticket.status === "NEW")
      .map((ticket) => ({ kind: "ticket" as const, ticket, ticketCount: 1 })),
  ];
  const ready: AssignmentQueueEntry[] = [
    ...cases
      .filter((entry) => entry.tickets.every(isReadyForAssignment))
      .map((entry) => ({ kind: "case" as const, ...entry, ticketCount: entry.tickets.length })),
    ...tickets
      .filter((ticket) => !memberIds.has(ticket.id) && isReadyForAssignment(ticket))
      .map((ticket) => ({ kind: "ticket" as const, ticket, ticketCount: 1 })),
  ];

  const leadTicket = (entry: AssignmentQueueEntry) => (entry.kind === "case" ? [...entry.tickets].sort(byPriorityThenAge)[0] : entry.ticket);
  const entryOrder = (left: AssignmentQueueEntry, right: AssignmentQueueEntry) => byPriorityThenAge(leadTicket(left), leadTicket(right));

  return { approval: approval.sort(entryOrder), ready: ready.sort(entryOrder) };
}

/** Total ticket count across a mix of standalone-ticket and case entries —
 *  what the workspace header and queue counters show, since a case entry
 *  counts as several tickets, not one row. */
export const queueTicketCount = (entries: AssignmentQueueEntry[]) => entries.reduce((total, entry) => total + entry.ticketCount, 0);

/** Auto-approval still calls the normal per-ticket endpoint for every ticket
 *  in the queue, case members included — grouping here is a display and
 *  work-unit concern for manual approve/assign, never a shortcut around the
 *  per-ticket status-transition rules. */
export const approvalTicketsFromEntries = (entries: AssignmentQueueEntry[]): CoordinatorTicket[] =>
  entries.flatMap((entry) => (entry.kind === "case" ? entry.tickets : [entry.ticket]));

/** §2.12b: Priority descending, then oldest first. */
const byPriorityThenAge = (a: CoordinatorTicket, b: CoordinatorTicket) =>
  priorityRank(a) - priorityRank(b) || Date.parse(a.created_at) - Date.parse(b.created_at);

/** Why this ticket cannot be assigned yet, in one line. */
export function awaitingApprovalReason(ticket: CoordinatorTicket) {
  if (ticket.classification_status === "MANUAL_REVIEW") return "Chờ duyệt phân loại thủ công";
  if (isAutoApprovable(ticket)) return "Đủ điều kiện tự động duyệt";
  if (!ticket.category) return "Chưa có danh mục";
  if (!ticket.priority) return "Chưa có mức ưu tiên";
  return "Chờ Ban quản lý duyệt";
}

// ---------------------------------------------------------------------------
// State 3 — the assignment draft board
// ---------------------------------------------------------------------------

/** A, B, ... Z, AA, AB, ... — spreadsheet-column naming, so the sequence keeps
 *  working past the 26th row instead of repeating itself. */
export function referenceName(index: number) {
  let name = "";
  let value = index;
  do {
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26) - 1;
  } while (value >= 0);
  return `Ticket ${name}`;
}

/** Reference names are keyed to the batch's own row order, not to where a row
 *  currently sits. Dragging a ticket to another technician must not rename it —
 *  the name exists so a coordinator can say "put Ticket C with An" out loud. */
export function referenceNames(batch: AssignmentProposalBatch): Map<string, string> {
  return new Map(batch.items.map((item, index) => [item.id, referenceName(index)]));
}

export type DraftColumn = { id: string; name: string; items: AssignmentProposalItem[] };
export type DraftBoard = { unassigned: AssignmentProposalItem[]; technicians: DraftColumn[] };

export const UNASSIGNED_COLUMN = "__unassigned__";

/** Splits the batch into the two halves the board renders.
 *
 *  A row is "placed" when it carries a technician and is still selected; a row
 *  that was deselected, or that the model could not fill, is unplaced work
 *  sitting in the left column waiting for a decision. Every active technician
 *  gets a column whether or not they hold anything, because an empty column is
 *  the drop target that puts the first ticket on them. */
export function draftBoard(batch: AssignmentProposalBatch, roster: TechnicianSummary[]): DraftBoard {
  const columns = new Map<string, DraftColumn>(
    activeTechnicians(roster).map((technician) => [technician.user_id, { id: technician.user_id, name: technicianLabel(technician), items: [] }]),
  );
  const unassigned: AssignmentProposalItem[] = [];
  for (const item of batch.items) {
    const technicianId = item.status === "PROPOSED" ? item.final_technician_id : null;
    const column = technicianId ? columns.get(technicianId) : undefined;
    if (column) column.items.push(item);
    else unassigned.push(item);
  }
  return { unassigned, technicians: [...columns.values()] };
}

/** The change a drop implies. `null` means the drop is a no-op and no request
 *  should be sent — dropping a row back where it already was is not an edit. */
export function dropChange(item: AssignmentProposalItem, columnId: string): { selected?: boolean; technician_id?: string } | null {
  if (columnId === UNASSIGNED_COLUMN) {
    const placed = item.status === "PROPOSED" && Boolean(item.final_technician_id);
    return placed ? { selected: false } : null;
  }
  if (item.status === "PROPOSED" && item.final_technician_id === columnId) return null;
  return { technician_id: columnId };
}

/** Rows the confirm button will actually turn into assignments. */
export const placedItems = (batch: AssignmentProposalBatch) => assignableItems(batch);

export const placedTicketCount = (batch: AssignmentProposalBatch) =>
  placedItems(batch).reduce((total, item) => total + Math.max(item.ticket_ids.length, 1), 0);

// ---------------------------------------------------------------------------
// Assignment history
// ---------------------------------------------------------------------------

export type DraftCardRow = {
  ticketId: string;
  code: string;
  location: string;
  category: string | null;
  priority: "P1" | "P2" | "P3" | null;
  createdAt: string | null;
  slaDueAt: string | null;
};

/** The rows a draft card renders: every member of the work item, with the
 *  facts a coordinator needs to place it. Falls back to the bare ticket ids so
 *  a response without member detail still renders one row per ticket rather
 *  than an empty card. */
export function draftCardRows(item: AssignmentProposalItem, fallbackCode: (id: string) => string): DraftCardRow[] {
  if (item.members && item.members.length > 0) {
    return item.members.map((member) => ({
      ticketId: member.ticket_id,
      code: member.display_code || fallbackCode(member.ticket_id),
      location: member.location_label || "Chưa xác định",
      category: member.category,
      priority: member.priority,
      createdAt: member.created_at,
      slaDueAt: member.sla_due_at,
    }));
  }
  return item.ticket_ids.map((ticketId) => ({
    ticketId,
    code: fallbackCode(ticketId),
    location: "Chưa xác định",
    category: item.ticket_category,
    priority: null,
    createdAt: null,
    slaDueAt: null,
  }));
}

export type HistoryRow = {
  record: AssignmentHistoryRecord;
  /** Whether the schedule that followed was a repeat, a decline, or unasked. */
  followup: string;
  /** SYSTEM-opened batches were the scheduler's; the confirm never is. */
  openedBySystem: boolean;
};

/** Confirmed rounds, newest first, straight from their frozen snapshots.
 *
 *  Nothing here looks at a live ticket or profile — the backend already refused
 *  to, and re-deriving anything on this side would put the same bug back one
 *  layer up. `has_snapshot` is false only for rounds confirmed before snapshots
 *  existed; those are shown as a dated record with no rows rather than
 *  reconstructed, because a reconstruction is a guess about the past. */
export function historyRows(records: AssignmentHistoryRecord[]): HistoryRow[] {
  return [...records]
    .sort((a, b) => Date.parse(b.confirmed_at || "") - Date.parse(a.confirmed_at || ""))
    .map((record) => ({
      record,
      followup: record.followup_schedule ? scheduleLabel(record.followup_schedule) : "Không ghi nhận",
      openedBySystem: record.created_by_type === "SYSTEM",
    }));
}

/** Who confirmed the round. Always a person — a scheduled batch is opened by
 *  the system but never confirmed by it (§8.1). */
export const historyConfirmedBy = (record: AssignmentHistoryRecord) =>
  record.confirmed_by_name || "Ban quản lý";

/** How the round started, which is a separate fact from who confirmed it. */
export const historyOrigin = (record: AssignmentHistoryRecord) =>
  record.created_by_type === "SYSTEM" ? "Lịch tự động tạo đề xuất" : "BQL tạo đề xuất";

// ---------------------------------------------------------------------------
// DIRECT auto-assignment: one way out, one way in
// ---------------------------------------------------------------------------

export const DIRECT_DISABLE_LABEL = "Tắt phân việc tự động";

/** Shown wherever DIRECT is off. Note what it is *not*: a disabled button.
 *
 *  A greyed-out "Bật" would tell a coordinator the action exists and they are
 *  not allowed it. The action does not exist — activation is a consequence of
 *  confirming real work, so the guidance names that path instead. */
export const DIRECT_OFF_GUIDANCE =
  "Phân việc tự động đang tắt. Hãy tạo và xác nhận một đề xuất phân việc ban đầu để kích hoạt.";

export const DIRECT_DISABLE_NOTICE =
  "Sau khi tắt, hệ thống sẽ không tự phân công cho các ticket đủ điều kiện tiếp theo; " +
  "Ban quản lý phải phân tay hoặc tạo đề xuất phân việc. " +
  "Các ticket đã phân công và lịch lặp lại tạo đề xuất không bị ảnh hưởng.";

/** What DIRECT offers right now.
 *
 *  Two shapes, and there is no third: when it is on, a control that stops it;
 *  when it is off, a sentence explaining how it starts. The union has no
 *  `enable` member on purpose — a component cannot render a control this type
 *  cannot describe. */
export type DirectControl =
  | { kind: "disable"; label: string; notice: string; delay: string }
  | { kind: "guidance"; message: string };

export function directControl(settings: AutoAssignmentSettings | null): DirectControl {
  if (!settings?.enabled) return { kind: "guidance", message: DIRECT_OFF_GUIDANCE };
  return {
    kind: "disable",
    label: DIRECT_DISABLE_LABEL,
    notice: DIRECT_DISABLE_NOTICE,
    delay: delayLabel(delayFromBackend(settings.activation_delay)),
  };
}

export const directStatusLabel = (settings: AutoAssignmentSettings | null) =>
  settings?.enabled
    ? `Phân việc tự động đang bật · ${delayLabel(delayFromBackend(settings.activation_delay))}`
    : "Phân việc tự động đang tắt";

/** Whether confirming *this* batch is what turned DIRECT on.
 *
 *  Read from the confirmed batch rather than by diffing the settings before and
 *  after: the backend already decided, and re-deriving it here could disagree
 *  with the audit trail over which confirmation earned the switch. */
export const directActivatedByConfirmation = (batch: AssignmentProposalBatch) =>
  batch.continue_auto_assignment === true;

export const DIRECT_ACTIVATED_MESSAGE =
  "Đã bật phân việc tự động. Từ giờ hệ thống sẽ tự phân công cho ticket đủ điều kiện; " +
  "bạn có thể tắt lại bất cứ lúc nào.";

// ---------------------------------------------------------------------------
// State 2 — while the model is answering
// ---------------------------------------------------------------------------

/** What the model is weighing, in the order the request presents it.
 *
 *  This is progress copy over a real request, not a claim about the decision:
 *  the candidate list is fixed by the backend before the call, and nothing here
 *  widens it. The four labels name what RULE_ENGINE_V1 actually weighs, in the
 *  order it weighs them — so a coordinator who reads them and then reads the
 *  decision reason sees the same story twice, not two different ones. */
export const BUILDING_STEPS = [
  "Thứ tự ưu tiên ticket",
  "Chuyên môn kỹ thuật viên",
  "Khối lượng công việc hiện tại",
  "Cân đối tải trong cả đợt",
];

/** Roughly how long each step is shown. The real call has no progress events,
 *  so this paces the display; it never decides when the batch is READY. */
export const BUILDING_STEP_MS = 2500;

export type BuildingStep = { label: string; state: "done" | "active" | "waiting" };

/** The bullet list State 2 renders. Only one step is ever `active`, so only one
 *  thing animates — the rest are static, which is what makes the animation
 *  readable and what keeps a reduced-motion fallback meaningful. */
export function buildingSteps(elapsedMs: number): BuildingStep[] {
  const reached = Math.floor(Math.max(0, elapsedMs) / BUILDING_STEP_MS);
  // The last step stays active rather than completing: the batch is not done
  // until the backend says READY, and a full checklist would claim otherwise.
  const current = Math.min(reached, BUILDING_STEPS.length - 1);
  return BUILDING_STEPS.map((label, index) => ({
    label,
    state: index < current ? "done" : index === current ? "active" : "waiting",
  }));
}

// ---------------------------------------------------------------------------
// The draft summary and its consequence
// ---------------------------------------------------------------------------

export type DraftSummary = { placed: number; unplaced: number; total: number };

/** Ticket members, not rows: a case is several tickets and the header count has
 *  to say so. */
export function draftSummary(batch: AssignmentProposalBatch): DraftSummary {
  const total = batch.items.reduce((sum, item) => sum + Math.max(item.ticket_ids.length, 1), 0);
  const placed = placedTicketCount(batch);
  return { placed, unplaced: total - placed, total };
}

/** Stated immediately before confirming, because a partial confirmation is
 *  valid and the tickets left behind are not obvious from the board alone. */
export const unassignedConsequence = (count: number) =>
  count > 0 ? `${count} ticket sẽ tiếp tục ở trạng thái chưa phân công.` : "";

/** The result modal's headline. Technicians are counted distinctly: three rows
 *  on one person is one technician, not three. */
export function assignedResult(batch: AssignmentProposalBatch) {
  const assigned = batch.items.filter((item) => item.status === "ASSIGNED");
  const technicians = new Set(assigned.map((item) => item.final_technician_id).filter(Boolean));
  const leftovers = batch.items.filter((item) => item.status !== "ASSIGNED");
  return {
    ticketCount: assigned.reduce((sum, item) => sum + Math.max(item.ticket_ids.length, 1), 0),
    technicianCount: technicians.size,
    unassignedCount: leftovers.reduce((sum, item) => sum + Math.max(item.ticket_ids.length, 1), 0),
  };
}
