/** The SLA table on the manager's report, held to the backend's.
 *
 *  `frontend/lib/risk.ts` restates `POLICY_SLA_MINUTES[SERVICE_HOURS_RISK_V2]`
 *  because a report has to draw its own axis before any ticket has arrived.
 *  Restating it is how the last one went wrong: the reports page carried
 *  `{ P3: 5, P2: 180, P1: 4320 }` -- the v1 wall-clock table -- for the whole of
 *  the v2 rollout, so the promise column said five minutes for what is now an
 *  ordinary mid band, and every P4 and P5 ticket was missing from the chart
 *  entirely because the loop iterated a four-element v1 list.
 *
 *  So the copy is pinned to the original rather than trusted. Reading the
 *  Python is the same trick `managerPriorityForms.test.ts` uses on
 *  `agent_schemas.py`, and for the same reason: a typechecker cannot see across
 *  the language boundary, and these two tables have to agree.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  COMPLIANCE_PRIORITIES,
  EMERGENCY_PRIORITY,
  PRIORITIES,
  PRIORITY_SLA_MINUTES,
  PRIORITY_SLA_TEXT,
} from "../lib/risk.ts";

const repo = new URL("../../", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, repo), "utf8");

/** Source with comments removed.
 *
 *  These assertions are about what the code does, and the comment explaining
 *  why a bad pattern was removed necessarily quotes the bad pattern. Without
 *  this the test fails on its own documentation.
 */
const code = (path: string) =>
  read(path).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const slaClock = read("src/domain/sla_clock.py");

/** The `SERVICE_HOURS_RISK_V2` block of `POLICY_SLA_MINUTES`, as a map. */
function backendSlaMinutes(): Record<string, number> {
  const start = slaClock.indexOf("SlaPolicy.SERVICE_HOURS_RISK_V2: {");
  assert.notEqual(start, -1, "POLICY_SLA_MINUTES no longer has a SERVICE_HOURS_RISK_V2 block");
  const end = slaClock.indexOf("},", start);
  const block = slaClock.slice(start, end);
  const found: Record<string, number> = {};
  for (const [, band, minutes] of block.matchAll(/Priority\.(P\d):\s*(\d+)/g)) {
    found[band] = Number(minutes);
  }
  return found;
}

test("every band the backend promises is on the report, with the same number", () => {
  const backend = backendSlaMinutes();
  assert.equal(Object.keys(backend).length, 5, "the v2 policy should cover all five bands");
  assert.deepEqual(PRIORITY_SLA_MINUTES, backend);
});

test("the report covers every band, not the four the v1 scale had", () => {
  assert.deepEqual(Object.keys(PRIORITY_SLA_MINUTES).sort(), [...PRIORITIES].sort());
  assert.deepEqual(Object.keys(PRIORITY_SLA_TEXT).sort(), [...PRIORITIES].sort());
});

test("the promise text agrees with the minutes behind it", () => {
  // Ten service hours a day, 08:00-18:00. A row saying "3 ngày làm việc" beside
  // a limit of 1800 minutes is only right while that stays true.
  const workingDay = 600;
  assert.equal(PRIORITY_SLA_MINUTES.P1 / workingDay, 3);
  assert.equal(PRIORITY_SLA_MINUTES.P2 / workingDay, 2);
  assert.equal(PRIORITY_SLA_MINUTES.P3 / workingDay, 1);
  assert.equal(PRIORITY_SLA_MINUTES.P4, 180);
  assert.match(PRIORITY_SLA_TEXT.P4, /3 giờ/);
});

test("the emergency band is the one measured in wall-clock minutes", () => {
  assert.equal(PRIORITY_SLA_MINUTES[EMERGENCY_PRIORITY], 5);
  assert.match(PRIORITY_SLA_TEXT[EMERGENCY_PRIORITY], /24\/7/);
});

test("the emergency band is left out of technician SLA compliance", () => {
  /** Not scored as a pass -- absent. An emergency nobody was dispatched to is
   *  not a technician's success or failure. */
  assert.ok(!COMPLIANCE_PRIORITIES.includes(EMERGENCY_PRIORITY));
  assert.deepEqual([...COMPLIANCE_PRIORITIES].sort(), ["P1", "P2", "P3", "P4"]);
});

test("the compliance set matches the backend's", () => {
  const start = slaClock.indexOf("COMPLIANCE_PRIORITIES");
  assert.notEqual(start, -1);
  const block = slaClock.slice(start, start + 400);
  const bands = [...block.matchAll(/Priority\.(P\d)/g)].map((match) => match[1]);
  assert.deepEqual(bands.sort(), [...COMPLIANCE_PRIORITIES].sort());
});

test("compliance rows run highest urgency first", () => {
  /** The report reads top-down and P4 is the one a manager is answering for. */
  assert.deepEqual(COMPLIANCE_PRIORITIES, ["P4", "P3", "P2", "P1"]);
});

// --- the surfaces that were reading the v1 scale ----------------------------

test("the reports page no longer carries its own priority list", () => {
  const page = code("frontend/app/manager/reports/page.tsx");
  assert.ok(
    !/\["P\d"(\s*,\s*"P\d")+\]/.test(page),
    "a hard-coded list of bands is what went stale last time; read them from lib/risk",
  );
  assert.ok(!page.includes('"P0"'), "P0 is not a priority; it never reaches this page from the backend");
  assert.ok(!/slaMinutes|slaText/.test(page), "the local v1 SLA tables should be gone");
});

test("no stylesheet rule is still keyed to P3 as the urgent band", () => {
  const css = read("frontend/app/globals.css");
  assert.ok(!css.includes("techP3Urgent"), "the technician urgent class was renamed");
  assert.ok(!css.includes("p3Urgent"), "the manager urgent row class was renamed");
  assert.ok(!css.includes("ticketPanelP3"), "the emergency panel class was renamed");
});

test("the manager row highlight matches the class the page actually emits", () => {
  /** The bug this replaces: `manager/page.tsx` emitted `emergencyUrgent` and
   *  the stylesheet only knew `p3Urgent`, so a P5 awaiting review rendered as
   *  an ordinary row. Both halves of a rename, or neither. */
  const css = read("frontend/app/globals.css");
  const page = read("frontend/app/manager/page.tsx");
  assert.ok(page.includes("emergencyUrgent"));
  assert.ok(css.includes(".mdRow.emergencyUrgent"));
});

test("every band a priority pill can carry has a rule", () => {
  const css = read("frontend/app/globals.css");
  for (const band of PRIORITIES) {
    assert.ok(css.includes(`.mdPriority-${band}`), `.mdPriority-${band} has no rule`);
  }
});

test("the dispatch log labels the escalation reason the backend sends", () => {
  /** It was keyed `P3_EMERGENCY` while `src/dispatch/service.py` raised
   *  `P5_EMERGENCY`, so the one escalation worth reading rendered as a raw
   *  code. */
  const page = read("frontend/app/manager/dispatch/page.tsx");
  const enums = read("src/models/enums.py");
  assert.ok(enums.includes('P5_EMERGENCY = "P5_EMERGENCY"'));
  assert.ok(page.includes("P5_EMERGENCY:"));
  assert.ok(!page.includes("P3_EMERGENCY"));
});
