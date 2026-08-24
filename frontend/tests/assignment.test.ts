/** Rules the assignment workspace renders — run with `npm test`.
 *
 *  These cover what the UI must not get wrong and a screenshot cannot show: an
 *  inactive technician never appears anywhere, a case row shows every member
 *  rather than its first ticket, a reference name survives a drag, a no-op move
 *  sends nothing, an empty board cannot be confirmed, the repeat schedule reads
 *  as a repeat rather than a delay, and a history record comes out of the frozen
 *  snapshot instead of a live row.
 *
 *  `frontend/lib/assignment.ts` has no runtime imports, so Node runs it directly
 *  with type stripping; no bundler or test framework is involved.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  AI_GROUP_LABEL,
  COORDINATOR_GROUP_LABEL,
  BUILDING_STEP_MS,
  DIRECT_ACTIVATED_MESSAGE,
  DIRECT_DISABLE_LABEL,
  DIRECT_DISABLE_NOTICE,
  DIRECT_OFF_GUIDANCE,
  NOTHING_PLACED_HINT,
  SCHEDULE_OPTIONS,
  UNASSIGNED_COLUMN,
  activeJobs,
  activeTechnicians,
  approvalTicketsFromEntries,
  assignedResult,
  assignmentErrorMessage,
  assignmentSourceQueues,
  autoApprovableTickets,
  awaitingApprovalQueue,
  awaitingApprovalReason,
  awaitingAssignmentQueue,
  buildingSteps,
  canCancelJob,
  canConfirmBatch,
  confirmSummary,
  delayFromBackend,
  directActivatedByConfirmation,
  directControl,
  directStatusLabel,
  draftBoard,
  draftCardRows,
  draftSummary,
  dropChange,
  formatCountdown,
  historyConfirmedBy,
  historyOrigin,
  historyRows,
  isAutoApprovable,
  isCoordinatorOverride,
  jobFailureReason,
  manualAssignmentQueue,
  placedTicketCount,
  queueTicketCount,
  referenceName,
  referenceNames,
  scheduleChoiceOf,
  scheduleLabel,
  scheduleSummary,
  technicianChoiceGroups,
  unassignedConsequence,
} from "../lib/assignment.ts";
import type { AssignmentHistoryRecord, AssignmentJob, AutoAssignmentSettings as AutoAssignmentSettingsType, AssignmentProposalBatch, AssignmentProposalItem, AssignmentProposalItemMember, CoordinatorCluster, CoordinatorTicket, TechnicianSummary } from "../types/api.ts";

const code = (id: string) => `PA-${id.slice(0, 6).toUpperCase()}`;

function technician(id: string, overrides: Partial<TechnicianSummary> = {}): TechnicianSummary {
  return { user_id: id, full_name: `KTV ${id}`, phone_e164: null, is_active: true, is_available: true, skill_category_ids: [], ...overrides };
}

function ticket(id: string, overrides: Partial<CoordinatorTicket> = {}): CoordinatorTicket {
  return {
    id,
    reporter_user_id: "r",
    reporter: null,
    source_unit_id: "u",
    location_label: "Hành lang tầng 10",
    description: null,
    status: "APPROVED",
    classification_status: "RESOLVED",
    display_code: null,
    category_id: "c",
    category: "ELEVATOR",
    priority: "P2",
    severity: null,
    red_flag_detected: false,
    score_total: null,
    sla_due_at: null,
    created_at: "2026-08-24T00:00:00Z",
    updated_at: "2026-08-24T00:00:00Z",
    available_actions: [],
    reassignment_count: 0,
    auto_assignment_paused: false,
    auto_assignment_pause_reason: null,
    active_assignment_id: null,
    active_assignment_status: null,
    active_technician_id: null,
    active_technician_name: null,
    latest_analysis: null,
    agent_questions: [],
    attachments: [],
    ...overrides,
  };
}

function cluster(id: string, ticketIds: string[], overrides: Partial<CoordinatorCluster> = {}): CoordinatorCluster {
  return {
    id,
    category_id: "c",
    category: "Rò rỉ nước",
    building_id: "b",
    building: "Tòa A",
    floor_label: "Tầng 10",
    density: ticketIds.length,
    status: "OPEN",
    closed: false,
    window_start: "2026-08-21T00:00:00Z",
    window_end: "2026-08-24T00:00:00Z",
    created_at: "2026-08-21T00:00:00Z",
    tickets: ticketIds.map((ticketId) => ({
      id: ticketId,
      display_code: code(ticketId),
      description: null,
      status: "APPROVED",
      priority: "P2",
      location_label: "Hành lang tầng 10",
      unit_code: null,
      floor_label: "Tầng 10",
      created_at: "2026-08-21T00:00:00Z",
      active_assignment_id: null,
      active_assignment_status: null,
      active_technician_id: null,
      active_technician_name: null,
    })),
    ...overrides,
  };
}

function job(overrides: Partial<AssignmentJob> = {}): AssignmentJob {
  return {
    id: "job-1",
    mode: "DIRECT",
    status: "SCHEDULED_GRACE",
    trigger: "REASSIGN_REJECTED",
    work_item_type: "TICKET",
    work_item_id: "t1",
    ticket_ids: ["t1"],
    execute_after: "2026-08-24T00:05:00Z",
    selected_technician_id: null,
    selected_technician_name: null,
    completed_model: null,
    decision_reason: null,
    error_code: null,
    created_at: "2026-08-24T00:00:00Z",
    completed_at: null,
    cancellable: true,
    ...overrides,
  };
}

function member(ticketId: string, overrides: Partial<AssignmentProposalItemMember> = {}): AssignmentProposalItemMember {
  return {
    ticket_id: ticketId,
    display_code: code(ticketId),
    location_label: "Hành lang tầng 10",
    category: "ELEVATOR",
    priority: "P2",
    created_at: "2026-08-24T00:00:00Z",
    sla_due_at: "2026-08-24T03:00:00Z",
    ...overrides,
  };
}

function item(overrides: Partial<AssignmentProposalItem> = {}): AssignmentProposalItem {
  return {
    id: "item-1",
    decision_id: "d1",
    status: "PROPOSED",
    work_item_type: "TICKET",
    work_item_id: "t1",
    ticket_id: "t1",
    ticket_display_code: code("t1abcdef"),
    ticket_description: null,
    ticket_location_label: "Hành lang tầng 10",
    ticket_category: "ELEVATOR",
    ticket_priority: "P2",
    proposed_technician_id: "tech-a",
    proposed_technician_name: "KTV tech-a",
    final_technician_id: "tech-a",
    final_technician_name: "KTV tech-a",
    selected_technician_id: "tech-a",
    selected_technician_name: "KTV tech-a",
    completed_model: "m",
    decided_at: null,
    ticket_ids: ["t1"],
    members: [member("t1", { display_code: code("t1abcdef") })],
    reason: null,
    created_at: "2026-08-24T00:00:00Z",
    updated_at: "2026-08-24T00:00:00Z",
    ...overrides,
  };
}

const batch = (items: AssignmentProposalItem[], overrides: Partial<AssignmentProposalBatch> = {}): AssignmentProposalBatch => ({
  id: "batch-1",
  status: "READY",
  ready_at: "2026-08-24T00:00:00Z",
  expires_at: "2026-08-24T00:10:00Z",
  continue_auto_assignment: null,
  activation_delay: null,
  version: 1,
  created_at: "2026-08-24T00:00:00Z",
  confirmed_at: null,
  cancelled_at: null,
  confirmed_by_user_id: null,
  confirmed_by_name: null,
  items,
  ...overrides,
});

/** A history record as the backend returns it: derived from the snapshot, so
 *  every field is already frozen and none of it is joined to a live row. */
const record = (overrides: Partial<AssignmentHistoryRecord> = {}): AssignmentHistoryRecord => ({
  batch_id: "batch-1",
  confirmed_at: "2026-08-24T01:00:00Z",
  confirmed_by_user_id: "coord-1",
  confirmed_by_name: "Điều phối viên Lan",
  created_by_type: "COORDINATOR",
  ticket_count: 2,
  technician_count: 1,
  items: [],
  followup_schedule: null,
  has_snapshot: true,
  ...overrides,
});

// ---------------------------------------------------------------------------
// Only active technicians are ever offered (decision 3)
// ---------------------------------------------------------------------------

test("an inactive technician is filtered out of the roster", () => {
  const roster = [technician("tech-a"), technician("tech-b", { is_active: false })];
  assert.deepEqual(activeTechnicians(roster).map((row) => row.user_id), ["tech-a"]);
});

test("the picker separates the AI suggestion from the coordinator's additions", () => {
  const roster = [technician("tech-a"), technician("tech-b"), technician("tech-c", { is_active: false })];

  const groups = technicianChoiceGroups(item(), roster);

  assert.deepEqual(groups.map((group) => group.label), [AI_GROUP_LABEL, COORDINATOR_GROUP_LABEL]);
  assert.deepEqual(groups[0].choices.map((choice) => choice.id), ["tech-a"]);
  // tech-b is offered, tech-c is not: inactive never renders.
  assert.deepEqual(groups[1].choices.map((choice) => choice.id), ["tech-b"]);
});

test("a row with no AI suggestion offers only the coordinator group", () => {
  const groups = technicianChoiceGroups(item({ status: "EMPTY", proposed_technician_id: null, final_technician_id: null }), [technician("tech-a")]);

  assert.deepEqual(groups.map((group) => group.label), [COORDINATOR_GROUP_LABEL]);
});

test("an override is recognised only when it differs from the AI choice", () => {
  assert.equal(isCoordinatorOverride(item()), false);
  assert.equal(isCoordinatorOverride(item({ final_technician_id: "tech-b" })), true);
  assert.equal(isCoordinatorOverride(item({ final_technician_id: null })), false);
});

// ---------------------------------------------------------------------------
// A case row renders every member (§4.2)
// ---------------------------------------------------------------------------

test("all members of an incident case are rendered, not just the first", () => {
  const caseItem = item({
    work_item_type: "INCIDENT_CASE",
    work_item_id: "case-1",
    ticket_id: null,
    ticket_ids: ["t1", "t2", "t3"],
    members: [
      member("t1", { display_code: "PA-000001", location_label: "Thang máy A" }),
      member("t2", { display_code: "PA-000002", location_label: "Thang máy A", priority: "P1" }),
      member("t3", { display_code: "PA-000003", location_label: "Thang máy A" }),
    ],
  });

  const rendered = draftCardRows(caseItem, code);

  assert.equal(rendered.length, 3);
  assert.deepEqual(rendered.map((row) => row.code), ["PA-000001", "PA-000002", "PA-000003"]);
  assert.deepEqual(rendered.map((row) => row.priority), ["P2", "P1", "P2"]);
  // The board places tickets, so every row carries what that decision needs.
  assert.ok(rendered.every((row) => row.location && row.createdAt && row.slaDueAt));
});

test("a row with no member detail still renders one card row per ticket", () => {
  const caseItem = item({ work_item_type: "INCIDENT_CASE", ticket_ids: ["t1", "t2"], members: [] });

  const rendered = draftCardRows(caseItem, code);

  assert.equal(rendered.length, 2);
  // A readable code rather than a blank row, and no invented location.
  assert.deepEqual(rendered.map((row) => row.code), [code("t1"), code("t2")]);
  assert.equal(rendered[0].location, "Chưa xác định");
});

// ---------------------------------------------------------------------------
// The grace-window cancel button (§6.2)
// ---------------------------------------------------------------------------

test("cancel is offered for a DIRECT rejection waiting out its grace window", () => {
  assert.equal(canCancelJob(job()), true);
});

test("cancel is not offered outside that window", () => {
  const cases = [
    job({ trigger: "INITIAL_AUTO", cancellable: false }),
    job({ trigger: "REASSIGN_SILENT", cancellable: false }),
    job({ status: "PRIMARY_RUNNING", cancellable: false }),
    job({ status: "FALLBACK_RUNNING", cancellable: false }),
    job({ status: "COMPLETED", cancellable: false }),
    job({ mode: "PROPOSAL", trigger: "COORDINATOR_PROPOSAL", cancellable: false }),
  ];
  for (const candidate of cases) assert.equal(canCancelJob(candidate), false, `${candidate.mode}/${candidate.trigger}/${candidate.status}`);
});

test("the rule is recomputed when the backend sends no flag", () => {
  const { cancellable: _flag, ...withoutFlag } = job();
  assert.equal(canCancelJob(withoutFlag as never), true);
  const initial = { ...withoutFlag, trigger: "INITIAL_AUTO" };
  assert.equal(canCancelJob(initial as never), false);
});

test("only unfinished DIRECT jobs reach the automatic queue", () => {
  const rows = activeJobs([
    job(),
    job({ id: "j2", status: "PRIMARY_RUNNING" }),
    job({ id: "j3", status: "COMPLETED" }),
    job({ id: "j4", mode: "PROPOSAL", status: "SCHEDULED_GRACE" }),
  ]);

  assert.deepEqual(rows.map((row) => row.id), ["job-1", "j2"]);
});

// ---------------------------------------------------------------------------
// The "Cần phân tay" queue (§4.7, §4.8, §14.3)
// ---------------------------------------------------------------------------

test("with auto off every approved unassigned ticket needs a human", () => {
  const rows = manualAssignmentQueue([ticket("t1"), ticket("t2")], [], false);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].reason, "Phân việc tự động đang tắt.");
  assert.equal(rows[0].urgent, false);
});

test("with auto on a ticket merely waiting for its delay is not in the queue", () => {
  assert.deepEqual(manualAssignmentQueue([ticket("t1")], [], true), []);
});

test("a paused ticket needs a human even while auto is on", () => {
  const rows = manualAssignmentQueue(
    [ticket("t1", { auto_assignment_paused: true, auto_assignment_pause_reason: "Không còn ứng viên." })],
    [],
    true,
  );

  assert.equal(rows.length, 1);
  assert.equal(rows[0].reason, "Không còn ứng viên.");
  assert.equal(rows[0].urgent, true);
});

test("a ticket past the reassignment cap is flagged as an instruction", () => {
  const rows = manualAssignmentQueue([ticket("t1", { reassignment_count: 4 })], [], true);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].urgent, true);
  assert.match(rows[0].reason, /vượt trần 3/);
});

test("a manual-required job explains itself without leaking internals", () => {
  const rows = manualAssignmentQueue(
    [ticket("t1")],
    [job({ status: "MANUAL_REQUIRED", error_code: "NO_CANDIDATES", ticket_ids: ["t1"] })],
    true,
  );

  assert.equal(rows.length, 1);
  assert.equal(rows[0].reason, "Không còn kỹ thuật viên phù hợp — cần phân tay.");
});

test("an unrecognised failure still produces an actionable line", () => {
  assert.equal(jobFailureReason(job({ status: "FAILED", error_code: "SOMETHING_NEW" })), "Lượt phân việc tự động không hoàn tất — cần phân tay.");
});

test("tickets under manual review never mix into the assignment queue", () => {
  assert.deepEqual(manualAssignmentQueue([ticket("t1", { classification_status: "MANUAL_REVIEW", status: "NEW" })], [], false), []);
});

test("a ticket the AI is currently holding stays out of the manual queue", () => {
  const rows = manualAssignmentQueue(
    [ticket("t1")],
    [
      // A stale failure from an earlier round, plus the round running now.
      job({ id: "old", status: "MANUAL_REQUIRED", error_code: "MODEL_FAILED", ticket_ids: ["t1"] }),
      job({ id: "now", status: "SCHEDULED_GRACE", ticket_ids: ["t1"] }),
    ],
    true,
  );

  assert.deepEqual(rows, []);
});

test("an already assigned ticket is not in the queue", () => {
  assert.deepEqual(manualAssignmentQueue([ticket("t1", { active_assignment_id: "a1" })], [], false), []);
});

// ---------------------------------------------------------------------------
// Confirming a batch (§4.2, §4.6)
// ---------------------------------------------------------------------------

test("a board with nothing placed cannot be confirmed", () => {
  // Not "confirm and turn auto on" — this screen never touches the V4 switch.
  // Confirming an empty board would just be rejected by the backend, so the
  // button is not offered and the hint says what to do instead.
  assert.equal(canConfirmBatch(batch([]), false), false);
  assert.equal(canConfirmBatch(batch([item({ status: "DESELECTED" })]), false), false);
  assert.equal(canConfirmBatch(batch([item({ status: "EMPTY", final_technician_id: null })]), false), false);
});

test("a board with one placed row confirms, leftovers and all", () => {
  // Partial confirmation is valid: the deselected row stays unassigned.
  assert.equal(canConfirmBatch(batch([item(), item({ id: "b", status: "DESELECTED" })]), false), true);
});

test("an expired batch cannot be confirmed however full the board is", () => {
  assert.equal(canConfirmBatch(batch([item()]), true), false);
});

test("the confirmation summary counts tickets, overrides and what is left behind", () => {
  const summary = confirmSummary(
    batch([
      item({ id: "a", work_item_type: "INCIDENT_CASE", ticket_ids: ["t1", "t2"] }),
      item({ id: "b", final_technician_id: "tech-b" }),
      item({ id: "c", status: "EMPTY", final_technician_id: null }),
      item({ id: "d", status: "DESELECTED" }),
    ]),
  );

  assert.equal(summary.itemCount, 2);
  assert.equal(summary.ticketCount, 3);
  assert.equal(summary.overrideCount, 1);
  assert.equal(summary.unassignedCount, 2);
  assert.equal(summary.consequence, "2 ticket sẽ tiếp tục ở trạng thái chưa phân công.");
});

test("a fully placed board states no consequence", () => {
  assert.equal(confirmSummary(batch([item()])).consequence, "");
  assert.equal(unassignedConsequence(0), "");
});

test("the draft header counts ticket members, not rows", () => {
  const summary = draftSummary(batch([
    item({ id: "case", work_item_type: "INCIDENT_CASE", ticket_ids: ["t1", "t2", "t3"] }),
    item({ id: "single", final_technician_id: "tech-b" }),
    item({ id: "left", status: "EMPTY", final_technician_id: null, ticket_ids: ["t9"] }),
  ]));

  assert.deepEqual(summary, { placed: 4, unplaced: 1, total: 5 });
});

test("the result headline counts distinct technicians, not rows", () => {
  const result = assignedResult(batch([
    item({ id: "a", status: "ASSIGNED", final_technician_id: "tech-a" }),
    item({ id: "b", status: "ASSIGNED", final_technician_id: "tech-a" }),
    item({ id: "c", status: "ASSIGNED", final_technician_id: "tech-b", work_item_type: "INCIDENT_CASE", ticket_ids: ["t1", "t2"] }),
    item({ id: "d", status: "EMPTY", final_technician_id: null }),
  ]));

  assert.equal(result.ticketCount, 4);
  assert.equal(result.technicianCount, 2);
  assert.equal(result.unassignedCount, 1);
});

// ---------------------------------------------------------------------------
// Errors and formatting (§9)
// ---------------------------------------------------------------------------

test("backend error codes become instructions", () => {
  // Each one names a control that is actually on the screen.
  assert.match(assignmentErrorMessage({ code: "PROPOSAL_EXPIRED" }), /Hủy đề xuất/);
  assert.match(assignmentErrorMessage({ code: "AUTO_ASSIGNMENT_PROPOSAL_REQUIRED" }), /đề xuất đã xác nhận/);
  assert.match(assignmentErrorMessage({ code: "TECHNICIAN_NOT_ELIGIBLE" }), /không còn hoạt động/);
  assert.match(assignmentErrorMessage({ code: "CONFLICT_VERSION" }), /tải lại/);
  assert.equal(assignmentErrorMessage({ code: "PROPOSAL_NOTHING_TO_ASSIGN" }), NOTHING_PLACED_HINT);
});

test("an unknown error falls back without inventing detail", () => {
  assert.equal(assignmentErrorMessage({ code: "WHO_KNOWS" }, "Không thực hiện được."), "Không thực hiện được.");
  assert.equal(assignmentErrorMessage(new Error("Backend trả về HTTP 500.")), "Backend trả về HTTP 500.");
});

test("the delay spelling from the backend maps onto the request form", () => {
  assert.equal(delayFromBackend("2_HOURS"), "2H");
  assert.equal(delayFromBackend("3_DAYS"), "3D");
  assert.equal(delayFromBackend(null), "IMMEDIATE");
});

test("the countdown floors at zero", () => {
  assert.equal(formatCountdown(65_000), "01:05");
  assert.equal(formatCountdown(-4_000), "00:00");
});

// ---------------------------------------------------------------------------
// State 1 — auto-approve eligibility
// ---------------------------------------------------------------------------

test("only fully classified reports are auto-approvable", () => {
  const eligible = ticket("t1", { available_actions: ["APPROVE", "OVERRIDE_CLASSIFICATION"] });
  const unclassified = ticket("t2", { classification_status: "MANUAL_REVIEW", status: "NEW", available_actions: ["RESOLVE_MANUAL_REVIEW", "REJECT_MANUAL_REVIEW"] });
  const noCategory = ticket("t3", { category: null, category_id: null, available_actions: ["OVERRIDE_CLASSIFICATION"] });

  assert.equal(isAutoApprovable(eligible), true);
  assert.equal(isAutoApprovable(unclassified), false);
  assert.equal(isAutoApprovable(noCategory), false);
  assert.deepEqual(autoApprovableTickets([eligible, unclassified, noCategory]).map((row) => row.id), ["t1"]);
});

test("an already approved report is not auto-approved again", () => {
  // APPROVED reports no longer offer APPROVE, so they cannot be swept up twice.
  assert.equal(isAutoApprovable(ticket("t1", { status: "APPROVED", available_actions: ["ASSIGN"] })), false);
});

// ---------------------------------------------------------------------------
// State 2 — the two preparation queues
// ---------------------------------------------------------------------------

test("the queues split on approval, not on classification", () => {
  const rows = [
    ticket("new-1", { status: "NEW" }),
    ticket("review-1", { status: "NEW", classification_status: "MANUAL_REVIEW" }),
    ticket("ready-1", { status: "APPROVED" }),
    ticket("assigned-1", { status: "APPROVED", active_assignment_id: "a1" }),
    ticket("done-1", { status: "COMPLETED" }),
  ];

  assert.deepEqual(awaitingApprovalQueue(rows).map((row) => row.id).sort(), ["new-1", "review-1"]);
  // Already assigned and already finished reports are in neither queue.
  assert.deepEqual(awaitingAssignmentQueue(rows).map((row) => row.id), ["ready-1"]);
});

test("both queues sort by priority then oldest first", () => {
  const rows = [
    ticket("p1-old", { priority: "P1", created_at: "2026-08-20T00:00:00Z" }),
    ticket("p3-new", { priority: "P3", created_at: "2026-08-23T00:00:00Z" }),
    ticket("p2-mid", { priority: "P2", created_at: "2026-08-21T00:00:00Z" }),
    ticket("p3-old", { priority: "P3", created_at: "2026-08-19T00:00:00Z" }),
  ];

  assert.deepEqual(awaitingAssignmentQueue(rows).map((row) => row.id), ["p3-old", "p3-new", "p2-mid", "p1-old"]);
});

test("the assignment queue keeps a paused ticket but drops one at the reassignment cap", () => {
  // Regression: a PROPOSAL batch is the recovery path for AUTO_ASSIGNMENT_DISABLED
  // and NO_CANDIDATES pauses (backend AssignmentCandidateService.eligible_ticket_query
  // with include_paused=True), so this queue -- documented as the proposal's only
  // source -- must keep showing those tickets rather than a count the batch can
  // never match. The reassignment cap is the one pause that still blocks a
  // PROPOSAL, so it stays excluded here and shows up in the manual queue instead.
  const rows = [
    ticket("disabled-1", { auto_assignment_paused: true, auto_assignment_pause_reason: "AUTO_ASSIGNMENT_DISABLED" }),
    ticket("no-candidates-1", { auto_assignment_paused: true, auto_assignment_pause_reason: "NO_CANDIDATES" }),
    ticket("capped-1", { reassignment_count: 4 }),
    ticket("plain-1", {}),
  ];

  assert.deepEqual(
    awaitingAssignmentQueue(rows).map((row) => row.id).sort(),
    ["disabled-1", "no-candidates-1", "plain-1"],
  );
});

// ---------------------------------------------------------------------------
// Case-aware source queues
// ---------------------------------------------------------------------------

test("a materialized case with every member new is one approval-queue entry, not two ticket rows", () => {
  const tickets = [ticket("m1", { status: "NEW" }), ticket("m2", { status: "NEW" })];
  const clusters = [cluster("case-1", ["m1", "m2"])];

  const { approval, ready } = assignmentSourceQueues(tickets, clusters);

  assert.deepEqual(approval, [{ kind: "case", caseRow: clusters[0], tickets, ticketCount: 2 }]);
  assert.deepEqual(ready, []);
});

test("a case with one member still new stays in the approval queue and never reaches ready", () => {
  const ready1 = ticket("m1", { status: "APPROVED" });
  const stillNew = ticket("m2", { status: "NEW" });
  const clusters = [cluster("case-1", ["m1", "m2"])];

  const { approval, ready } = assignmentSourceQueues([ready1, stillNew], clusters);

  assert.equal(approval.length, 1);
  assert.equal(approval[0].kind, "case");
  assert.equal(ready.length, 0);
});

test("a case reaches the ready queue only once every member is independently ready", () => {
  const first = ticket("m1", { status: "APPROVED" });
  const second = ticket("m2", { status: "APPROVED" });
  const clusters = [cluster("case-1", ["m1", "m2"])];

  const { approval, ready } = assignmentSourceQueues([first, second], clusters);

  assert.deepEqual(approval, []);
  assert.deepEqual(ready, [{ kind: "case", caseRow: clusters[0], tickets: [first, second], ticketCount: 2 }]);
});

test("one member missing a category keeps the whole case out of the ready queue", () => {
  const first = ticket("m1", { status: "APPROVED" });
  const second = ticket("m2", { status: "APPROVED", category_id: null, category: null });
  const clusters = [cluster("case-1", ["m1", "m2"])];

  const { ready } = assignmentSourceQueues([first, second], clusters);

  assert.deepEqual(ready, []);
});

test("a DERIVED cluster is only a suggestion — its members stay ordinary standalone rows", () => {
  // Regression: a derived cluster has no durable IncidentCase id, so the
  // backend's case_draft has never heard of it. Grouping it into a
  // "Case · N ticket" entry here would let the UI offer to approve/assign a
  // work item the assignment service cannot lock, splitting UI and backend.
  const first = ticket("m1", { status: "APPROVED" });
  const second = ticket("m2", { status: "APPROVED" });
  const clusters = [cluster("derived-1", ["m1", "m2"], { status: "DERIVED" })];

  const { ready } = assignmentSourceQueues([first, second], clusters);

  assert.deepEqual(ready.map((entry) => entry.kind), ["ticket", "ticket"]);
  assert.deepEqual(ready.map((entry) => (entry.kind === "ticket" ? entry.ticket.id : null)).sort(), ["m1", "m2"]);
});

test("queueTicketCount sums case members and standalone tickets alike", () => {
  const tickets = [ticket("m1", { status: "APPROVED" }), ticket("m2", { status: "APPROVED" }), ticket("solo", { status: "APPROVED" })];
  const clusters = [cluster("case-1", ["m1", "m2"])];

  const { ready } = assignmentSourceQueues(tickets, clusters);

  assert.equal(queueTicketCount(ready), 3);
});

test("approvalTicketsFromEntries flattens a case entry back to its member tickets", () => {
  const tickets = [ticket("m1", { status: "NEW" }), ticket("m2", { status: "NEW" }), ticket("solo", { status: "NEW" })];
  const clusters = [cluster("case-1", ["m1", "m2"])];

  const { approval } = assignmentSourceQueues(tickets, clusters);

  assert.deepEqual(approvalTicketsFromEntries(approval).map((row) => row.id).sort(), ["m1", "m2", "solo"]);
});

test("the approval queue explains why each row is stuck", () => {
  assert.equal(awaitingApprovalReason(ticket("t1", { classification_status: "MANUAL_REVIEW" })), "Chờ duyệt phân loại thủ công");
  assert.equal(awaitingApprovalReason(ticket("t2", { available_actions: ["APPROVE"] })), "Đủ điều kiện tự động duyệt");
  assert.equal(awaitingApprovalReason(ticket("t3", { category: null, available_actions: [] })), "Chưa có danh mục");
  assert.equal(awaitingApprovalReason(ticket("t4", { priority: null, available_actions: [] })), "Chưa có mức ưu tiên");
});

// ---------------------------------------------------------------------------
// State 3 — reference names
// ---------------------------------------------------------------------------

test("reference names run A, B, C and keep going past Z", () => {
  assert.equal(referenceName(0), "Ticket A");
  assert.equal(referenceName(2), "Ticket C");
  assert.equal(referenceName(25), "Ticket Z");
  assert.equal(referenceName(26), "Ticket AA");
  assert.equal(referenceName(27), "Ticket AB");
});

test("a reference name follows the row, not the column it sits in", () => {
  const rows = [item({ id: "a" }), item({ id: "b" }), item({ id: "c" })];
  const before = referenceNames(batch(rows));

  // The same batch after the coordinator moved the middle row to someone else.
  const after = referenceNames(batch([rows[0], { ...rows[1], final_technician_id: "tech-b" }, rows[2]]));

  assert.equal(before.get("b"), "Ticket B");
  assert.equal(after.get("b"), "Ticket B");
  assert.deepEqual([...after.values()], ["Ticket A", "Ticket B", "Ticket C"]);
});

// ---------------------------------------------------------------------------
// State 3 — the draft board
// ---------------------------------------------------------------------------

test("the board splits placed rows from unplaced ones", () => {
  const roster = [technician("tech-a"), technician("tech-b")];
  const board = draftBoard(
    batch([
      item({ id: "placed", final_technician_id: "tech-a" }),
      item({ id: "dropped", status: "DESELECTED" }),
      item({ id: "empty", status: "EMPTY", proposed_technician_id: null, final_technician_id: null }),
    ]),
    roster,
  );

  assert.deepEqual(board.unassigned.map((row) => row.id), ["dropped", "empty"]);
  assert.deepEqual(board.technicians.map((column) => [column.id, column.items.length]), [["tech-a", 1], ["tech-b", 0]]);
});

test("an empty technician still gets a column to drop onto", () => {
  const board = draftBoard(batch([]), [technician("tech-a")]);
  assert.equal(board.technicians.length, 1);
  assert.deepEqual(board.technicians[0].items, []);
});

test("an inactive technician gets no column and holds no rows", () => {
  const board = draftBoard(
    batch([item({ id: "x", final_technician_id: "tech-gone" })]),
    [technician("tech-a"), technician("tech-gone", { is_active: false })],
  );

  assert.deepEqual(board.technicians.map((column) => column.id), ["tech-a"]);
  // The row falls back to unassigned rather than resurrecting a retired name.
  assert.deepEqual(board.unassigned.map((row) => row.id), ["x"]);
});

test("a deselected row still counts as unplaced even with a technician on it", () => {
  const board = draftBoard(batch([item({ id: "x", status: "DESELECTED", final_technician_id: "tech-a" })]), [technician("tech-a")]);
  assert.deepEqual(board.unassigned.map((row) => row.id), ["x"]);
  assert.equal(board.technicians[0].items.length, 0);
});

test("dropping a row onto a technician assigns it", () => {
  assert.deepEqual(dropChange(item({ status: "EMPTY", final_technician_id: null }), "tech-b"), { technician_id: "tech-b" });
  assert.deepEqual(dropChange(item({ final_technician_id: "tech-a" }), "tech-b"), { technician_id: "tech-b" });
});

test("dropping a row back into unassigned deselects it", () => {
  assert.deepEqual(dropChange(item({ final_technician_id: "tech-a" }), UNASSIGNED_COLUMN), { selected: false });
});

test("a drop that changes nothing sends no request", () => {
  // Back onto the technician it already sits on.
  assert.equal(dropChange(item({ final_technician_id: "tech-a" }), "tech-a"), null);
  // Back into the column it came from.
  assert.equal(dropChange(item({ status: "DESELECTED" }), UNASSIGNED_COLUMN), null);
  assert.equal(dropChange(item({ status: "EMPTY", final_technician_id: null }), UNASSIGNED_COLUMN), null);
});

test("the placed count follows the rows, cases included", () => {
  const board = batch([
    item({ id: "single", final_technician_id: "tech-a" }),
    item({ id: "case", work_item_type: "INCIDENT_CASE", ticket_ids: ["t1", "t2", "t3"], final_technician_id: "tech-a" }),
    item({ id: "dropped", status: "DESELECTED" }),
  ]);

  assert.equal(placedTicketCount(board), 4);
});

// ---------------------------------------------------------------------------
// State 2 — while the model is answering
// ---------------------------------------------------------------------------

test("exactly one building step is active at a time", () => {
  for (const elapsed of [0, 1200, 3000, 7000, 60_000]) {
    const active = buildingSteps(elapsed).filter((step) => step.state === "active");
    assert.equal(active.length, 1, `elapsed ${elapsed}`);
  }
});

test("the building steps advance and never complete on their own", () => {
  assert.deepEqual(buildingSteps(0).map((step) => step.state), ["active", "waiting", "waiting", "waiting"]);
  assert.deepEqual(buildingSteps(BUILDING_STEP_MS * 2 + 10).map((step) => step.state), ["done", "done", "active", "waiting"]);
  // A batch the worker never answered would otherwise show a finished
  // checklist while nothing had actually finished.
  assert.deepEqual(buildingSteps(BUILDING_STEP_MS * 50).map((step) => step.state), ["done", "done", "done", "active"]);
});

// ---------------------------------------------------------------------------
// The recurring proposal schedule
// ---------------------------------------------------------------------------

test("the schedule offers no-repeat plus three intervals, in backend spelling", () => {
  assert.deepEqual(SCHEDULE_OPTIONS.map((option) => option.value), ["NONE", "2_HOURS", "1_DAY", "3_DAYS"]);
});

test("every repeat option says Lặp lại, so it cannot read as a one-time delay", () => {
  for (const option of SCHEDULE_OPTIONS.filter((row) => row.value !== "NONE")) {
    assert.ok(option.label.startsWith("Lặp lại mỗi "), option.label);
  }
  assert.equal(scheduleLabel("2_HOURS"), "Lặp lại mỗi 2 giờ");
  assert.equal(scheduleLabel("NONE"), "Không tự động");
  assert.equal(scheduleLabel(null), "Không tự động");
});

test("the schedule banner promises a draft to review, never an assignment", () => {
  const on = scheduleSummary({ enabled: true, interval: "1_DAY", next_run_at: null, last_run_at: null, version: 2, updated_at: "" });
  assert.ok(on.includes("Lặp lại mỗi 1 ngày"));
  assert.ok(on.includes("chờ Ban quản lý duyệt"));
  assert.ok(scheduleSummary(null).includes("Không tự động"));
});

test("an enabled schedule reads back as its own interval", () => {
  assert.equal(scheduleChoiceOf({ enabled: true, interval: "3_DAYS", next_run_at: null, last_run_at: null, version: 1, updated_at: "" }), "3_DAYS");
  assert.equal(scheduleChoiceOf({ enabled: false, interval: null, next_run_at: null, last_run_at: null, version: 1, updated_at: "" }), "NONE");
  assert.equal(scheduleChoiceOf(null), "NONE");
});

// ---------------------------------------------------------------------------
// Assignment history, from the frozen snapshot
// ---------------------------------------------------------------------------

test("history is newest first", () => {
  const rows = historyRows([
    record({ batch_id: "older", confirmed_at: "2026-08-22T00:00:00Z" }),
    record({ batch_id: "newer", confirmed_at: "2026-08-24T00:00:00Z" }),
  ]);

  assert.deepEqual(rows.map((row) => row.record.batch_id), ["newer", "older"]);
});

test("a record names the confirming coordinator and how the round started", () => {
  const scheduled = record({ created_by_type: "SYSTEM", confirmed_by_name: "Điều phối viên Lan" });

  assert.equal(historyOrigin(scheduled), "Lịch tự động tạo đề xuất");
  // Opened by the scheduler, confirmed by a person: two separate facts.
  assert.equal(historyConfirmedBy(scheduled), "Điều phối viên Lan");
  assert.equal(historyOrigin(record({})), "BQL tạo đề xuất");
  assert.equal(historyConfirmedBy(record({ confirmed_by_name: null })), "Ban quản lý");
});

test("the repeat that followed is reported, including a recorded decline", () => {
  assert.equal(historyRows([record({ followup_schedule: "2_HOURS" })])[0].followup, "Lặp lại mỗi 2 giờ");
  // Asked and declined is a different fact from never asked.
  assert.equal(historyRows([record({ followup_schedule: "NONE" })])[0].followup, "Không tự động");
  assert.equal(historyRows([record({ followup_schedule: null })])[0].followup, "Không ghi nhận");
});

// ---------------------------------------------------------------------------
// DIRECT auto-assignment: one way out, and no way in from here
// ---------------------------------------------------------------------------

const settings = (overrides: Partial<AutoAssignmentSettingsType> = {}): AutoAssignmentSettingsType => ({
  enabled: false,
  activation_delay: "IMMEDIATE",
  version: 1,
  updated_at: "2026-08-24T00:00:00Z",
  activated_by_batch_id: null,
  activated_by_user_id: null,
  activated_at: null,
  ...overrides,
});

test("while DIRECT is off the UI offers guidance, not an action", () => {
  for (const off of [null, settings(), settings({ enabled: false, activation_delay: "2_HOURS" })]) {
    const control = directControl(off);
    assert.equal(control.kind, "guidance");
    // The union has no `enable` shape, so a component cannot render one: this
    // asserts the narrowing a reader would otherwise have to take on faith.
    assert.equal("label" in control, false);
    assert.equal(control.kind === "guidance" && control.message, DIRECT_OFF_GUIDANCE);
  }
});

test("the off-state guidance names the path that actually starts DIRECT", () => {
  assert.match(DIRECT_OFF_GUIDANCE, /đề xuất phân việc/);
  assert.match(DIRECT_OFF_GUIDANCE, /đang tắt/);
  // Never an instruction to press something that would turn it on.
  assert.doesNotMatch(DIRECT_OFF_GUIDANCE, /Bật phân việc tự động/);
});

test("while DIRECT is on the only control offered is the one that stops it", () => {
  const control = directControl(settings({ enabled: true, activation_delay: "2_HOURS" }));

  assert.equal(control.kind, "disable");
  assert.equal(control.kind === "disable" && control.label, DIRECT_DISABLE_LABEL);
  assert.equal(DIRECT_DISABLE_LABEL, "Tắt phân việc tự động");
  assert.equal(control.kind === "disable" && control.delay, "Sau 2 giờ");
});

test("the stop notice explains the consequence and spares the repeat schedule", () => {
  assert.match(DIRECT_DISABLE_NOTICE, /không tự phân công/);
  // The two features are easy to confuse; stopping one must not read as
  // stopping both.
  assert.match(DIRECT_DISABLE_NOTICE, /lịch lặp lại/i);
  assert.match(DIRECT_DISABLE_NOTICE, /đã phân công/);
});

test("the status line says which state DIRECT is in", () => {
  assert.equal(directStatusLabel(settings({ enabled: true, activation_delay: "1_DAY" })), "Phân việc tự động đang bật · Sau 1 ngày");
  assert.equal(directStatusLabel(settings()), "Phân việc tự động đang tắt");
  assert.equal(directStatusLabel(null), "Phân việc tự động đang tắt");
});

test("activation is read from the confirmed batch, not guessed", () => {
  // The backend decided which confirmation earned the switch; re-deriving it
  // here could disagree with the audit trail.
  assert.equal(directActivatedByConfirmation(batch([item()], { continue_auto_assignment: true })), true);
  assert.equal(directActivatedByConfirmation(batch([item()], { continue_auto_assignment: false })), false);
  // Null means "not confirmed yet", which is not an activation either.
  assert.equal(directActivatedByConfirmation(batch([item()], { continue_auto_assignment: null })), false);
});

test("the activation message tells the coordinator it can be undone", () => {
  assert.match(DIRECT_ACTIVATED_MESSAGE, /Đã bật phân việc tự động/);
  assert.match(DIRECT_ACTIVATED_MESSAGE, /tắt lại bất cứ lúc nào/);
});

test("no module in the assignment UI can form a request that enables DIRECT", () => {
  // A source-level check on purpose. The backend refuses the transition, but a
  // client function able to *build* the request is a loaded gun sitting next to
  // the rule — and this is the only way to assert its absence without importing
  // the whole runtime client into a type-stripped test.
  const root = new URL("..", import.meta.url);
  for (const file of ["api/backend.api.ts", "components/manager/AssignmentWorkspace.tsx", "app/manager/page.tsx"]) {
    const source = readFileSync(new URL(file, root), "utf8");
    assert.doesNotMatch(source, /enabled:\s*true/, file);
    assert.doesNotMatch(source, /updateAutoAssignmentSettings/, file);
    assert.doesNotMatch(source, /Bật phân việc tự động/, file);
    assert.doesNotMatch(source, /continue_auto_assignment:/, file);
  }
});
