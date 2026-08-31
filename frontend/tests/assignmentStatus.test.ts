import assert from "node:assert/strict";
import test from "node:test";
import { assignmentStatusDisplay } from "../lib/assignmentStatus.ts";

test("assignment states use the KTV-facing labels", () => {
  assert.deepEqual(assignmentStatusDisplay("ASSIGNED"), { label: "Đã gán", tone: "success" });
  assert.deepEqual(assignmentStatusDisplay("IN_PROGRESS"), { label: "Đang xử lý", tone: "processing" });
  assert.deepEqual(assignmentStatusDisplay("REJECTED"), { label: "KTV từ chối", tone: "warning" });
});

test("there is no acknowledgement state between assigned and in progress", () => {
  // ACCEPTED is gone from the enum, so an unknown value falls through to the
  // generic label rather than to its own wording. This is what stops a stale
  // client, or a payload from an older deployment, from reviving "KTV đã tiếp
  // nhận" on a screen.
  assert.deepEqual(assignmentStatusDisplay("ACCEPTED"), { label: "Đã gán", tone: "neutral" });
});

test("no assignment state leaves the ticket lifecycle label in charge", () => {
  assert.equal(assignmentStatusDisplay(null), null);
});
