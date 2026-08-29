/** Rules the Visual Assignment board renders — run with `npm test`.
 *
 *  These cover what the UI must not get wrong and a screenshot cannot show: an
 *  invalid drop is refused rather than bounced back, a group is one unit with no
 *  interaction that could split it, a rejected confirm leaves the arrangement
 *  alone and names the failures, and the Automatic Assignment modal carries §2's
 *  wording rather than a paraphrase of it.
 *
 *  `frontend/lib/visualAssignment.ts` has no runtime imports, so Node runs it
 *  directly with type stripping; no bundler or test framework is involved.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  AUTO_ASSIGNMENT_CONFIRMATION,
  AUTO_ASSIGNMENT_OFF_NOTICE,
  BLOCKING_WARNINGS,
  POOL,
  advisoryWarnings,
  blockingWarnings,
  canConfirm,
  canPlace,
  columnLoad,
  confirmFailureMessage,
  confirmSummary,
  decisionSourceLabel,
  emptyDraft,
  failedUnitIds,
  hoursLabel,
  isBlocking,
  orderedTechnicians,
  placedTicketCount,
  placementsOf,
  reconcileDraft,
  riskyPlacements,
  slackLabel,
  unitsInColumn,
  warningLabel,
} from "../lib/visualAssignment.ts";
import type { BoardTechnician, BoardUnit, VisualBoard } from "../types/api.ts";

const TECH_FREE = "tech-free";
const TECH_BUSY = "tech-busy";
const TECH_UNSKILLED = "tech-unskilled";

function unit(id: string, overrides: Partial<BoardUnit> = {}): BoardUnit {
  return {
    unit_id: id,
    unit_type: "TICKET",
    ticket_ids: [`${id}-t1`],
    display_codes: [`PA-${id.toUpperCase()}`],
    category_id: "cat-water",
    category_code: "WATER",
    category_display_name: "Rò rỉ nước",
    priority: "P2",
    score: 40,
    submitted_at: "2026-08-26T01:00:00Z",
    location_labels: ["Hành lang tầng 10"],
    p80_seconds: 5 * 3600,
    member_count: 1,
    eligible_technician_ids: [TECH_FREE, TECH_BUSY],
    previews: [
      { technician_id: TECH_FREE, blocked: false, warnings: [], planned_start_at: "2026-08-26T01:00:00Z", planned_finish_at: "2026-08-26T06:30:00Z", worst_slack_seconds: null },
      { technician_id: TECH_BUSY, blocked: false, warnings: ["SCHEDULE_RISK", "OVERLOADED"], planned_start_at: "2026-08-26T06:00:00Z", planned_finish_at: "2026-08-27T02:00:00Z", worst_slack_seconds: -7200 },
      { technician_id: TECH_UNSKILLED, blocked: true, warnings: ["MISSING_SKILL"], planned_start_at: null, planned_finish_at: null, worst_slack_seconds: null },
    ],
    ...overrides,
  };
}

function technician(id: string, overrides: Partial<BoardTechnician> = {}): BoardTechnician {
  return {
    technician_id: id,
    display_name: id,
    is_active: true,
    is_available: true,
    skill_category_ids: ["cat-water"],
    active_assignment_count: 0,
    in_progress_count: 0,
    planned_slots: [],
    day_ends_at: null,
    ...overrides,
  };
}

function board(overrides: Partial<VisualBoard> = {}): VisualBoard {
  return {
    generated_at: "2026-08-26T01:00:00Z",
    within_working_shift: true,
    units: [unit("a")],
    technicians: [technician(TECH_FREE), technician(TECH_BUSY, { active_assignment_count: 3 }), technician(TECH_UNSKILLED, { skill_category_ids: [] })],
    ...overrides,
  };
}

// ------------------------------------------------------------------ placement

test("every unit starts in the pool", () => {
  const draft = emptyDraft(board());
  assert.equal(draft["a"], POOL);
  assert.deepEqual(unitsInColumn(board(), draft, POOL).map((item) => item.unit_id), ["a"]);
});

test("a blocked pairing refuses the drop rather than bouncing it back", () => {
  // §3 is hard-enforced on confirm, so the board must not let a manager
  // arrange something the backend will reject at the end.
  assert.equal(canPlace(unit("a"), TECH_UNSKILLED), false);
  assert.equal(canPlace(unit("a"), TECH_FREE), true);
});

test("the drop verdict comes from the server, never from re-deriving skills", () => {
  // A technician the previews did not cover falls back to the eligible list,
  // which is the same answer from the other direction.
  const sparse = unit("a", { previews: [], eligible_technician_ids: [TECH_FREE] });
  assert.equal(canPlace(sparse, TECH_FREE), true);
  assert.equal(canPlace(sparse, TECH_BUSY), false);
});

test("the pool always accepts a unit back", () => {
  assert.equal(canPlace(unit("a"), POOL), true);
});

test("blocking and advisory warnings are kept apart", () => {
  // The first three refuse a drop; the last two colour a card the manager may
  // still confirm. §3 lists the first three and not the other two.
  assert.deepEqual([...BLOCKING_WARNINGS], ["MISSING_SKILL", "TECHNICIAN_UNAVAILABLE", "OUT_OF_SHIFT"]);
  assert.equal(isBlocking("MISSING_SKILL"), true);
  assert.equal(isBlocking("OVERLOADED"), false);
  assert.deepEqual(advisoryWarnings(unit("a"), TECH_BUSY), ["SCHEDULE_RISK", "OVERLOADED"]);
  assert.deepEqual(blockingWarnings(unit("a"), TECH_UNSKILLED), ["MISSING_SKILL"]);
});

test("every warning code has Vietnamese copy", () => {
  for (const code of ["MISSING_SKILL", "TECHNICIAN_UNAVAILABLE", "OUT_OF_SHIFT", "OVERLOADED", "SCHEDULE_RISK"]) {
    assert.notEqual(warningLabel(code), code, `${code} has no label`);
  }
});

// --------------------------------------------------------------------- groups

test("a group is one unit carrying all its members", () => {
  const grouped = unit("case-1", {
    unit_type: "GROUP",
    ticket_ids: ["t1", "t2", "t3"],
    display_codes: ["PA-1", "PA-2", "PA-3"],
    member_count: 3,
    p80_seconds: 15 * 3600,
  });
  const state = board({ units: [grouped] });
  const draft = { "case-1": TECH_FREE };

  // One placement, three reports. Nothing in the draft can address a member.
  assert.equal(placementsOf(draft).length, 1);
  assert.equal(placedTicketCount(state, draft), 3);
  assert.equal(hoursLabel(grouped.p80_seconds), "15 giờ");
});

// -------------------------------------------------------------------- confirm

test("an empty board cannot be confirmed", () => {
  assert.equal(canConfirm(board(), emptyDraft(board())), false);
});

test("nothing can be confirmed outside the working shift", () => {
  const closed = board({ within_working_shift: false });
  assert.equal(canConfirm(closed, { a: TECH_FREE }), false);
});

test("only real placements are sent", () => {
  assert.deepEqual(placementsOf({ a: TECH_FREE, b: POOL }), [{ unit_id: "a", technician_id: TECH_FREE }]);
});

test("the summary counts reports, not drags", () => {
  const grouped = unit("case-1", { unit_type: "GROUP", ticket_ids: ["t1", "t2"], member_count: 2 });
  const state = board({ units: [unit("a"), grouped] });
  const summary = confirmSummary(state, { a: TECH_FREE, "case-1": TECH_BUSY });

  assert.match(summary, /2 nhóm việc \(3 phản ánh\)/);
  assert.match(summary, /2 kỹ thuật viên/);
});

test("an unplaced board says so rather than showing a zero", () => {
  assert.match(confirmSummary(board(), emptyDraft(board())), /Chưa có công việc nào/);
});

test("risky-but-allowed placements are counted for the confirm bar", () => {
  assert.equal(riskyPlacements(board(), { a: TECH_BUSY }), 1);
  assert.equal(riskyPlacements(board(), { a: TECH_FREE }), 0);
});

test("a rejected confirm names the reasons and says nothing was saved", () => {
  const message = confirmFailureMessage([
    { unit_id: "a", technician_id: TECH_UNSKILLED, codes: ["MISSING_SKILL"] },
    { unit_id: "b", technician_id: TECH_BUSY, codes: ["ACTIVE_ASSIGNMENT_EXISTS"] },
  ]);
  assert.match(message, /Không có kỹ năng phù hợp/);
  assert.match(message, /đã có kỹ thuật viên khác/i);
  assert.match(message, /Không có thay đổi nào được lưu/);
});

test("a rejected confirm marks the cards it named", () => {
  assert.deepEqual(
    failedUnitIds([
      { unit_id: "a", technician_id: TECH_FREE, codes: ["MISSING_SKILL"] },
      { unit_id: "a", technician_id: TECH_FREE, codes: ["OUT_OF_SHIFT"] },
    ]),
    ["a"],
  );
});

// -------------------------------------------------------------------- refresh

test("a refresh keeps placements for units still on the board", () => {
  const next = board({ units: [unit("a")] });
  assert.deepEqual(reconcileDraft({ a: TECH_FREE, gone: TECH_BUSY }, next), { a: TECH_FREE });
});

test("a unit that vanished loses its placement", () => {
  // It was taken by someone else or stopped being eligible; confirming it would
  // be confirming something the manager can no longer see.
  assert.deepEqual(reconcileDraft({ gone: TECH_FREE }, board({ units: [] })), {});
});

// -------------------------------------------------------------------- columns

test("columns are ordered availability first, then least loaded", () => {
  const state = board({
    technicians: [
      technician("busy", { active_assignment_count: 4 }),
      technician("away", { is_available: false }),
      technician("free", { active_assignment_count: 0 }),
    ],
  });
  assert.deepEqual(orderedTechnicians(state).map((item) => item.technician_id), ["free", "busy", "away"]);
});

test("a column header shows what is held and what is being added", () => {
  const state = board();
  const load = columnLoad(state, { a: TECH_BUSY }, state.technicians[1]);
  assert.equal(load.current, 3);
  assert.equal(load.adding, 1);
  assert.equal(load.hours, 5);
});

test("durations read as hours, not seconds", () => {
  assert.equal(hoursLabel(5 * 3600), "5 giờ");
  assert.equal(hoursLabel(4.5 * 3600), "4,5 giờ");
});

test("slack reads as lateness when it is negative", () => {
  assert.match(slackLabel(-7200), /^Trễ 2 giờ/);
  assert.match(slackLabel(3600), /^Còn dư 1 giờ/);
  assert.match(slackLabel(null), /Chưa có lịch hẹn/);
});

// ------------------------------------------------------- the automatic toggle

test("the confirmation modal carries the contract wording", () => {
  // §2 specifies this text. A paraphrase would quietly change what a manager is
  // told before autonomy is switched on.
  for (const fragment of [
    "AI phân loại xác định",
    "không trùng lặp",
    "không phải phản ánh khẩn cấp",
    "tự động duyệt",
    "bỏ qua bước gộp nhóm",
    "phân công ngay lập tức",
    "chuyển cho Ban quản lý",
  ]) {
    assert.ok(AUTO_ASSIGNMENT_CONFIRMATION.includes(fragment), `missing: ${fragment}`);
  }
});

test("switching off says it does not recall work already assigned", () => {
  assert.match(AUTO_ASSIGNMENT_OFF_NOTICE, /chỉ dừng các phân công trong tương lai/);
  assert.match(AUTO_ASSIGNMENT_OFF_NOTICE, /vẫn giữ nguyên/);
});

test("an agent decision and a fallback never share a label", () => {
  // §7: "no model reasoned about this one" is the fact a reviewer is looking
  // for, so the two must be distinguishable at a glance.
  assert.notEqual(decisionSourceLabel("AGENT"), decisionSourceLabel("SCHEDULER_FALLBACK"));
  assert.match(decisionSourceLabel("SCHEDULER_FALLBACK"), /không phản hồi/);
});

// ------------------------------------------------------------------- removals

test("the proposal architecture is gone from the frontend", () => {
  // §9's removal list, asserted against the source. A merge restoring one of
  // these would restore a screen with no backend behind it.
  const types = readFileSync(new URL("../types/api.ts", import.meta.url), "utf8");
  for (const name of [
    "AssignmentProposalBatch",
    "AssignmentProposalItem",
    "AssignmentSchedule",
    "AssignmentJob",
    "AssignmentHistoryRecord",
    "AutoAssignmentDelay",
  ]) {
    assert.ok(!types.includes(name), `${name} is still declared`);
  }

  const api = readFileSync(new URL("../api/backend.api.ts", import.meta.url), "utf8");
  for (const fn of [
    "listCoordinatorAssignmentProposals",
    "createCoordinatorAssignmentProposal",
    "confirmCoordinatorAssignmentProposal",
    "getAssignmentSchedule",
    "listAssignmentHistory",
    "listCoordinatorAssignmentJobs",
  ]) {
    assert.ok(!api.includes(fn), `${fn} is still exported`);
  }
});

test("no resident-facing completion promise survives", () => {
  // §4: the resident sees an expected start and a description of what is
  // happening now. `planned_finish_at` is internal and must not appear in the
  // resident payload at all.
  const types = readFileSync(new URL("../types/api.ts", import.meta.url), "utf8");
  const resident = types.slice(types.indexOf("export type ResidentTicket"), types.indexOf("export type ResidentCategory"));
  assert.ok(!resident.includes("estimated_resolution_text"));
  assert.ok(!resident.includes("expected_resolution_at"));
  assert.ok(!resident.includes("planned_finish_at"));
  assert.ok(resident.includes("progress_text"));
  assert.ok(resident.includes("expected_start_at"));
  // The acceptance SLA is gone from the model, so it is gone from the payload
  // the resident screens are typed against.
  assert.ok(!resident.includes("acceptance_due_at"));
});

/** The acceptance step is removed from the product, not merely hidden.
 *
 *  Read as source text across every screen a person actually uses, because the
 *  failure this guards against is a single label surviving a refactor -- a
 *  button in one branch, a status map entry, a column header -- and no type
 *  error would ever catch it.
 */
test("no acceptance step survives in the technician, resident, BQL, audit or notification UI", () => {
  const files = [
    "../app/technician/page.tsx",
    "../app/technician/tickets/[id]/page.tsx",
    "../app/manager/page.tsx",
    "../app/manager/audit/page.tsx",
    "../components/manager/TicketDetailPanel.tsx",
    "../lib/assignmentStatus.ts",
    "../lib/residentStatus.ts",
    "../lib/mockService.ts",
    "../types/api.ts",
    "../api/backend.api.ts",
  ];
  // Wording a person would read, plus the identifiers behind it. "sẵn sàng
  // nhận việc" is deliberately absent: that is the technician's availability
  // switch -- whether they may be *given* work at all -- and it never meant the
  // acknowledgement step this change removes.
  const banned = [
    ">Nhận việc",
    "Đã nhận việc",
    "xác nhận nhận việc",
    "Cần xác nhận",
    "KTV đã tiếp nhận",
    "Hạn nhận việc",
    "acceptance_due_at",
    "accepted_at",
    "acceptTechnicianAssignment",
    "/accept",
  ];
  for (const file of files) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    for (const phrase of banned) {
      assert.ok(!source.includes(phrase), `${file} still contains ${phrase}`);
    }
    // Only inside a quoted string or a status map -- "ACCEPTED" as a word in a
    // comment explaining why it is gone is exactly what should be kept.
    assert.ok(!source.includes('"ACCEPTED":'), `${file} still maps ACCEPTED`);
    assert.ok(!source.includes("ACCEPTED:"), `${file} still maps ACCEPTED`);
  }
});

/** §4 item 4: the order comes from the backend, and starting is gated on it. */
test("the technician queue renders the backend order and gates starting on it", () => {
  const queue = readFileSync(new URL("../app/technician/page.tsx", import.meta.url), "utf8");
  // The list is whatever `/technician/queue` returned, in that order -- no
  // client-side sort, which would put this screen's idea of "Làm ngay" next to
  // the scheduler's.
  assert.ok(queue.includes("getTechnicianQueue"));
  assert.ok(queue.includes('if (filter === "open") return ordered;'));
  assert.ok(queue.includes('"Làm ngay"') && queue.includes('"Tiếp theo"') && queue.includes("Thứ ${index + 1}"));
  // No completion ETA on a technician card.
  assert.ok(!queue.includes("planned_finish_at"));

  const detail = readFileSync(new URL("../app/technician/tickets/[id]/page.tsx", import.meta.url), "utf8");
  // ASSIGNED offers exactly two actions, and the start one is disabled off the
  // queue head with the rule spelled out.
  assert.ok(detail.includes("Bắt đầu xử lý") && detail.includes("Từ chối"));
  assert.ok(detail.includes("const isQueueHead = ticket.planned_order === 0;"));
  assert.ok(detail.includes("disabled={busy || !isQueueHead}"));
  assert.ok(detail.includes("QUEUE_HEAD_HINT"));
  // IN_PROGRESS offers the other two.
  assert.ok(detail.includes("Hoàn thành") && detail.includes("Không xử lý được"));
});
