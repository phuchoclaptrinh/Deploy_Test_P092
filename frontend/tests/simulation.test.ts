/** The pure half of the capacity-simulation screen.
 *
 *  `frontend/lib/simulation.ts` has no runtime imports, so Node runs it directly
 *  with type stripping. What is tested here is what a coordinator actually
 *  collides with: a pasted scenario that does not parse, a timestamp that must
 *  not be re-interpreted through the viewer's timezone, and the label maps that
 *  decide what the SLA column says.
 *
 *  Three rules the page must not break are asserted against its source the same
 *  way `assignment.test.ts` checks the DIRECT switch:
 *
 *  * it writes nothing;
 *  * **neither flow is production** — there is no badge to render and no third
 *    column to render it on;
 *  * every SLA label talks about *starting*, never about finishing.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DECISION_SOURCE_LABELS,
  OUTCOME_LABELS,
  REASON_LABELS,
  RISK_REASON_LABELS,
  SAMPLE_SCENARIO,
  SCENARIO_LABELS,
  SCENARIO_NOTES,
  SCENARIO_ORDER,
  SLA_DURATION_SOURCE_LABELS,
  SLA_POLICY_LABELS,
  SLA_STATUS_LABELS,
  SLA_STATUS_TONES,
  SimulationInputError,
  atRiskTickets,
  buildExportFile,
  byTicketId,
  comparisonCards,
  excludedSummary,
  formatClock,
  formatCompliance,
  formatDelta,
  formatMinutes,
  hasSlaOverride,
  lateStartedTickets,
  notStartedTickets,
  parseScenario,
  scenarioOf,
  scenarioSlaPolicy,
} from "../lib/simulation.ts";

const root = new URL("../", import.meta.url);
const pageSource = readFileSync(new URL("app/manager/simulation/page.tsx", root), "utf8");
const libSource = readFileSync(new URL("lib/simulation.ts", root), "utf8");
const typesSource = readFileSync(new URL("types/api.ts", root), "utf8");

// ---------------------------------------------------------------------------
// Two flows, and only two.
// ---------------------------------------------------------------------------

test("the screen renders exactly two columns", () => {
  assert.deepEqual(SCENARIO_ORDER, ["OLD_APP", "NEW_APP"]);
  SCENARIO_ORDER.forEach((key) => { assert.ok(SCENARIO_LABELS[key]); assert.ok(SCENARIO_NOTES[key]); });
});

test("the new app is labelled a simulation, in the column title itself", () => {
  assert.equal(SCENARIO_LABELS.NEW_APP, "App mới (mô phỏng)");
  assert.match(SCENARIO_NOTES.NEW_APP, /chưa áp dụng vào production/i);
});

test("no third scenario survives anywhere the screen can reach", () => {
  // Not "the label was changed" — the string is gone from the simulator's whole
  // frontend surface, so nothing can render it by accident.
  [libSource, pageSource, typesSource].forEach((source) => {
    assert.equal(/PROPOSED_OPTIMIZED/.test(source), false);
    assert.equal(/proposed_optimized/.test(source), false);
    assert.equal(/proposedDelta/.test(source), false);
  });
});

test("nothing on this screen claims to be production", () => {
  // A badge reading "Production" over a hypothetical policy is the single most
  // expensive mistake this screen could make, so the word is not available to it.
  assert.equal(/planned_by_production/.test(pageSource), false);
  assert.equal(/planned_by_production/.test(libSource), false);
  assert.equal(/is_production_parity/.test(pageSource), false);
  assert.equal(/simColBadge">Production/.test(pageSource), false);
});

test("the page reads the two flows by key rather than by column position", () => {
  const run = { old_app: { scenario: "OLD_APP" }, new_app: { scenario: "NEW_APP" } } as never;
  SCENARIO_ORDER.forEach((key) => assert.equal(scenarioOf(run, key).scenario, key));
});

// ---------------------------------------------------------------------------
// Parsing the scenario document.
// ---------------------------------------------------------------------------

test("the sample scenario parses and carries both arrays", () => {
  const scenario = parseScenario(SAMPLE_SCENARIO);
  assert.equal((scenario.tickets as unknown[]).length, 10);
  assert.equal((scenario.technicians as unknown[]).length, 4);
  assert.equal(scenarioSlaPolicy(scenario), "SERVICE_HOURS_DRAFT_V1");
});

test("CSV is refused with a message that says so", () => {
  // A CSV cell is always a string, so `false` and `"false"` become the same
  // value and a boolean flips silently. There is no CSV path left.
  assert.throws(
    () => parseScenario("ticket_id,created_at\nT001,2026-09-01T08:00:00+07:00"),
    (error: unknown) => error instanceof SimulationInputError && /CSV/.test((error as Error).message),
  );
});

test("a bare array is refused — the contract is one object", () => {
  assert.throws(() => parseScenario('[{"ticket_id":"T001"}]'), SimulationInputError);
});

test("broken JSON is reported against the editor", () => {
  assert.throws(() => parseScenario("{,}"), (error: unknown) => error instanceof SimulationInputError && /JSON không hợp lệ/.test((error as Error).message));
});

test("an empty editor is refused before a round trip", () => {
  assert.throws(() => parseScenario("   "), SimulationInputError);
});

test("a scenario missing its arrays names the missing section", () => {
  assert.throws(() => parseScenario('{"tickets": []}'), (error: unknown) => /technicians/.test((error as Error).message));
  assert.throws(() => parseScenario('{"technicians": []}'), (error: unknown) => /tickets/.test((error as Error).message));
});

test("a scenario with no sla_policy is read as the production clock", () => {
  // Which is the risk policy now. The banner has to name whatever the backend
  // will actually run an unspecified scenario under, or it tells a user their
  // scenario is on one clock while the run uses another.
  assert.equal(
    scenarioSlaPolicy(parseScenario('{"tickets":[],"technicians":[]}')),
    "SERVICE_HOURS_RISK_V2",
  );
});

test("the sample lets the policy set every deadline", () => {
  assert.equal(/"sla_minutes"/.test(SAMPLE_SCENARIO), false);
  const tickets = parseScenario(SAMPLE_SCENARIO).tickets as Record<string, unknown>[];
  tickets.forEach((ticket) => assert.equal(ticket.sla_minutes, undefined));
});

test("the sample uses the settings block the backend still accepts", () => {
  // `safety_buffer_minutes` and `current_app` were retired with the third
  // scenario. The strict parser rejects them, so a sample carrying either would
  // fail on the first click.
  const settings = (parseScenario(SAMPLE_SCENARIO).settings ?? {}) as Record<string, unknown>;
  assert.equal(settings.safety_buffer_minutes, undefined);
  assert.equal(settings.current_app, undefined);
  assert.ok(settings.new_app);
  assert.equal(settings.micro_batch_interval_ms, 750);
  assert.equal(settings.micro_batch_size, 20);
});

// ---------------------------------------------------------------------------
// Rendering.
// ---------------------------------------------------------------------------

test("timestamps are read off the payload's own +07:00 offset", () => {
  // Not through `Date`: the backend already answered in Vietnam local time, and
  // going through the viewer's timezone would move every figure on the screen.
  assert.equal(formatClock("2026-09-01T08:16:00+07:00"), "08:16 01/09");
  assert.equal(formatClock(null), "—");
});

test("minutes read the way a coordinator says them", () => {
  assert.equal(formatMinutes(45), "45 phút");
  assert.equal(formatMinutes(125), "2g 05p");
  assert.equal(formatMinutes(-90), "-1g 30p");
  assert.equal(formatMinutes(null), "—");
});

test("a delta keeps its sign", () => {
  assert.equal(formatDelta(3), "+3");
  assert.equal(formatDelta(-2), "-2");
});

test("every enum value has a Vietnamese label", () => {
  // A missing entry renders `undefined` in the column this screen exists for,
  // so the maps are checked against the unions rather than spot-checked.
  (["ASSIGNED", "REQUIRES_MANUAL_P3_REVIEW", "REQUIRES_MANUAL_P5_REVIEW", "NO_ELIGIBLE_TECHNICIAN"] as const).forEach((value) => assert.ok(OUTCOME_LABELS[value]));
  (["ON_TIME", "LATE_STARTED", "OPEN_OVERDUE", "OPEN_NOT_DUE", "NOT_EVALUABLE"] as const).forEach((value) => { assert.ok(SLA_STATUS_LABELS[value]); assert.ok(SLA_STATUS_TONES[value]); });
  (["P3_MANUAL_REVIEW", "P5_MANUAL_REVIEW", "MISSING_SKILL", "TECHNICIAN_UNAVAILABLE", "TECHNICIAN_EXCLUDED"] as const).forEach((value) => assert.ok(REASON_LABELS[value]));
  (["SCHEDULER_SIMULATED", "SCHEDULER_FALLBACK_SIMULATED", "MANUAL_SIMULATED"] as const).forEach((value) => assert.ok(DECISION_SOURCE_LABELS[value]));
  (["POLICY", "INPUT_OVERRIDE"] as const).forEach((value) => assert.ok(SLA_DURATION_SOURCE_LABELS[value]));
  (["WALL_CLOCK_V1", "SERVICE_HOURS_DRAFT_V1", "SERVICE_HOURS_RISK_V2"] as const).forEach((value) => assert.ok(SLA_POLICY_LABELS[value]));
  assert.ok(RISK_REASON_LABELS.START_SLA_RISK);
});

test("the two emergency outcomes are told apart by name", () => {
  // They mean the same thing under two different scales, and a single shared
  // label would make an old comparison read as if it had been run under the new
  // one. The bands are eight months and one inversion apart.
  assert.match(OUTCOME_LABELS.REQUIRES_MANUAL_P3_REVIEW, /P3/);
  assert.match(OUTCOME_LABELS.REQUIRES_MANUAL_P5_REVIEW, /P5/);
  assert.notStrictEqual(OUTCOME_LABELS.REQUIRES_MANUAL_P3_REVIEW, OUTCOME_LABELS.REQUIRES_MANUAL_P5_REVIEW);
  assert.match(REASON_LABELS.P5_MANUAL_REVIEW, /P5/);
});

test("the policy labels say which priority scale each one reads", () => {
  // The same screen can show a run under either, and "giờ phục vụ" alone would
  // not tell a manager whether the P3 in front of them is a ten-hour band or a
  // five-minute emergency.
  assert.match(SLA_POLICY_LABELS.WALL_CLOCK_V1, /P1–P3/);
  assert.match(SLA_POLICY_LABELS.SERVICE_HOURS_DRAFT_V1, /P1–P3/);
  assert.match(SLA_POLICY_LABELS.SERVICE_HOURS_RISK_V2, /P1–P5/);
});

test("the risk policy is the one marked as production", () => {
  assert.match(SLA_POLICY_LABELS.SERVICE_HOURS_RISK_V2, /đang áp dụng/);
  assert.ok(!/đang áp dụng/.test(SLA_POLICY_LABELS.SERVICE_HOURS_DRAFT_V1));
});

test("a scenario declaring the risk policy is read as that policy", () => {
  assert.strictEqual(
    scenarioSlaPolicy({ sla_policy: { mode: "SERVICE_HOURS_RISK_V2" } }),
    "SERVICE_HOURS_RISK_V2",
  );
  assert.strictEqual(scenarioSlaPolicy({ sla_policy: { mode: "WALL_CLOCK_V1" } }), "WALL_CLOCK_V1");
  assert.strictEqual(
    scenarioSlaPolicy({ sla_policy: { mode: "SERVICE_HOURS_DRAFT_V1" } }),
    "SERVICE_HOURS_DRAFT_V1",
  );
  // Unspecified follows the backend default, not the oldest policy.
  assert.strictEqual(scenarioSlaPolicy({}), "SERVICE_HOURS_RISK_V2");
});

test("every SLA label talks about starting, never about finishing", () => {
  // The metric changed meaning. A label still reading "hoàn tất trễ" over a
  // start-time figure would be read as "finished late", which is a different
  // number about a different promise.
  assert.match(SLA_STATUS_LABELS.ON_TIME, /bắt đầu/i);
  assert.match(SLA_STATUS_LABELS.LATE_STARTED, /bắt đầu/i);
  assert.match(SLA_STATUS_LABELS.OPEN_OVERDUE, /chưa bắt đầu/i);
  assert.match(SLA_STATUS_LABELS.OPEN_NOT_DUE, /chưa bắt đầu/i);
  Object.values(SLA_STATUS_LABELS).forEach((label) => assert.equal(/hoàn tất/i.test(label), false));
});

test("the page says which timestamp the SLA is measured at", () => {
  assert.match(pageSource, /tới nơi và bắt đầu xử lý/);
  assert.match(pageSource, /Thời gian hoàn tất chỉ dùng để tính công suất/);
});

test("an unstarted overdue ticket is red, an unstarted in-time one is amber", () => {
  // Past its deadline and untouched is the clearest breach on the table, so it
  // is not allowed to look milder than a job that at least got started.
  assert.equal(SLA_STATUS_TONES.OPEN_OVERDUE, "bad");
  assert.equal(SLA_STATUS_TONES.LATE_STARTED, "bad");
  assert.equal(SLA_STATUS_TONES.OPEN_NOT_DUE, "warn");
  assert.equal(SLA_STATUS_TONES.ON_TIME, "good");
});

test("the fallback decision source says no model was consulted", () => {
  // The real system would ask an agent here and the simulator did not, so the
  // label must not let that row be read as an AI decision.
  assert.match(DECISION_SOURCE_LABELS.SCHEDULER_FALLBACK_SIMULATED, /không gọi AI/i);
  Object.values(DECISION_SOURCE_LABELS).forEach((label) => assert.match(label, /mô phỏng/i));
});

// ---------------------------------------------------------------------------
// The denominator is never hidden.
// ---------------------------------------------------------------------------

const scenarioResult = {
  scenario: "NEW_APP",
  summary: {
    compliance_rate: 0.8889,
    sla_on_time_tickets: 8,
    sla_evaluable_tickets: 9,
    sla_late_started_tickets: 1,
    sla_open_overdue_tickets: 0,
    sla_open_not_due_tickets: 2,
    sla_not_evaluable_tickets: 1,
  },
  tickets: [
    { ticket_id: "T1", sla_status: "ON_TIME", start_late_minutes: 0, projected_start_late_minutes: 0, risk_state: "SAFE" },
    { ticket_id: "T2", sla_status: "LATE_STARTED", start_late_minutes: 40, projected_start_late_minutes: 40, risk_state: "AT_RISK" },
    { ticket_id: "T3", sla_status: "OPEN_OVERDUE", start_late_minutes: 900, projected_start_late_minutes: 0, risk_state: "SAFE" },
    { ticket_id: "T4", sla_status: "LATE_STARTED", start_late_minutes: 120, projected_start_late_minutes: 200, risk_state: "AT_RISK" },
    { ticket_id: "T5", sla_status: "OPEN_NOT_DUE", start_late_minutes: 0, projected_start_late_minutes: 0, risk_state: "SAFE" },
  ],
} as never;

test("a compliance rate always arrives with its denominator", () => {
  // A rate with a hidden denominator is one that improves by losing tickets.
  assert.equal(formatCompliance(scenarioResult), "88.9% (8/9 đánh giá được)");
});

test("a rate over an empty denominator is not rendered as 0% or 100%", () => {
  const empty = { summary: { compliance_rate: null, sla_on_time_tickets: 0, sla_evaluable_tickets: 0 } } as never;
  assert.match(formatCompliance(empty), /chưa có ticket nào đánh giá được/);
});

test("only what is genuinely outside the denominator is listed as excluded", () => {
  // `OPEN_OVERDUE` is deliberately absent: it is *in* the denominator, and
  // listing it as excluded would be the exact misreading this screen prevents.
  const text = excludedSummary(scenarioResult);
  assert.match(text, /2 chưa bắt đầu nhưng chưa tới hạn/);
  assert.match(text, /1 không đánh giá được/);
  assert.equal(/quá hạn/.test(text), false);
});

test("late starts, unstarted work and at-risk assignments are three tables", () => {
  // "Started late", "never started" and "assigned but not guaranteed" are three
  // different problems with three different fixes.
  assert.deepEqual(lateStartedTickets(scenarioResult).map((t) => t.ticket_id), ["T4", "T2"]);
  assert.deepEqual(notStartedTickets(scenarioResult).map((t) => t.ticket_id), ["T3", "T5"]);
  assert.deepEqual(atRiskTickets(scenarioResult).map((t) => t.ticket_id), ["T4", "T2"]);
});

test("tickets can be looked up by id", () => {
  assert.equal(byTicketId(scenarioResult).get("T2")?.start_late_minutes, 40);
});

// ---------------------------------------------------------------------------
// The comparison cards, and the sign that must not be flipped twice.
// ---------------------------------------------------------------------------

const comparisonRun = {
  old_app: { summary: { bql_effort_minutes: 180, sla_late_started_tickets: 3, total_start_late_minutes: 168 } },
  new_app: { summary: { bql_effort_minutes: 40, sla_late_started_tickets: 2, total_start_late_minutes: 0 } },
  comparison: {
    bql_minutes_saved: 140,
    bql_hours_saved: 2.33,
    late_starts_avoided: 1,
    start_late_minutes_avoided: 168,
    average_response_minutes_saved: 14.7,
    p95_response_minutes_saved: 17,
    travel_minutes_saved: -20,
    compliance_rate_gain: 0.1111,
  },
} as never;

test("the cards read the comparison straight off the payload", () => {
  // The backend already computed OLD − NEW. A minus sign in JSX is exactly how
  // one of these numbers eventually gets rendered backwards.
  const cards = comparisonCards(comparisonRun);
  assert.equal(cards.length, 3);
  assert.equal(cards[0].value, "2.33 giờ");
  assert.equal(cards[1].value, "+1");
  assert.equal(cards[2].value, "2g 48p");
  cards.forEach((card) => assert.equal(card.better, true));
});

test("the page never negates a comparison value", () => {
  assert.equal(/-\(.*bql_hours_saved/.test(pageSource), false);
  assert.equal(/comparison\.\w+\s*\*\s*-1/.test(pageSource), false);
  assert.match(pageSource, /comparisonCards/);
});

test("a card whose number favours the old app is not painted as a win", () => {
  const losing = {
    ...(comparisonRun as unknown as Record<string, unknown>),
    comparison: { ...(comparisonRun as never as { comparison: Record<string, number> }).comparison, bql_minutes_saved: -30, bql_hours_saved: -0.5 },
  } as never;
  assert.equal(comparisonCards(losing)[0].better, false);
});

test("every card label is about starting, and none mentions a proposal", () => {
  comparisonCards(comparisonRun).forEach((card) => {
    assert.equal(/đề xuất/i.test(card.label), false);
    assert.equal(/hoàn tất/i.test(card.label), false);
  });
  assert.match(comparisonCards(comparisonRun)[1].label, /bắt đầu trễ/i);
});

test("the export carries the policy and settings the run was produced under", () => {
  const run = { generated_at: "2026-09-01T10:30:00+07:00", sla_policy: "SERVICE_HOURS_DRAFT_V1", settings: { micro_batch_size: 20 }, warnings: [] } as never;
  const file = buildExportFile(run);
  assert.match(file.filename, /^mo-phong-cong-suat-.*\.json$/);
  const parsed = JSON.parse(file.content);
  assert.equal(parsed.sla_policy, "SERVICE_HOURS_DRAFT_V1");
  assert.equal(parsed.settings.micro_batch_size, 20);
});

// ---------------------------------------------------------------------------
// A deadline the policy did not set.
// ---------------------------------------------------------------------------

test("a column containing a pinned deadline is flagged", () => {
  const withOverride = { tickets: [{ sla_duration_source: "POLICY" }, { sla_duration_source: "INPUT_OVERRIDE" }] } as never;
  const clean = { tickets: [{ sla_duration_source: "POLICY" }] } as never;
  assert.equal(hasSlaOverride(withOverride), true);
  assert.equal(hasSlaOverride(clean), false);
});

test("the page warns when a run pinned its own deadlines", () => {
  assert.match(pageSource, /hasSlaOverride/);
  assert.match(pageSource, /hạn SLA tự đặt/);
});

// ---------------------------------------------------------------------------
// The screen writes nothing, and shows what an at-risk row needs.
// ---------------------------------------------------------------------------

test("the simulation page calls no endpoint but the simulation run", () => {
  // A what-if screen that could assign, approve or dispatch would stop being a
  // what-if. The only backend call it may make is the read-only run.
  const imported = /import \{([^}]*)\} from "@\/api\/backend\.api"/.exec(pageSource);
  assert.deepEqual(imported?.[1].split(",").map((name) => name.trim()).filter(Boolean), ["runCapacitySimulation"]);
});

test("the at-risk table shows everything a coordinator must act on", () => {
  // Projected start, the deadline, how late, that BQL would be told, and who
  // decided. Any one of those missing turns the row into an unexplained flag.
  assert.match(pageSource, /projected_start_at/);
  assert.match(pageSource, /projected_start_late_minutes/);
  assert.match(pageSource, /would_notify_bql/);
  assert.match(pageSource, /Sẽ thông báo BQL/);
  assert.match(pageSource, /DECISION_SOURCE_LABELS/);
});

test("the ticket table separates departure, start and completion", () => {
  assert.match(pageSource, /Bắt đầu di chuyển/);
  assert.match(pageSource, /Thời điểm bắt đầu/);
  assert.match(pageSource, /departed_at/);
  assert.match(pageSource, /work_started_at/);
});

test("the page shows a draft-policy warning", () => {
  assert.match(pageSource, /SERVICE_HOURS_DRAFT_V1/);
  assert.match(pageSource, /chưa áp dụng production/i);
});

test("the page only accepts .json uploads", () => {
  assert.match(pageSource, /accept="\.json,application\/json"/);
});
