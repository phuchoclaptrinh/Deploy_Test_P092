/** The manager forms, held to the five-band scale.
 *
 *  Every bug this guards against is the same shape: a surface written when the
 *  scale ran P1–P3 with P3 as the emergency, left in place after the scale
 *  inverted to P1–P5 with P5 as the emergency. A typechecker sees nothing
 *  wrong -- `<option>P3</option>` is valid JSX either way -- so a P4 ticket
 *  opened its own override form and found a dropdown that did not contain its
 *  current value.
 *
 *  The dropdowns are JSX and cannot be called from Node, so they are asserted
 *  against the source text, the way `emergencyReview.test.ts` and
 *  `assignment.test.ts` already do for rules that are not pure functions.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { OVERRIDE_PRIORITIES, PRIORITIES, PRIORITY_LABELS } from "../lib/risk.ts";
import { QUESTION_KIND_LABELS } from "../lib/questionKinds.ts";

const root = new URL("../", import.meta.url);
const repo = new URL("../", root);

const read = (path: string) => readFileSync(new URL(path, root), "utf8");

/** Every screen with a priority `<select>` on it. */
const FORM_SOURCES = [
  "components/manager/TicketDetailPanel.tsx",
  "app/manager/tickets/[id]/page.tsx",
];

// ---------------------------------------------------------------------------
// The override forms
// ---------------------------------------------------------------------------

test("a coordinator may override to any band except the emergency one", () => {
  assert.deepEqual(OVERRIDE_PRIORITIES, ["P1", "P2", "P3", "P4"]);
});

FORM_SOURCES.forEach((path) => {
  test(`${path} builds its priority select from the shared list`, () => {
    const source = read(path);
    assert.match(source, /OVERRIDE_PRIORITIES\.map/);
  });

  test(`${path} hard-codes no priority options of its own`, () => {
    // The exact failure: a literal option list that stopped at P3, so a P4
    // ticket displayed a value absent from its own dropdown.
    const source = read(path);
    assert.doesNotMatch(source, /<option>P[1-5]<\/option>/);
  });

  test(`${path} says why P5 is not on the list`, () => {
    // Left unexplained, the next person to widen the range adds it back.
    assert.match(read(path), /P5 chỉ đặt được qua cổng duyệt khẩn cấp/);
  });
});

test("the emergency band is reachable from no generic override form", () => {
  assert.ok(!OVERRIDE_PRIORITIES.includes("P5"));
});

test("every band a ticket can hold has a label", () => {
  // Including the two the old scale never had. A missing entry renders
  // `undefined` in a dropdown.
  PRIORITIES.forEach((band) => assert.ok(PRIORITY_LABELS[band], `${band} has no label`));
  assert.deepEqual(PRIORITIES, ["P1", "P2", "P3", "P4", "P5"]);
});

test("the manager list filters across all five bands", () => {
  const source = read("app/manager/page.tsx");
  assert.match(source, /PRIORITIES\]\.reverse\(\)\.map/);
  assert.doesNotMatch(source, /<option>P[1-5]<\/option>/);
});

// ---------------------------------------------------------------------------
// The question history
// ---------------------------------------------------------------------------

test("all eight question kinds have a Vietnamese label", () => {
  /** The contract is the Python enum; drift is what this catches. Read rather
   *  than duplicated, so adding a kind fails here instead of rendering its raw
   *  code to a coordinator. */
  const schema = readFileSync(new URL("src/models/agent_schemas.py", repo), "utf8");
  const rest = schema.slice(schema.indexOf("class AgentQuestionKind") + 1);
  // Sliced to the next top-level definition: the enum body ends with one blank
  // line, not two, so cutting on a paragraph break swept in three later enums.
  const end = rest.search(/^(class |QUESTION_KIND_CRITERION)/m);
  const kinds = [...rest.slice(0, end).matchAll(/^\s{4}([A-Z_]+) = "\1"$/gm)].map((match) => match[1]);
  assert.equal(kinds.length, 8, `expected eight question kinds, found ${kinds.join(", ")}`);
  kinds.forEach((kind) => assert.ok(QUESTION_KIND_LABELS[kind], `${kind} renders as a raw code`));
});

test("the five criterion questions each name their criterion", () => {
  // The point of splitting SEVERITY_CONFIRMATION into five was that a
  // coordinator reading the history can tell which number the answer moved.
  const labels = [
    QUESTION_KIND_LABELS.SAFETY_CONFIRMATION,
    QUESTION_KIND_LABELS.SPREAD_CONFIRMATION,
    QUESTION_KIND_LABELS.ESSENTIAL_FUNCTION_CONFIRMATION,
    QUESTION_KIND_LABELS.AFFECTED_SCOPE_CONFIRMATION,
    QUESTION_KIND_LABELS.DETERIORATION_CONFIRMATION,
  ];
  assert.equal(new Set(labels).size, 5, "two criterion questions share a label");
  labels.forEach((label) => assert.ok(label && !/_/.test(label), `${label} is not Vietnamese prose`));
});

test("the retired severity question has no label to come back to", () => {
  assert.equal(QUESTION_KIND_LABELS.SEVERITY_CONFIRMATION, undefined);
});
