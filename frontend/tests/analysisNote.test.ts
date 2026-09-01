/** The AI note shown on the coordinator detail panel.
 *
 *  The bug this guards against: a DUPLICATE_UNCERTAIN ticket carries its
 *  explanation in `duplicate_reason`, not `ai_reason`, and the panel used to
 *  render only `ai_reason` -- so a coordinator opening an uncertain-duplicate
 *  ticket saw the classification rationale and nothing about the duplicate.
 *
 *  `frontend/lib/analysisNote.ts` has no runtime imports, so Node runs it
 *  directly with type stripping.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { analysisNoteBlocks, duplicateCandidateHints, manualReviewReason } from "../lib/analysisNote.ts";

const root = new URL("../", import.meta.url);

function analysis(overrides: Record<string, unknown> = {}) {
  return {
    exit_reason: "ANALYSIS_COMPLETE",
    ai_reason: "Mô tả nêu rò nước từ trần nhà tắm, khớp Category và vị trí đã chọn.",
    duplicate_reason: null,
    duplicate_verdict: null,
    duplicate_candidates: [],
    error_code: null,
    ...overrides,
  } as never;
}

function candidate(overrides: Record<string, unknown> = {}) {
  return {
    ticket_id: "t-1",
    display_code: "PA-B5C810",
    category_name: "Thang máy",
    location_label: "Thang máy · Tầng 4",
    floor_label: "4",
    status: "APPROVED",
    summary: "cửa thang có vẻ bị kẹt nhưng chưa rõ có người bên trong hay không.",
    created_at: null,
    completed_at: null,
    recently_completed: false,
    ...overrides,
  };
}

test("a plain run shows only the classification rationale", () => {
  const blocks = analysisNoteBlocks(analysis());
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].key, "classification");
  assert.match(blocks[0].text, /rò nước/);
});

test("an uncertain duplicate adds the duplicate reason as its own block", () => {
  const blocks = analysisNoteBlocks(analysis({
    exit_reason: "DUPLICATE_UNCERTAIN",
    duplicate_verdict: "UNCERTAIN",
    duplicate_reason: "Cùng thang máy tầng 4 nhưng chưa rõ có cùng hiện tượng với ứng viên nào.",
  }));
  assert.deepEqual(blocks.map((block) => block.key), ["classification", "duplicate"]);
  assert.match(blocks[1].text, /chưa rõ có cùng hiện tượng/);
});

test("an uncertain duplicate with no recorded reason still gets a duplicate block", () => {
  const blocks = analysisNoteBlocks(analysis({ exit_reason: "DUPLICATE_UNCERTAIN", duplicate_reason: "   " }));
  assert.deepEqual(blocks.map((block) => block.key), ["classification", "duplicate"]);
  assert.match(blocks[1].text, /không ghi lại lý do/);
});

test("an uncertain duplicate that lost its classification prose still shows the duplicate block", () => {
  const blocks = analysisNoteBlocks(analysis({
    ai_reason: null,
    exit_reason: "DUPLICATE_UNCERTAIN",
    duplicate_reason: "Hai phản ánh khác tầng.",
  }));
  assert.deepEqual(blocks.map((block) => block.key), ["duplicate"]);
});

test("a limit-reached run explains why it stopped", () => {
  const blocks = analysisNoteBlocks(analysis({ exit_reason: "LIMIT_REACHED" }));
  assert.deepEqual(blocks.map((block) => block.key), ["classification", "limit"]);
  assert.match(blocks[1].text, /hết lượt hỏi/);
});

test("a technically failed run yields no note blocks -- the error box owns that", () => {
  assert.deepEqual(analysisNoteBlocks(analysis({ error_code: "DB_TIMEOUT", ai_reason: "OperationalError: ..." })), []);
  assert.deepEqual(analysisNoteBlocks(null), []);
});

test("an uncertain duplicate lists every candidate the agent weighed", () => {
  const hints = duplicateCandidateHints(analysis({
    exit_reason: "DUPLICATE_UNCERTAIN",
    duplicate_candidates: [
      candidate(),
      candidate({ ticket_id: "t-2", display_code: "PA-797684", status: "NEW", summary: "Màn hình hiển thị tầng chớp nhẹ.", recently_completed: true }),
    ],
  }));
  assert.deepEqual(hints.map((hint) => hint.code), ["PA-B5C810", "PA-797684"]);
  assert.match(hints[0].detail, /cửa thang/);
  assert.equal(hints[1].status, "NEW");
  assert.equal(hints[1].recentlyCompleted, true);
});

test("a candidate with no summary falls back to its category and location", () => {
  const [hint] = duplicateCandidateHints(analysis({
    exit_reason: "DUPLICATE_UNCERTAIN",
    duplicate_candidates: [candidate({ summary: "  " })],
  }));
  assert.equal(hint.detail, "Thang máy · Thang máy · Tầng 4");
});

test("candidate hints are empty for any exit that is not an uncertain duplicate", () => {
  assert.deepEqual(duplicateCandidateHints(analysis({ duplicate_candidates: [candidate()] })), []);
  assert.deepEqual(duplicateCandidateHints(analysis({ exit_reason: "LIMIT_REACHED", duplicate_candidates: [candidate()] })), []);
  assert.deepEqual(duplicateCandidateHints(null), []);
});

test("manualReviewReason names each waiting state", () => {
  assert.match(manualReviewReason({ latest_analysis: null } as never), /chưa có đủ dữ liệu/);
  assert.match(manualReviewReason({ latest_analysis: analysis({ error_code: "DB_TIMEOUT" }) } as never), /lỗi kỹ thuật \(DB_TIMEOUT\)/);
  assert.match(manualReviewReason({ latest_analysis: analysis({ exit_reason: "DUPLICATE_UNCERTAIN" }) } as never), /chưa đủ chắc chắn để kết luận trùng/);
  assert.match(manualReviewReason({ latest_analysis: analysis({ exit_reason: "LIMIT_REACHED" }) } as never), /hết lượt hỏi/);
});

test("both coordinator surfaces render the note through the shared helper", () => {
  // Neither surface may re-derive the note inline: that is how the panel drifted
  // from the duplicate reason in the first place.
  const panel = readFileSync(new URL("components/manager/TicketDetailPanel.tsx", root), "utf8");
  assert.match(panel, /analysisNoteBlocks\(/);
  assert.match(panel, /duplicateCandidateHints\(/);
  assert.doesNotMatch(panel, /latest_analysis\?\.ai_reason/);

  const page = readFileSync(new URL("components/manager/ManagerManualReview.tsx", root), "utf8");
  assert.match(page, /from "@\/lib\/analysisNote"/);
  assert.doesNotMatch(page, /function manualReviewReason\(/);
});
