/** The rubric vocabulary the manager screens read from.
 *
 *  `frontend/lib/risk.ts` has no runtime imports, so Node runs it directly with
 *  no bundler and no DOM. What these tests are for is the class of bug a
 *  typechecker cannot see: a label that says the wrong band, a weight that
 *  drifts from `docs/risk_scoring_v2.md`, or a breakdown that does not add up to
 *  the total printed beside it.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  BLOCKER_CODES,
  BLOCKER_FLOORS,
  BLOCKER_LABELS,
  CRITERIA,
  CRITERION_ANCHORS,
  CRITERION_LABELS,
  CRITERION_WEIGHTS,
  DOWNGRADE_PRIORITIES,
  EMERGENCY_PRIORITY,
  MAX_CRITERION_SCORE,
  PRIORITIES,
  PRIORITY_BANDS,
  PRIORITY_LABELS,
  PRIORITY_TONES,
  blockerRaisedPriority,
  criterionPoints,
  criterionScore,
  formatBlocker,
  formatRiskScore,
  isEmergency,
  scopeWasOverruled,
} from "../lib/risk.ts";
import type { RiskAssessment } from "../types/api.ts";

const repo = new URL("../../", import.meta.url);
const riskScoringSource = readFileSync(new URL("src/domain/risk_scoring.py", repo), "utf8");

function backendCriterionWeights(): Record<string, number> {
  const start = riskScoringSource.indexOf("CRITERION_WEIGHTS");
  assert.notEqual(start, -1, "backend CRITERION_WEIGHTS block is missing");
  const open = riskScoringSource.indexOf("{", start);
  const close = riskScoringSource.indexOf("}", open);
  const block = riskScoringSource.slice(open, close);
  const weights: Record<string, number> = {};
  for (const [, criterion, weight] of block.matchAll(/"([a-z_]+)":\s*Decimal\((?:"|')?(\d+(?:\.\d+)?)(?:"|')?\)/g)) {
    weights[criterion] = Number(weight);
  }
  return weights;
}

function assessment(overrides: Partial<RiskAssessment> = {}): RiskAssessment {
  return {
    id: "a1",
    revision_no: 1,
    source: "AI_ANALYSIS",
    human_safety_score: 1,
    property_spread_score: 1,
    essential_function_score: 1,
    deterioration_speed_score: 1,
    ai_scope_score: 0,
    backend_scope_score: null,
    effective_scope_score: 0,
    confirmed_affected_unit_count: null,
    blocker_codes: [],
    evidence: {},
    unknown_facts: [],
    risk_score: 20,
    score_priority: "P2",
    blocker_floor: null,
    final_priority: "P2",
    rubric_version: "risk-v2.1",
    case_id_snapshot: null,
    case_density_snapshot: null,
    override_reason: null,
    reviewed_by: null,
    created_at: "2026-08-29T00:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// The rubric
// ---------------------------------------------------------------------------

test("the five weights match the backend calculator and sum to 100", () => {
  assert.deepEqual(CRITERION_WEIGHTS, backendCriterionWeights());
  assert.equal(
    CRITERIA.reduce((total, criterion) => total + CRITERION_WEIGHTS[criterion], 0),
    100,
  );
});

test("the criteria render heaviest first", () => {
  // Two screens showing the same five rows in different orders is a small bug
  // that costs a reader a second every time they cross-check one against the
  // other.
  const weights = CRITERIA.map((criterion) => CRITERION_WEIGHTS[criterion]);
  assert.deepEqual(weights, [...weights].sort((a, b) => b - a));
});

test("every criterion has a label and five anchors", () => {
  CRITERIA.forEach((criterion) => {
    assert.ok(CRITERION_LABELS[criterion]);
    assert.equal(CRITERION_ANCHORS[criterion].length, MAX_CRITERION_SCORE + 1);
    CRITERION_ANCHORS[criterion].forEach((anchor) => assert.ok(anchor.length > 0));
  });
});

test("a criterion at full score contributes its whole weight", () => {
  CRITERIA.forEach((criterion) => {
    assert.equal(criterionPoints(criterion, MAX_CRITERION_SCORE), CRITERION_WEIGHTS[criterion]);
    assert.equal(criterionPoints(criterion, 0), 0);
  });
});

test("the breakdown adds up to the score the backend sent", () => {
  // The one arithmetic claim this module makes. If it drifts, a manager sees
  // five rows that do not add up to the number above them and has no way to
  // tell which half is wrong.
  const row = assessment();
  const total = CRITERIA.reduce(
    (sum, criterion) => sum + criterionPoints(criterion, criterionScore(row, criterion)),
    0,
  );
  assert.equal(Number(total.toFixed(2)), row.risk_score);
});

test("the breakdown uses the effective scope, not the estimate", () => {
  // Showing the Agent's guess would make the rows stop adding up to the total.
  const row = assessment({ ai_scope_score: 4, backend_scope_score: 1, effective_scope_score: 1 });
  assert.equal(criterionScore(row, "affected_scope"), 1);
});

// ---------------------------------------------------------------------------
// Priority
// ---------------------------------------------------------------------------

test("the scale runs P1 to P5 with the emergency at the top", () => {
  assert.deepEqual(PRIORITIES, ["P1", "P2", "P3", "P4", "P5"]);
  assert.equal(EMERGENCY_PRIORITY, "P5");
  assert.equal(isEmergency("P5"), true);
  assert.equal(isEmergency("P3"), false);
  assert.equal(isEmergency(null), false);
});

test("every band has a label, a score range and a tone", () => {
  PRIORITIES.forEach((band) => {
    assert.ok(PRIORITY_LABELS[band]);
    assert.ok(PRIORITY_BANDS[band]);
    assert.ok(PRIORITY_TONES[band]);
  });
});

test("the bands are contiguous and cover nought to a hundred", () => {
  const bounds = PRIORITIES.map((band) => PRIORITY_BANDS[band].split("–").map(Number));
  assert.deepEqual(bounds[0], [0, 20]);
  assert.deepEqual(bounds[bounds.length - 1], [80, 100]);
  bounds.slice(1).forEach(([low], index) => assert.equal(low, bounds[index]![1]));
});

test("only the emergency band gets the critical tone", () => {
  // Spending a distinct alarm colour on more than one band is how the alarm
  // stops meaning anything.
  const critical = PRIORITIES.filter((band) => PRIORITY_TONES[band] === "critical");
  assert.deepEqual(critical, ["P5"]);
});

test("an emergency cannot be downgraded to itself", () => {
  // Staying at P5 is *confirming*, which is a different action with different
  // consequences: it does not unlock assignment.
  assert.deepEqual(DOWNGRADE_PRIORITIES, ["P1", "P2", "P3", "P4"]);
  assert.ok(!DOWNGRADE_PRIORITIES.includes("P5"));
});

// ---------------------------------------------------------------------------
// Blockers
// ---------------------------------------------------------------------------

test("there are exactly eleven blockers, seven of them at P5", () => {
  assert.equal(BLOCKER_CODES.length, 11);
  assert.equal(BLOCKER_CODES.filter((code) => BLOCKER_FLOORS[code] === "P5").length, 7);
  assert.equal(BLOCKER_CODES.filter((code) => BLOCKER_FLOORS[code] === "P4").length, 4);
});

test("every blocker has a label and a floor", () => {
  BLOCKER_CODES.forEach((code) => {
    assert.ok(BLOCKER_LABELS[code]);
    assert.ok(BLOCKER_FLOORS[code]);
  });
});

test("an unknown blocker code renders as itself rather than as undefined", () => {
  // A backend that adds a code before the frontend knows it should show the
  // code, not a blank cell where an emergency was.
  assert.equal(formatBlocker("SOMETHING_NEW"), "SOMETHING_NEW");
  assert.equal(formatBlocker("FIRE_OR_SMOKE"), BLOCKER_LABELS.FIRE_OR_SMOKE);
});

// ---------------------------------------------------------------------------
// Reading one assessment
// ---------------------------------------------------------------------------

test("a blocker that decided the outcome is detectable", () => {
  // The case where the number on screen does not explain the band on screen.
  assert.equal(blockerRaisedPriority(assessment()), false);
  assert.equal(
    blockerRaisedPriority(assessment({ score_priority: "P2", final_priority: "P5", blocker_floor: "P5" })),
    true,
  );
});

test("a scope the backend overruled is detectable, in both directions", () => {
  assert.equal(scopeWasOverruled(assessment()), false);
  assert.equal(scopeWasOverruled(assessment({ ai_scope_score: 4, backend_scope_score: 1 })), true);
  assert.equal(scopeWasOverruled(assessment({ ai_scope_score: 0, backend_scope_score: 3 })), true);
  // Agreeing is not overruling, and flagging it would cry wolf on every case.
  assert.equal(scopeWasOverruled(assessment({ ai_scope_score: 2, backend_scope_score: 2 })), false);
});

test("a score renders to two places, and a missing one renders as a dash", () => {
  assert.equal(formatRiskScore(21.25), "21.25");
  assert.equal(formatRiskScore(80), "80.00");
  assert.equal(formatRiskScore(null), "—");
  assert.equal(formatRiskScore(undefined), "—");
});
