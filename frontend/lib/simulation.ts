import type { SimulationComparison, SimulationDecisionSource, SimulationOutcome, SimulationReason, SimulationRun, SimulationScenarioKey, SimulationScenarioResult, SimulationSlaDurationSource, SimulationSlaPolicy, SimulationSlaStatus, SimulationTicketOutcome } from "@/types/api";

/** The pure half of the capacity-simulation screen.
 *
 *  Everything here is a function of its arguments: no fetch, no React, no
 *  runtime import. That is what lets `frontend/tests/simulation.test.ts` run it
 *  under `node --test` with type stripping, and it is also why the parsing lives
 *  here rather than inline in the page — a coordinator pasting a scenario that
 *  does not parse is the single most likely thing to happen on this screen, so
 *  the code that handles it is the code that gets tested.
 *
 *  The backend re-validates all of it, and is strictly the stricter of the two.
 *  Nothing here is a security boundary; it exists so that "line 14 is missing a
 *  timezone" arrives before a round trip rather than after one.
 *
 *  **Two flows, and neither is production.** There is no parity flag to render
 *  and no badge to earn: `NEW_APP` is a hypothetical policy, and every label in
 *  this file says so.
 *
 *  **SLA is measured when the technician arrives and starts.** Every label about
 *  lateness here says "bắt đầu", never "hoàn tất".
 */

export const SCENARIO_LABELS: Record<SimulationScenarioKey, string> = {
  OLD_APP: "App cũ (thủ công)",
  NEW_APP: "App mới (mô phỏng)",
};

/** The subtitle under each column. Neither claims to be production, and the
 *  second says outright that it is not. */
export const SCENARIO_NOTES: Record<SimulationScenarioKey, string> = {
  OLD_APP: "Phân loại và điều phối tay từng ticket, xử lý theo thứ tự đến",
  NEW_APP: "Luồng tự động theo chính sách giả định; chưa áp dụng vào production",
};

export const SLA_POLICY_LABELS: Record<SimulationSlaPolicy, string> = {
  WALL_CLOCK_V1: "Đồng hồ treo tường (thang P1–P3 cũ)",
  SERVICE_HOURS_DRAFT_V1: "Giờ phục vụ 08:00–18:00 (đề xuất, thang P1–P3 cũ)",
  SERVICE_HOURS_RISK_V2: "Giờ phục vụ 08:00–18:00, thang rủi ro P1–P5 (đang áp dụng)",
};

/** `ASSIGNED` means a technician was chosen — the SLA column says whether the
 *  work actually began. */
export const OUTCOME_LABELS: Record<SimulationOutcome, string> = {
  ASSIGNED: "Đã phân công",
  REQUIRES_MANUAL_P3_REVIEW: "Chờ BQL duyệt P3",
  REQUIRES_MANUAL_P5_REVIEW: "Chờ BQL xử lý khẩn cấp P5",
  NO_ELIGIBLE_TECHNICIAN: "Không có KTV phù hợp",
};

/** Every one of these is about *starting*, not finishing. */
export const SLA_STATUS_LABELS: Record<SimulationSlaStatus, string> = {
  ON_TIME: "Bắt đầu đúng hạn",
  LATE_STARTED: "Bắt đầu trễ",
  OPEN_OVERDUE: "Chưa bắt đầu, đã quá hạn",
  OPEN_NOT_DUE: "Chưa bắt đầu, chưa tới hạn",
  NOT_EVALUABLE: "Không đánh giá được",
};

/** Maps to the three badge colours the page defines. `OPEN_OVERDUE` is red, not
 *  amber: a ticket past its deadline that nobody has touched is the clearest
 *  breach on the table. `OPEN_NOT_DUE` is amber — not yet a failure, and not a
 *  success either. */
export const SLA_STATUS_TONES: Record<SimulationSlaStatus, "good" | "warn" | "bad"> = {
  ON_TIME: "good",
  LATE_STARTED: "bad",
  OPEN_OVERDUE: "bad",
  OPEN_NOT_DUE: "warn",
  NOT_EVALUABLE: "warn",
};

export const REASON_LABELS: Record<SimulationReason, string> = {
  P3_MANUAL_REVIEW: "P3 phải do BQL xử lý tay",
  P5_MANUAL_REVIEW: "P5 khẩn cấp, BQL xử lý tay",
  MISSING_SKILL: "Không KTV nào có kỹ năng này",
  TECHNICIAN_UNAVAILABLE: "KTV có kỹ năng nhưng không khả dụng",
  TECHNICIAN_EXCLUDED: "KTV phù hợp bị loại trừ cho ticket này",
};

/** None of these claims a model decided anything. `SCHEDULER_FALLBACK_SIMULATED`
 *  is the deterministic conservative branch the simulator takes where the real
 *  system would consult an agent — saying otherwise would credit the AI with a
 *  decision it never made. */
export const DECISION_SOURCE_LABELS: Record<SimulationDecisionSource, string> = {
  SCHEDULER_SIMULATED: "Bộ xếp lịch (mô phỏng)",
  SCHEDULER_FALLBACK_SIMULATED: "Fallback mô phỏng (không gọi AI)",
  MANUAL_SIMULATED: "Điều phối tay (mô phỏng)",
};

export const RISK_REASON_LABELS: Record<string, string> = {
  START_SLA_RISK: "Không KTV nào bắt đầu kịp hạn",
};

/** Where the row's deadline came from. `INPUT_OVERRIDE` is the one a
 *  coordinator has to notice: that ticket is not measured against the policy the
 *  run is nominally under. */
export const SLA_DURATION_SOURCE_LABELS: Record<SimulationSlaDurationSource, string> = {
  POLICY: "Theo chính sách",
  INPUT_OVERRIDE: "Hạn tự đặt (khác chính sách)",
};

/** True when the run pinned a deadline the policy disagrees with. The screen
 *  flags the column, because a compliance figure built partly on typed-in
 *  deadlines is not a figure about the policy. */
export function hasSlaOverride(result: SimulationScenarioResult): boolean {
  return result.tickets.some((ticket) => ticket.sla_duration_source === "INPUT_OVERRIDE");
}

export class SimulationInputError extends Error {}

/** The scenario document, out of whatever was pasted.
 *
 *  JSON only. The CSV path is gone on purpose: a CSV cell is always a string,
 *  so `false` and `"false"` become the same value and a boolean flips silently.
 *  Rejecting it here means the coordinator finds out in the editor rather than
 *  from a result that quietly answered a different question.
 */
export function parseScenario(text: string): Record<string, unknown> {
  const trimmed = (text || "").trim();
  if (!trimmed) throw new SimulationInputError("Chưa có kịch bản. Bấm “Dữ liệu mẫu” để bắt đầu.");
  if (!trimmed.startsWith("{")) {
    throw new SimulationInputError("Kịch bản phải là một object JSON duy nhất (bắt đầu bằng “{”). Không còn hỗ trợ CSV.");
  }
  let payload: unknown;
  try {
    payload = JSON.parse(trimmed);
  } catch (error) {
    throw new SimulationInputError(`JSON không hợp lệ: ${(error as Error).message}`);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new SimulationInputError("Kịch bản phải là một object JSON duy nhất.");
  }
  const scenario = payload as Record<string, unknown>;
  // Checked here only so the message names the missing section; the backend
  // owns the real rules and is stricter than this.
  for (const key of ["technicians", "tickets"]) {
    if (!Array.isArray(scenario[key])) throw new SimulationInputError(`Kịch bản thiếu mảng “${key}”.`);
  }
  return scenario;
}

/** Which SLA policy a scenario document asks for, for the banner.
 *
 *  The fallback has to be whatever `CURRENT_POLICY` is on the backend, because
 *  that is what an unspecified scenario will actually be run under. It said
 *  `WALL_CLOCK_V1` while the backend had already moved to the risk scale, so the
 *  banner told a user their scenario was on the old clock and the run used the
 *  new one -- the one case where being wrong here is worse than saying nothing.
 */
export function scenarioSlaPolicy(scenario: Record<string, unknown>): SimulationSlaPolicy {
  const block = scenario.sla_policy as { mode?: string } | undefined;
  if (block?.mode === "WALL_CLOCK_V1") return "WALL_CLOCK_V1";
  if (block?.mode === "SERVICE_HOURS_DRAFT_V1") return "SERVICE_HOURS_DRAFT_V1";
  return "SERVICE_HOURS_RISK_V2";
}

/** "08:16 01/09" from the payload's own +07:00 timestamp.
 *
 *  Read off the string rather than through `Date`: the backend already answered
 *  in Vietnam local time, and rendering it through the viewer's timezone would
 *  move every figure on the screen for anyone travelling.
 */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!match) return iso;
  const [, , month, day, hour, minute] = match;
  return `${hour}:${minute} ${day}/${month}`;
}

/** Minutes as a coordinator says them: "45 phút", "2g 05p". */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null) return "—";
  const rounded = Math.round(minutes);
  if (Math.abs(rounded) < 60) return `${rounded} phút`;
  const sign = rounded < 0 ? "-" : "";
  const absolute = Math.abs(rounded);
  return `${sign}${Math.floor(absolute / 60)}g ${String(absolute % 60).padStart(2, "0")}p`;
}

export function formatDelta(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

/** "88.9% (8/9 đánh giá được)" — the rate never appears without its denominator,
 *  because a compliance figure with a hidden denominator is one that improves by
 *  losing tickets. */
export function formatCompliance(result: SimulationScenarioResult): string {
  const { compliance_rate: rate, sla_on_time_tickets: onTime, sla_evaluable_tickets: evaluable } = result.summary;
  if (rate == null || evaluable === 0) return "— (chưa có ticket nào đánh giá được)";
  return `${(rate * 100).toFixed(1)}% (${onTime}/${evaluable} đánh giá được)`;
}

/** The sentence under the rate: what is *not* in the denominator.
 *
 *  `OPEN_OVERDUE` is deliberately absent — it *is* in the denominator, and it is
 *  the worst thing on the table. Only the two genuinely excluded groups appear
 *  here.
 */
export function excludedSummary(result: SimulationScenarioResult): string {
  const { sla_open_not_due_tickets: notDue, sla_not_evaluable_tickets: notEvaluable } = result.summary;
  const parts: string[] = [];
  if (notDue) parts.push(`${notDue} chưa bắt đầu nhưng chưa tới hạn`);
  if (notEvaluable) parts.push(`${notEvaluable} không đánh giá được (BQL xử lý tay: P5 hiện tại hoặc P3 ở chính sách cũ)`);
  return parts.join(" · ");
}

/** Tickets that started late, worst first — the table a coordinator opens this
 *  screen for. */
export function lateStartedTickets(result: SimulationScenarioResult): SimulationTicketOutcome[] {
  return result.tickets
    .filter((ticket) => ticket.sla_status === "LATE_STARTED")
    .sort((left, right) => right.start_late_minutes - left.start_late_minutes);
}

/** Tickets nobody has begun, worst overdue first.
 *
 *  Kept apart from the late-start table: "started late" and "not started at all"
 *  are different problems with different fixes, and merging them hides the ones
 *  still waiting.
 */
export function notStartedTickets(result: SimulationScenarioResult): SimulationTicketOutcome[] {
  return result.tickets
    .filter((ticket) => ticket.sla_status === "OPEN_OVERDUE" || ticket.sla_status === "OPEN_NOT_DUE")
    .sort((left, right) => right.start_late_minutes - left.start_late_minutes);
}

/** Assignments the run could not guarantee, worst projected lateness first.
 *
 *  These are the rows that would have raised a notification to Building
 *  Management, so the screen gives them their own table rather than letting them
 *  blend into the day.
 */
export function atRiskTickets(result: SimulationScenarioResult): SimulationTicketOutcome[] {
  return result.tickets
    .filter((ticket) => ticket.risk_state === "AT_RISK")
    .sort((left, right) => right.projected_start_late_minutes - left.projected_start_late_minutes);
}

export function byTicketId(result: SimulationScenarioResult): Map<string, SimulationTicketOutcome> {
  return new Map(result.tickets.map((ticket) => [ticket.ticket_id, ticket]));
}

export function scenarioOf(run: SimulationRun, key: SimulationScenarioKey): SimulationScenarioResult {
  return key === "OLD_APP" ? run.old_app : run.new_app;
}

/** The comparison cards, in the order the screen shows them.
 *
 *  Every value is read straight off `run.comparison`, which is already
 *  `OLD_APP − NEW_APP` (or `NEW_APP − OLD_APP` for the rate). Nothing here
 *  negates anything: a stray minus sign in JSX is exactly how one of these
 *  numbers eventually gets read backwards.
 */
export function comparisonCards(run: SimulationRun): {
  key: keyof SimulationComparison;
  label: string;
  value: string;
  description: string;
  better: boolean;
}[] {
  const c = run.comparison;
  return [
    {
      key: "bql_hours_saved",
      label: "BQL tiết kiệm so với app cũ",
      value: `${c.bql_hours_saved.toFixed(2)} giờ`,
      description: `App mới ${run.new_app.summary.bql_effort_minutes} phút · app cũ ${run.old_app.summary.bql_effort_minutes} phút`,
      better: c.bql_minutes_saved > 0,
    },
    {
      key: "late_starts_avoided",
      label: "Giảm ticket bắt đầu trễ",
      value: formatDelta(c.late_starts_avoided),
      description: `App mới ${run.new_app.summary.sla_late_started_tickets} · app cũ ${run.old_app.summary.sla_late_started_tickets}`,
      better: c.late_starts_avoided > 0,
    },
    {
      key: "start_late_minutes_avoided",
      label: "Giảm phút bắt đầu trễ",
      value: formatMinutes(c.start_late_minutes_avoided),
      description: `App mới ${formatMinutes(run.new_app.summary.total_start_late_minutes)} · app cũ ${formatMinutes(run.old_app.summary.total_start_late_minutes)}`,
      better: c.start_late_minutes_avoided > 0,
    },
  ];
}

export const SCENARIO_ORDER: SimulationScenarioKey[] = ["OLD_APP", "NEW_APP"];

/** The export file: the whole run, including the policy and settings it was
 *  produced under. A results table without its assumptions is one nobody can
 *  reproduce next month. */
export function buildExportFile(run: SimulationRun): { filename: string; content: string } {
  const stamp = (run.generated_at || "").slice(0, 16).replace(/[:T]/g, "-");
  return { filename: `mo-phong-cong-suat-${stamp || "ket-qua"}.json`, content: `${JSON.stringify(run, null, 2)}\n` };
}

/** A scenario that demonstrates the difference on the first click.
 *
 *  Chosen, not random. KTV_01 is the only available plumber and starts on a
 *  150-minute job, so a queue builds behind it; T007 is a P2 with a three-hour
 *  SLA that arrives while that queue is waiting. The old app leaves it at the
 *  back and starts it late; the new app puts it in front of the unstarted P1s.
 *  T006 is a P3 that neither flow may auto-assign.
 *
 *  No ticket carries `sla_minutes`: the policy supplies it. Switch
 *  `sla_policy.mode` to `WALL_CLOCK_V1` and every P1 deadline moves from 1800
 *  service minutes to 4320 wall-clock ones, which is the other comparison this
 *  screen can make.
 */
export const SAMPLE_SCENARIO = `{
  "scenario_name": "Tòa nhà 30 tầng — một ngày mẫu",
  "building": { "floor_count": 30, "units_per_floor": 7 },
  "sla_policy": { "mode": "SERVICE_HOURS_DRAFT_V1" },
  "settings": {
    "travel_base_minutes": 3,
    "travel_per_floor_minutes": 1,
    "micro_batch_interval_ms": 750,
    "micro_batch_size": 20,
    "simulation_horizon_days": 14,
    "old_app": { "manual_category_minutes": 10, "manual_dispatch_minutes": 8 },
    "new_app": { "ai_classification_minutes": 1, "manual_review_minutes": 10 }
  },
  "technicians": [
    { "technician_id": "KTV_01", "skills": ["plumbing", "locksmith"], "start_floor": 1,  "is_active": true, "is_available": true },
    { "technician_id": "KTV_02", "skills": ["electrical", "network"], "start_floor": 10, "is_active": true, "is_available": true },
    { "technician_id": "KTV_03", "skills": ["hvac", "electrical"],    "start_floor": 20, "is_active": true, "is_available": true },
    { "technician_id": "KTV_04", "skills": ["plumbing", "hvac"],      "start_floor": 5,  "is_active": true, "is_available": false }
  ],
  "tickets": [
    { "ticket_id": "T001", "created_at": "2026-09-01T08:00:00+07:00", "floor": 3,  "unit": "0302", "issue_type": "WATER",        "priority": "P1", "repair_minutes": 150, "required_skill": "plumbing",   "need_hand_categorized": false, "score_total": 30 },
    { "ticket_id": "T002", "created_at": "2026-09-01T08:10:00+07:00", "floor": 12, "unit": "1204", "issue_type": "HVAC",         "priority": "P1", "repair_minutes": 90,  "required_skill": "hvac",       "need_hand_categorized": true,  "score_total": 35 },
    { "ticket_id": "T003", "created_at": "2026-09-01T08:20:00+07:00", "floor": 28, "unit": "2802", "issue_type": "POWER_OUTAGE", "priority": "P2", "repair_minutes": 45,  "required_skill": "electrical", "need_hand_categorized": false, "score_total": 72 },
    { "ticket_id": "T004", "created_at": "2026-09-01T08:30:00+07:00", "floor": 9,  "unit": "0905", "issue_type": "WALL_DAMP",    "priority": "P1", "repair_minutes": 90,  "required_skill": "plumbing",   "need_hand_categorized": false, "score_total": 25 },
    { "ticket_id": "T005", "created_at": "2026-09-01T08:40:00+07:00", "floor": 4,  "unit": "0403", "issue_type": "WATER",        "priority": "P1", "repair_minutes": 80,  "required_skill": "plumbing",   "need_hand_categorized": false, "score_total": 20 },
    { "ticket_id": "T006", "created_at": "2026-09-01T09:00:00+07:00", "floor": 19, "unit": "1901", "issue_type": "ELEVATOR",     "priority": "P3", "repair_minutes": 60,  "required_skill": "mechanical", "need_hand_categorized": false, "score_total": 95 },
    { "ticket_id": "T007", "created_at": "2026-09-01T09:15:00+07:00", "floor": 2,  "unit": "0206", "issue_type": "WATER",        "priority": "P2", "repair_minutes": 75,  "required_skill": "plumbing",   "need_hand_categorized": true,  "score_total": 60 },
    { "ticket_id": "T008", "created_at": "2026-09-01T10:05:00+07:00", "floor": 30, "unit": "3007", "issue_type": "INTERNET_TV",  "priority": "P1", "repair_minutes": 50,  "required_skill": "network",    "need_hand_categorized": false, "score_total": 15 },
    { "ticket_id": "T009", "created_at": "2026-09-01T13:20:00+07:00", "floor": 22, "unit": "2203", "issue_type": "POWER_OUTAGE", "priority": "P2", "repair_minutes": 55,  "required_skill": "electrical", "need_hand_categorized": false, "score_total": 68 },
    { "ticket_id": "T010", "created_at": "2026-09-01T17:00:00+07:00", "floor": 15, "unit": "1502", "issue_type": "HVAC",         "priority": "P2", "repair_minutes": 110, "required_skill": "hvac",       "need_hand_categorized": true,  "score_total": 30 }
  ]
}`;
