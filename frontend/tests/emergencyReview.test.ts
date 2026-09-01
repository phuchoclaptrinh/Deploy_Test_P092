/** What the management UI may offer for a ticket held at the emergency P5 gate.
 *
 *  The bug these guard against: `classification_status === "MANUAL_REVIEW"` is
 *  where two different things wait — a report the analysis could not classify,
 *  and one it classified as an emergency. Any surface keying on that alone hands
 *  a coordinator the generic resolve/reject form for an emergency, which the
 *  backend then refuses.
 *
 *  `frontend/lib/emergencyReview.ts` has no runtime imports, so Node runs it directly
 *  with type stripping. The component rules that cannot be expressed as a pure
 *  function are asserted against the source, the same way `assignment.test.ts`
 *  checks that the DIRECT switch is never turned on from the UI.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  EMERGENCY_REVIEW_ACTION,
  isEmergencyReviewPending,
  managerControls,
} from "../lib/emergencyReview.ts";

const root = new URL("../", import.meta.url);

function ticket(overrides: Record<string, unknown> = {}) {
  return {
    available_actions: [EMERGENCY_REVIEW_ACTION],
    latest_analysis: {
      exit_reason: "EMERGENCY_REVIEW_REQUIRED",
      emergency_review_status: "PENDING",
      grouping_status: "WAITING_EMERGENCY_MANAGEMENT_REVIEW",
      ...(overrides.latest_analysis as Record<string, unknown> | undefined),
    },
    ...overrides,
  } as never;
}

test("a pending P5 review is detected from the analysis status", () => {
  assert.equal(isEmergencyReviewPending(ticket()), true);
});

test("a stale action list cannot hide a pending P5 review", () => {
  // A cached payload that still lists the generic actions must not put the
  // generic controls back on screen: the analysis field is the fact.
  const stale = ticket({ available_actions: ["RESOLVE_MANUAL_REVIEW", "REJECT_MANUAL_REVIEW"] });
  assert.equal(isEmergencyReviewPending(stale), true);
  const controls = managerControls(stale);
  assert.equal(controls.canManualReview, false);
  assert.equal(controls.canRejectManualReview, false);
});

test("a pending P5 review offers only the emergency action", () => {
  const controls = managerControls(ticket());
  assert.equal(controls.canReviewEmergency, true);
  assert.equal(controls.canApprove, false);
  assert.equal(controls.canAssign, false);
  assert.equal(controls.canOverride, false);
  assert.equal(controls.canManualReview, false);
  assert.equal(controls.canRejectManualReview, false);
  assert.equal(controls.canDecideDuplicate, false);
});

test("a resolved P5 review restores the ordinary controls", () => {
  const confirmed = ticket({
    available_actions: ["APPROVE", "OVERRIDE_CLASSIFICATION"],
    latest_analysis: { emergency_review_status: "CONFIRMED" },
  });
  assert.equal(isEmergencyReviewPending(confirmed), false);
  const controls = managerControls(confirmed);
  assert.equal(controls.canApprove, true);
  assert.equal(controls.canOverride, true);
});

test("an uncertain duplicate keeps its own review, and is never a P5", () => {
  // The two waiting states are separate gates. Merging them would either hide
  // the duplicate panel or offer the emergency one for a ticket that has no emergency.
  const uncertain = ticket({
    available_actions: ["RESOLVE_MANUAL_REVIEW", "REJECT_MANUAL_REVIEW"],
    latest_analysis: {
      exit_reason: "DUPLICATE_UNCERTAIN",
      emergency_review_status: "NOT_REQUIRED",
      grouping_status: "WAITING_DUPLICATE_DECISION",
    },
  });
  const controls = managerControls(uncertain);
  assert.equal(controls.emergencyPending, false);
  assert.equal(controls.canDecideDuplicate, true);
  assert.equal(controls.canManualReview, true);
});

test("an unclassifiable manual-review ticket still gets the generic form", () => {
  const generic = ticket({
    available_actions: ["RESOLVE_MANUAL_REVIEW", "REJECT_MANUAL_REVIEW"],
    latest_analysis: { exit_reason: "LIMIT_REACHED", emergency_review_status: "NOT_REQUIRED", grouping_status: "NOT_ELIGIBLE" },
  });
  const controls = managerControls(generic);
  assert.equal(controls.emergencyPending, false);
  assert.equal(controls.canManualReview, true);
  assert.equal(controls.canDecideDuplicate, false);
});

test("a ticket with no analysis at all is not treated as gated", () => {
  assert.equal(isEmergencyReviewPending(null), false);
  assert.equal(isEmergencyReviewPending({ available_actions: [], latest_analysis: null } as never), false);
});

test("every management surface decides through the shared predicate", () => {
  // A surface that re-derives the rule inline is a surface that will drift
  // from the backend guard the next time either side changes.
  for (const file of [
    "components/manager/TicketDetailPanel.tsx",
    "components/manager/ManagerManualReview.tsx",
  ]) {
    const source = readFileSync(new URL(file, root), "utf8");
    assert.match(source, /managerControls\(/, file);
    assert.doesNotMatch(source, /emergency_review_status\s*===/, file);
  }
});

test("the detail panel never opens the generic manual form for a P5 ticket", () => {
  const source = readFileSync(new URL("components/manager/TicketDetailPanel.tsx", root), "utf8");
  // The generic form and the generic footer are both behind `!emergencyPending`, and
  // the only control offered instead is the link to the review page.
  assert.match(source, /\{!emergencyPending && \(canApprove \|\| canAssign \|\| canOverride \|\| canManualReview\)/);
  assert.match(source, /\{emergencyPending && <footer/);
  assert.match(source, /manualReview = !emergencyPending &&/);
});
