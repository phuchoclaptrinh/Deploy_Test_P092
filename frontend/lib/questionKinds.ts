/** All eight question kinds the Agent may ask, in Vietnamese.
 *
 *  Five of them name a single rubric criterion. That is the point of the split:
 *  `SEVERITY_CONFIRMATION` used to stand for "ask something about how bad it
 *  is", and a coordinator reading the history could not tell which number the
 *  answer was supposed to move. Each label here names the criterion the answer
 *  fed, so the history reads as a record of what the Agent did not know.
 *
 *  Kept exhaustive against `AgentQuestionKind` in `src/models/agent_schemas.py`;
 *  `frontend/tests/managerPriorityForms.test.ts` reads that enum and fails if a
 *  kind loses its label.
 *
 *  Data rather than a component, so the test runner -- which strips types from
 *  `.ts` but not from `.tsx` -- can import it.
 */
export const QUESTION_KIND_LABELS: Record<string, string> = {
  CATEGORY_CONFIRMATION: "Xác nhận danh mục",
  LOCATION_CONFIRMATION: "Xác nhận vị trí",
  RECENT_COMPLETION: "Sự cố vừa xử lý xong",
  SAFETY_CONFIRMATION: "Hỏi rõ an toàn con người",
  SPREAD_CONFIRMATION: "Hỏi rõ mức lan thiệt hại",
  ESSENTIAL_FUNCTION_CONFIRMATION: "Hỏi rõ chức năng thiết yếu",
  AFFECTED_SCOPE_CONFIRMATION: "Hỏi rõ phạm vi căn hộ",
  DETERIORATION_CONFIRMATION: "Hỏi rõ tốc độ xấu đi",
};
