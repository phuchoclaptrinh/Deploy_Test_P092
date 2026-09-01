import type { CoordinatorAnalysisSummary, CoordinatorTicket } from "@/types/api";

/** One sentence on why a ticket is sitting in manual review, for the notice banner.
 *
 *  Shared by the full manual-review page and the coordinator detail panel so the
 *  two surfaces never drift. A technical error is called out as such: it says the
 *  analysis never finished, which a coordinator acts on differently from a
 *  verdict about the report itself.
 */
export function manualReviewReason(ticket: CoordinatorTicket): string {
  const analysis = ticket.latest_analysis;
  if (!analysis) return "Agent chưa có đủ dữ liệu để xác định một danh mục đáng tin cậy.";
  if (analysis.error_code) return `Phân tích dừng vì lỗi kỹ thuật (${analysis.error_code}). Có thể chạy lại phân tích.`;
  if (analysis.exit_reason === "DUPLICATE_UNCERTAIN") return "AI tìm thấy phản ánh tương tự nhưng chưa đủ chắc chắn để kết luận trùng.";
  if (analysis.exit_reason === "LIMIT_REACHED") return "Agent đã dùng hết lượt hỏi nhưng vẫn chưa đủ căn cứ để kết luận.";
  return "Agent chưa thể xác định duy nhất một danh mục và cần điều phối viên xác nhận.";
}

export type AnalysisNoteBlock = { key: string; label: string; text: string };

/** The AI's own explanation of where this ticket ended up, split by the question
 *  each part answers.
 *
 *  `ai_reason` says why it was classified this way; on a DUPLICATE_UNCERTAIN exit
 *  `duplicate_reason` says why the duplicate verdict was left open -- the panel
 *  used to drop this entirely, so a coordinator opening an uncertain-duplicate
 *  ticket saw only the classification note and no word on the duplicate. A
 *  LIMIT_REACHED exit gets a fixed line because the run stops without prose.
 *
 *  Returns nothing when the run failed technically: `error_code` drives a
 *  separate warning box and the raw exception text must never render as a note.
 */
export function analysisNoteBlocks(
  analysis: CoordinatorAnalysisSummary | null | undefined,
): AnalysisNoteBlock[] {
  if (!analysis || analysis.error_code?.trim()) return [];
  const blocks: AnalysisNoteBlock[] = [];

  const classification = analysis.ai_reason?.trim();
  if (classification) {
    blocks.push({ key: "classification", label: "Vì sao phân loại như vậy", text: classification });
  }

  if (analysis.exit_reason === "DUPLICATE_UNCERTAIN") {
    blocks.push({
      key: "duplicate",
      label: "Vì sao chưa chắc là trùng lặp",
      text:
        analysis.duplicate_reason?.trim()
        || "AI tìm thấy phản ánh tương tự nhưng không ghi lại lý do cụ thể cho kết luận chưa chắc chắn.",
    });
  } else if (analysis.exit_reason === "LIMIT_REACHED") {
    blocks.push({
      key: "limit",
      label: "Vì sao dừng phân tích",
      text: "Agent đã dùng hết lượt hỏi cư dân nhưng vẫn chưa đủ căn cứ để kết luận. Cần điều phối viên xác nhận thủ công.",
    });
  }

  return blocks;
}

export type DuplicateCandidateHint = {
  code: string;
  detail: string;
  status: string;
  recentlyCompleted: boolean;
};

/** The candidate tickets the agent weighed on a DUPLICATE_UNCERTAIN exit.
 *
 *  There is deliberately no single master ticket -- "uncertain" means the agent
 *  could not commit to one -- so the panel names the whole shortlist the
 *  coordinator has to choose between, rather than leaving the reason text
 *  referring to candidates it never lists. Empty for every other exit, and empty
 *  once the snapshot has been cleared.
 */
export function duplicateCandidateHints(
  analysis: CoordinatorAnalysisSummary | null | undefined,
): DuplicateCandidateHint[] {
  if (!analysis || analysis.exit_reason !== "DUPLICATE_UNCERTAIN") return [];
  return (analysis.duplicate_candidates || []).map((candidate) => ({
    code: candidate.display_code,
    detail:
      candidate.summary?.trim()
      || [candidate.category_name, candidate.location_label].filter(Boolean).join(" · ")
      || "Không có mô tả",
    status: candidate.status,
    recentlyCompleted: candidate.recently_completed,
  }));
}
