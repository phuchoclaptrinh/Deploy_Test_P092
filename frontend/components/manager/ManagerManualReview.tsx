"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, ClipboardCheck, FileImage, FileText, MessageSquareText, ShieldX } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { IncidentGallery } from "@/components/IncidentImage";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { SeverityField } from "@/components/manager/SeverityField";
import { formatCategoryName } from "@/lib/category";
import { formatSeverity, type TicketSeverity } from "@/lib/severity";
import type { TicketImage } from "@/lib/types";
import type { CoordinatorCategory, CoordinatorTicket } from "@/types/api";

export type ManualResolutionSource = "IMAGE" | "TEXT" | "OTHER";

type Props = {
  ticket: CoordinatorTicket;
  images: TicketImage[];
  categories: CoordinatorCategory[];
  busy: boolean;
  /** `severity` is only supplied when the report has none yet — §8.3 keeps a stored one. */
  onResolve: (categoryId: string, source: ManualResolutionSource, reason: string, severity: TicketSeverity | null) => void;
  onReject: (reason: string) => void;
};

export function ManagerManualReview({ ticket, images, categories, busy, onResolve, onReject }: Props) {
  const analysis = ticket.latest_analysis;
  return <div className="managerPageStack managerManualReviewPage">
    <div className="managerDetailContext"><Link href="/manager?view=manual-review"><ArrowLeft size={15} />Hàng chờ duyệt</Link><span className="badge warning">Chờ điều phối viên duyệt</span></div>
    <section className="managerManualReviewNotice"><span><ClipboardCheck size={21} /></span><div><strong>Cần xác nhận thủ công</strong><p>{manualReviewReason(ticket)}</p></div></section>
    <div className="managerManualEvidenceGrid">
      <article className="managerManualEvidenceCard"><header><FileImage size={18} /><div><small>TỪ ẢNH</small><strong>{formatAnalysisCategories(analysis?.image_categories, categories)}</strong></div></header><IncidentGallery images={images} alt="Ảnh cư dân gửi" className="managerManualGallery" /><EvidenceMeta severity={analysis?.severity} redFlag={Boolean(analysis?.red_flag_signal)} /></article>
      <span className="managerManualVersus">VS</span>
      <article className="managerManualEvidenceCard"><header><FileText size={18} /><div><small>TỪ VĂN BẢN</small><strong>{formatAnalysisCategories(analysis?.text_categories, categories)}</strong></div></header><div className="managerManualDescription">{ticket.description || "Cư dân không nhập mô tả."}</div><EvidenceMeta severity={analysis?.severity} redFlag={Boolean(analysis?.red_flag_text)} /></article>
    </div>
    <ManagerSurface className="managerManualQuestions" title="Câu hỏi Agent đã hỏi cư dân"><AgentQuestionHistory ticket={ticket} /></ManagerSurface>
    <ManagerSurface className="managerManualDecisionSurface" title="Hướng xử lý"><ManualReviewDecision ticket={ticket} categories={categories} busy={busy} onResolve={onResolve} onReject={onReject} /></ManagerSurface>
  </div>;
}

function AgentQuestionHistory({ ticket }: { ticket: CoordinatorTicket }) {
  if (!ticket.agent_questions.length) return <div className="managerManualEmpty"><MessageSquareText size={18} /><span>Agent chưa hỏi thêm cư dân.</span></div>;
  return <div className="tableWrap"><table className="dataTable managerManualQuestionTable"><thead><tr><th>Lượt</th><th>Câu hỏi</th><th>Trả lời của cư dân</th></tr></thead><tbody>{ticket.agent_questions.map((question) => <tr key={question.id}><td>{question.round_number}</td><td>{question.question_text}</td><td>{agentAnswerLabel(question.answer_text, question.answer_upload_id, question.status)}</td></tr>)}</tbody></table></div>;
}

function ManualReviewDecision({ ticket, categories, busy, onResolve, onReject }: Omit<Props, "images">) {
  const imageCategories = useMemo(() => resolveAnalysisCategories(ticket.latest_analysis?.image_categories, categories), [categories, ticket.latest_analysis?.image_categories]);
  const textCategories = useMemo(() => resolveAnalysisCategories(ticket.latest_analysis?.text_categories, categories), [categories, ticket.latest_analysis?.text_categories]);
  const [decision, setDecision] = useState<"valid" | "invalid">("valid");
  const [source, setSource] = useState<ManualResolutionSource>("OTHER");
  const [categoryId, setCategoryId] = useState("");
  const [reason, setReason] = useState("BQL xác nhận kết quả phân loại.");
  const [rejectReason, setRejectReason] = useState("");
  // The analysis left this report without a severity, so the backend cannot
  // score it until the Coordinator names one. There is no default: an unanswered
  // control keeps the confirm button disabled.
  const severityMissing = !ticket.severity;
  const [severity, setSeverity] = useState<TicketSeverity | "">("");

  useEffect(() => {
    if (categoryId) return;
    if (imageCategories[0]) { setSource("IMAGE"); setCategoryId(imageCategories[0].id); return; }
    if (textCategories[0]) { setSource("TEXT"); setCategoryId(textCategories[0].id); return; }
    if (categories[0]) setCategoryId(categories[0].id);
  }, [categories, categoryId, imageCategories, textCategories]);

  const chooseSource = (nextSource: ManualResolutionSource) => {
    const options = nextSource === "IMAGE" ? imageCategories : nextSource === "TEXT" ? textCategories : categories;
    setSource(nextSource);
    setCategoryId(options[0]?.id || "");
  };
  const sourceOptions = source === "IMAGE" ? imageCategories : source === "TEXT" ? textCategories : categories;
  const canResolve = !busy && decision === "valid" && Boolean(categoryId) && reason.trim().length >= 3 && (!severityMissing || severity !== "");

  return <div className="managerManualDecisionGrid">
    <section className={`managerManualDecisionCard${decision === "valid" ? " active" : ""}`}>
      <label className="managerManualDecisionTitle"><input type="radio" name="manual-decision" checked={decision === "valid"} onChange={() => setDecision("valid")} /><span><strong>Xác nhận Category hợp lệ</strong><small>Chọn kết quả đúng để hệ thống tính lại điểm.</small></span></label>
      <div className="managerManualSourceOptions">
        <button type="button" className={source === "IMAGE" ? "active" : ""} disabled={!imageCategories.length} onClick={() => { setDecision("valid"); chooseSource("IMAGE"); }}>Theo ảnh</button>
        <button type="button" className={source === "TEXT" ? "active" : ""} disabled={!textCategories.length} onClick={() => { setDecision("valid"); chooseSource("TEXT"); }}>Theo văn bản</button>
        <button type="button" className={source === "OTHER" ? "active" : ""} onClick={() => { setDecision("valid"); chooseSource("OTHER"); }}>Danh mục khác</button>
      </div>
      <div className="field"><label htmlFor="manual-category">Danh mục xác nhận</label><select id="manual-category" value={categoryId} disabled={decision !== "valid"} onChange={(event) => setCategoryId(event.target.value)}>{sourceOptions.map((category) => <option value={category.id} key={category.id}>{formatCategoryName(category.code, category.display_name)}</option>)}</select></div>
      <SeverityField id="manual-severity" missing={severityMissing} stored={ticket.severity} value={severity} disabled={decision !== "valid"} onChange={setSeverity} />
      <div className="field"><label htmlFor="manual-reason">Ghi chú xác nhận</label><textarea id="manual-reason" value={reason} disabled={decision !== "valid"} onChange={(event) => setReason(event.target.value)} /></div>
      <button className="button" disabled={!canResolve} onClick={() => onResolve(categoryId, source, reason.trim(), severityMissing ? severity || null : null)}><CheckCircle2 size={16} />Xác nhận và tính lại điểm</button>
      <small className="managerManualStepHint">Bước này chỉ chốt phân loại. Phản ánh vẫn cần thao tác “Duyệt phản ánh” riêng sau đó.</small>
    </section>
    <section className={`managerManualDecisionCard invalid${decision === "invalid" ? " active" : ""}`}>
      <label className="managerManualDecisionTitle"><input type="radio" name="manual-decision" checked={decision === "invalid"} onChange={() => setDecision("invalid")} /><span><strong>Xác nhận ticket không hợp lệ</strong><small>Ticket sẽ bị loại và cư dân nhận được lý do.</small></span></label>
      <div className="field"><label>Lý do gửi cho cư dân *</label><textarea placeholder="Nhập lý do rõ ràng..." value={rejectReason} disabled={decision !== "invalid"} onFocus={() => setDecision("invalid")} onChange={(event) => setRejectReason(event.target.value)} /></div>
      <button className="button danger" disabled={busy || decision !== "invalid" || rejectReason.trim().length < 3} onClick={() => onReject(rejectReason.trim())}><ShieldX size={16} />Loại bỏ ticket</button>
    </section>
  </div>;
}

function EvidenceMeta({ severity, redFlag }: { severity?: TicketSeverity | null; redFlag: boolean }) {
  return <footer><span>Mức độ: <strong>{formatSeverity(severity)}</strong></span><span>Nguy hiểm: <strong>{redFlag ? "Có" : "Không"}</strong></span></footer>;
}

function manualReviewReason(ticket: CoordinatorTicket) {
  const analysis = ticket.latest_analysis;
  if (!analysis) return "Agent chưa có đủ dữ liệu để xác định một danh mục đáng tin cậy.";
  if (analysis.error_code) return "Quá trình phân tích gặp lỗi và cần điều phối viên xác nhận kết quả.";
  const image = new Set(analysis.image_categories || []);
  const text = new Set(analysis.text_categories);
  const mismatch = image.size > 0 && (image.size !== text.size || [...text].some((value) => !image.has(value)));
  if (mismatch) return "Kết quả phân loại từ ảnh và mô tả của cư dân chưa thống nhất.";
  if (analysis.exit_reason === "LIMIT_REACHED") return "Agent đã sử dụng hết lượt phân tích nhưng chưa đủ tự tin để kết luận.";
  if (analysis.is_confident === false) return "Agent chưa đủ độ tin cậy để tự động xác nhận danh mục.";
  return "Agent chưa thể xác định duy nhất một danh mục và cần điều phối viên xác nhận.";
}

function resolveAnalysisCategories(values: string[] | null | undefined, categories: CoordinatorCategory[]) {
  const rows = (values || []).map((value) => categories.find((category) => category.id === value || category.code === value)).filter((category): category is CoordinatorCategory => Boolean(category));
  return rows.filter((category, index) => rows.findIndex((row) => row.id === category.id) === index);
}

function formatAnalysisCategories(values: string[] | null | undefined, categories: CoordinatorCategory[]) {
  const rows = resolveAnalysisCategories(values, categories);
  if (rows.length) return rows.map((row) => formatCategoryName(row.code, row.display_name)).join(", ");
  if (values?.length) return values.map((value) => formatCategoryName(value)).join(", ");
  return "Chưa xác định";
}

function agentAnswerLabel(answerText: string | null, answerUploadId: string | null, status: string) {
  if (answerText) return answerText;
  if (answerUploadId) return "Đã gửi ảnh bổ sung";
  if (["EXPIRED", "CANCELLED"].includes(status)) return "Không có câu trả lời";
  return "Chưa trả lời";
}
