"use client";

import Link from "next/link";
import { ArrowDown, ArrowLeft, CheckCircle2, ClipboardCheck, Copy, MessageSquareText, ShieldAlert, ShieldX, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { RiskCriteriaField, criteriaComplete } from "@/components/manager/RiskCriteriaField";
import { RiskBreakdown } from "@/components/manager/RiskBreakdown";
import { formatCategoryName } from "@/lib/category";
import { QUESTION_KIND_LABELS } from "@/lib/questionKinds";
import { formatDateTime } from "@/lib/mockService";
import { EMERGENCY_CONFIRM_HINT, EMERGENCY_PENDING_LOCKED_HINT, managerControls } from "@/lib/emergencyReview";
import { DOWNGRADE_PRIORITIES, PRIORITY_LABELS, formatBlocker } from "@/lib/risk";
import type { BlockerCode, CoordinatorAgentQuestionSummary, CoordinatorCategory, CoordinatorDuplicateCandidate, CoordinatorTicket, EmergencyDecision, RiskCriteriaInput, TicketPriority } from "@/types/api";

export type ManualResolutionSource = "IMAGE" | "TEXT" | "OTHER";

type Props = {
  ticket: CoordinatorTicket;
  categories: CoordinatorCategory[];
  busy: boolean;
  /** `criteria` is only supplied when the report has no assessment yet — a stored one is kept. */
  onResolve: (categoryId: string, source: ManualResolutionSource, reason: string, criteria: RiskCriteriaInput | null, blockers: BlockerCode[]) => void;
  onReject: (reason: string) => void;
  /** Present only for a report the agent flagged as an uncertain duplicate. */
  onDuplicateDecision?: (isDuplicate: boolean, reason: string, masterTicketId: string | null) => void;
  onEmergencyDecision?: (decision: EmergencyDecision, priority: TicketPriority | null, reason: string) => void;
};


export function ManagerManualReview({ ticket, categories, busy, onResolve, onReject, onDuplicateDecision, onEmergencyDecision }: Props) {
  const analysis = ticket.latest_analysis;
  // The emergency gate takes precedence over everything else on this page: a
  // P3 report has not reached duplicate handling and will not until this is
  // answered, so the duplicate panel cannot be open at the same time.
  const { emergencyPending: awaitingEmergency, canDecideDuplicate } = managerControls(ticket);
  // Only while the decision is genuinely still open. Once it is taken the
  // backend refuses a second one, so leaving the buttons up would offer an
  // action that can only fail.
  const uncertainDuplicate = canDecideDuplicate;
  return <div className="managerPageStack managerManualReviewPage">
    <div className="managerDetailContext"><Link href="/manager?view=manual-review"><ArrowLeft size={15} />Hàng chờ duyệt</Link><span className={`badge ${awaitingEmergency ? "danger managerUrgentBadge" : "warning"}`}>{awaitingEmergency ? "P5 khẩn cấp" : "Chờ điều phối viên duyệt"}</span></div>
    <section className={`managerManualReviewNotice${awaitingEmergency ? " urgent" : ""}`}><span>{awaitingEmergency ? <ShieldAlert size={21} /> : <ClipboardCheck size={21} />}</span><div><strong>{awaitingEmergency ? "Cần xác nhận khẩn cấp" : "Cần xác nhận thủ công"}</strong><p>{manualReviewReason(ticket)}</p></div></section>

    {awaitingEmergency && onEmergencyDecision && <ManagerSurface className="managerManualEmergency" title="Xử lý khẩn cấp P5" icon={<ShieldAlert size={19} />}><EmergencyReview ticket={ticket} busy={busy} onDecide={onEmergencyDecision} /></ManagerSurface>}

    <ManagerSurface className="managerManualAiSummary" title="Kết luận của AI">
      <div className="managerManualMetaRow">
        <Meta label="Danh mục cuối cùng" value={categoryLabel(analysis?.final_category_id, categories)} />
        <Meta label="Sự kiện khẩn cấp" value={(analysis?.risk_assessment?.blocker_codes || []).map(formatBlocker).join(", ") || "Không có"} />
      </div>
      <p className="managerManualAiReason">{analysis?.ai_reason?.trim() || "AI không ghi lại lý do phân loại."}</p>
      {/* The whole breakdown, not a summary of it. A coordinator disagreeing
          with a priority needs the row to disagree with. */}
      {analysis?.risk_assessment && <RiskBreakdown assessment={analysis.risk_assessment} />}
    </ManagerSurface>

    {uncertainDuplicate && onDuplicateDecision && <ManagerSurface className="managerManualDuplicate" title="Phản ánh có thể trùng"><DuplicateReview ticket={ticket} busy={busy} onDecide={onDuplicateDecision} /></ManagerSurface>}

    <ManagerSurface className="managerManualQuestions" title="Câu hỏi Agent đã hỏi cư dân"><AgentQuestionHistory ticket={ticket} /></ManagerSurface>
    {/* An emergency waiting on this page is not also waiting on a
        classification override; offering both would be offering two ways to
        answer the same question. */}
    {!awaitingEmergency && <ManagerSurface className="managerManualDecisionSurface" title="Hướng xử lý"><ManualReviewDecision ticket={ticket} categories={categories} busy={busy} onResolve={onResolve} onReject={onReject} /></ManagerSurface>}
  </div>;
}

/** The emergency gate: confirm P5, or downgrade it with a reason.
 *
 *  Exactly two actions, because there are exactly two answers. Confirming does
 *  not hand the work to anybody -- Building Management deals with a P5
 *  themselves -- so it is stated on the row rather than left to be discovered.
 *  Downgrading is the only route back into the normal pipeline, and it is the
 *  one that asks for a reason: keeping a priority the rubric already reached
 *  needs no argument, overriding it does.
 *
 *  Confirm sits open on the row; downgrade is folded into a `details` because
 *  the common answer to an emergency is "yes, it is one". */
function EmergencyReview({ ticket, busy, onDecide }: { ticket: CoordinatorTicket; busy: boolean; onDecide: (decision: EmergencyDecision, priority: TicketPriority | null, reason: string) => void }) {
  const analysis = ticket.latest_analysis;
  const [priority, setPriority] = useState<TicketPriority>("P4");
  const [reason, setReason] = useState("");
  const canDowngrade = reason.trim().length > 0 && !busy;

  return <div className="managerEmergencyReview">
    <div className="managerEmergencyDecisionRow">
      <span><ShieldAlert size={22} /></span>
      <div>
        <strong>Xác nhận khẩn cấp P5</strong>
        <p>{EMERGENCY_CONFIRM_HINT}</p>
        <div><b>{analysis?.ai_priority_before_review || "P5"}</b><small>{blockerSummary(analysis)}</small><small>{ticket.location_label || "Chưa rõ vị trí"}</small></div>
      </div>
      {/* The confirm sends no reason. `reason` below belongs to the downgrade,
          and attaching it here would file a coordinator's argument for lowering
          the priority as their justification for keeping it. */}
      <button type="button" className="button danger managerEmergencyPrimary" disabled={busy} onClick={() => onDecide("CONFIRM_P5", null, "")}>
        <ShieldAlert size={15} />{busy ? "Đang xác nhận..." : "Xác nhận"}
      </button>
    </div>

    <details className="managerEmergencyDowngrade">
      <summary>Hạ mức nếu chưa đủ khẩn cấp</summary>
      <div className="managerEmergencyDowngradeFields">
      <label>
        Hạ mức xuống
        <select value={priority} onChange={(event) => setPriority(event.target.value as TicketPriority)} disabled={busy}>
          {DOWNGRADE_PRIORITIES.map((band) => <option value={band} key={band}>{PRIORITY_LABELS[band]}</option>)}
        </select>
      </label>
      <textarea
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="Lý do hạ mức (bắt buộc)"
        rows={2}
        disabled={busy}
      />
      <button type="button" className="button secondary" disabled={!canDowngrade} onClick={() => onDecide("DOWNGRADE_PRIORITY", priority, reason.trim())}>
        <ArrowDown size={15} />Hạ mức và xử lý tiếp
      </button>
      <small className="managerEmergencyNote">{EMERGENCY_PENDING_LOCKED_HINT}</small>
      </div>
    </details>
  </div>;
}

/** Confirm or reject the duplicate the agent was not sure about.
 *
 *  Every candidate shown here is the snapshot the agent actually judged, so the
 *  reason above and the list below always describe the same evidence. Choosing
 *  "not a duplicate" publishes the report and lets the backend look for a
 *  spreading case; choosing "duplicate" links it and stops there. */
function DuplicateReview({ ticket, busy, onDecide }: { ticket: CoordinatorTicket; busy: boolean; onDecide: (isDuplicate: boolean, reason: string, masterTicketId: string | null) => void }) {
  const analysis = ticket.latest_analysis;
  const candidates = analysis?.duplicate_candidates || [];
  const [masterId, setMasterId] = useState(candidates.length === 1 ? candidates[0].ticket_id : "");
  const [reason, setReason] = useState("");
  const canConfirm = !busy && Boolean(masterId);

  return <div className="managerManualDuplicateBody">
    <p className="managerManualAiReason"><Sparkles size={15} aria-hidden="true" />{analysis?.duplicate_reason?.trim() || "AI không nêu lý do cho kết luận chưa chắc chắn."}</p>
    {candidates.length === 0 ? (
      <div className="managerManualEmpty"><Copy size={18} /><span>Không còn ứng viên nào được lưu lại.</span></div>
    ) : (
      <div className="tableWrap"><table className="dataTable"><thead><tr><th></th><th>Mã</th><th>Danh mục</th><th>Vị trí</th><th>Trạng thái</th><th>Thời gian</th><th>Hiện tượng</th></tr></thead><tbody>
        {candidates.map((candidate) => <tr key={candidate.ticket_id}>
          <td><input type="radio" name="duplicate-master" checked={masterId === candidate.ticket_id} onChange={() => setMasterId(candidate.ticket_id)} aria-label={`Chọn ${candidate.display_code}`} /></td>
          <td>{candidate.display_code}</td>
          <td>{candidate.category_name || "Chưa xác định"}</td>
          <td>{[candidate.location_label, candidate.floor_label].filter(Boolean).join(" · ") || "Chưa xác định"}</td>
          <td>{candidate.status}{candidate.recently_completed && <span className="badge warning">Vừa xong</span>}</td>
          <td>{candidateTime(candidate)}</td>
          <td>{candidate.summary || "—"}</td>
        </tr>)}
      </tbody></table></div>
    )}
    <div className="field"><label htmlFor="duplicate-reason">Ghi chú quyết định</label><textarea id="duplicate-reason" value={reason} placeholder="Vì sao bạn kết luận như vậy..." onChange={(event) => setReason(event.target.value)} /></div>
    <div className="managerManualDuplicateActions">
      <button className="button" type="button" disabled={!canConfirm} onClick={() => onDecide(true, reason.trim(), masterId)}><Copy size={16} />Xác nhận là trùng</button>
      <button className="button secondary" type="button" disabled={busy} onClick={() => onDecide(false, reason.trim(), null)}><CheckCircle2 size={16} />Xác nhận không trùng</button>
    </div>
  </div>;
}

function AgentQuestionHistory({ ticket }: { ticket: CoordinatorTicket }) {
  if (!ticket.agent_questions.length) return <div className="managerManualEmpty"><MessageSquareText size={18} /><span>Agent chưa hỏi thêm cư dân.</span></div>;
  return <div className="tableWrap"><table className="dataTable managerManualQuestionTable"><thead><tr><th>Lượt</th><th>Loại</th><th>Câu hỏi</th><th>Lựa chọn đưa ra</th><th>Trả lời của cư dân</th><th>Thời điểm</th></tr></thead><tbody>{ticket.agent_questions.map((question) => <tr key={question.id}>
    <td>{question.round_number}</td>
    <td>{question.question_kind ? QUESTION_KIND_LABELS[question.question_kind] || question.question_kind : "—"}</td>
    <td>{question.question_text}</td>
    <td>{(question.options || []).join(" / ") || "—"}</td>
    <td>{agentAnswerLabel(question)}</td>
    <td>{question.answered_at ? formatDateTime(question.answered_at) : formatDateTime(question.asked_at)}</td>
  </tr>)}</tbody></table></div>;
}

function ManualReviewDecision({ ticket, categories, busy, onResolve, onReject }: Pick<Props, "ticket" | "categories" | "busy" | "onResolve" | "onReject">) {
  const analysis = ticket.latest_analysis;
  const imageCategories = useMemo(() => resolveCategory(analysis?.image_category_id, categories), [analysis?.image_category_id, categories]);
  const textCategories = useMemo(() => resolveCategory(analysis?.text_category_id, categories), [analysis?.text_category_id, categories]);
  const [decision, setDecision] = useState<"valid" | "invalid">("valid");
  const [source, setSource] = useState<ManualResolutionSource>("OTHER");
  const [categoryId, setCategoryId] = useState("");
  const [reason, setReason] = useState("BQL xác nhận kết quả phân loại.");
  const [rejectReason, setRejectReason] = useState("");
  // The analysis left this report unscored, so the backend cannot derive a
  // priority until the Coordinator scores it. There is no default anywhere: an
  // unanswered control keeps the confirm button disabled rather than recording a
  // zero somebody did not choose.
  const assessment = ticket.risk_assessment;
  const scoreMissing = !assessment;
  const [criteria, setCriteria] = useState<Partial<RiskCriteriaInput>>({});
  const [blockers, setBlockers] = useState<BlockerCode[]>([]);

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
  const canResolve = !busy && decision === "valid" && Boolean(categoryId) && reason.trim().length >= 3 && (!scoreMissing || criteriaComplete(criteria));

  return <div className="managerManualDecisionGrid">
    <section className={`managerManualDecisionCard${decision === "valid" ? " active" : ""}`}>
      <label className="managerManualDecisionTitle"><input type="radio" name="manual-decision" checked={decision === "valid"} onChange={() => setDecision("valid")} /><span><strong>Xác nhận Category hợp lệ</strong><small>Chọn kết quả đúng để hệ thống tính lại điểm.</small></span></label>
      <div className="managerManualSourceOptions">
        <button type="button" className={source === "IMAGE" ? "active" : ""} disabled={!imageCategories.length} onClick={() => { setDecision("valid"); chooseSource("IMAGE"); }}>Theo ảnh</button>
        <button type="button" className={source === "TEXT" ? "active" : ""} disabled={!textCategories.length} onClick={() => { setDecision("valid"); chooseSource("TEXT"); }}>Theo văn bản</button>
        <button type="button" className={source === "OTHER" ? "active" : ""} onClick={() => { setDecision("valid"); chooseSource("OTHER"); }}>Danh mục khác</button>
      </div>
      <div className="field"><label htmlFor="manual-category">Danh mục xác nhận</label><select id="manual-category" value={categoryId} disabled={decision !== "valid"} onChange={(event) => setCategoryId(event.target.value)}>{sourceOptions.map((category) => <option value={category.id} key={category.id}>{formatCategoryName(category.code, category.display_name)}</option>)}</select></div>
      <RiskCriteriaField missing={scoreMissing} stored={assessment} value={criteria} blockers={blockers} disabled={decision !== "valid"} onChange={setCriteria} onBlockersChange={setBlockers} />
      <div className="field"><label htmlFor="manual-reason">Ghi chú xác nhận</label><textarea id="manual-reason" value={reason} disabled={decision !== "valid"} onChange={(event) => setReason(event.target.value)} /></div>
      <button className="button" disabled={!canResolve} onClick={() => onResolve(categoryId, source, reason.trim(), scoreMissing && criteriaComplete(criteria) ? criteria : null, blockers)}><CheckCircle2 size={16} />Xác nhận và tính lại điểm</button>
      <small className="managerManualStepHint">Bước này chỉ chốt phân loại. Phản ánh vẫn cần thao tác “Duyệt phản ánh” riêng sau đó.</small>
    </section>
    <section className={`managerManualDecisionCard invalid${decision === "invalid" ? " active" : ""}`}>
      <label className="managerManualDecisionTitle"><input type="radio" name="manual-decision" checked={decision === "invalid"} onChange={() => setDecision("invalid")} /><span><strong>Xác nhận ticket không hợp lệ</strong><small>Ticket sẽ bị loại và cư dân nhận được lý do.</small></span></label>
      <div className="field"><label>Lý do gửi cho cư dân *</label><textarea placeholder="Nhập lý do rõ ràng..." value={rejectReason} disabled={decision !== "invalid"} onFocus={() => setDecision("invalid")} onChange={(event) => setRejectReason(event.target.value)} /></div>
      <button className="button danger" disabled={busy || decision !== "invalid" || rejectReason.trim().length < 3} onClick={() => onReject(rejectReason.trim())}><ShieldX size={16} />Loại bỏ ticket</button>
    </section>
  </div>;
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div className="metaItem"><span>{label}</span><strong>{value}</strong></div>;
}

function manualReviewReason(ticket: CoordinatorTicket) {
  const analysis = ticket.latest_analysis;
  if (!analysis) return "Agent chưa có đủ dữ liệu để xác định một danh mục đáng tin cậy.";
  // A technical error is not a verdict about the report: it says the analysis
  // never finished, which is a different thing for a coordinator to act on.
  if (analysis.error_code) return `Phân tích dừng vì lỗi kỹ thuật (${analysis.error_code}). Có thể chạy lại phân tích.`;
  if (analysis.exit_reason === "DUPLICATE_UNCERTAIN") return "AI tìm thấy phản ánh tương tự nhưng chưa đủ chắc chắn để kết luận trùng.";
  if (analysis.exit_reason === "LIMIT_REACHED") return "Agent đã dùng hết lượt hỏi nhưng vẫn chưa đủ căn cứ để kết luận.";
  return "Agent chưa thể xác định duy nhất một danh mục và cần điều phối viên xác nhận.";
}

function resolveCategory(value: string | null | undefined, categories: CoordinatorCategory[]) {
  const match = value ? categories.find((category) => category.id === value || category.code === value) : undefined;
  return match ? [match] : [];
}

/** The named emergency facts behind this priority, or the fact that there are
 *  none. An empty list is worth saying out loud: it means the score alone
 *  reached P5, which is a different thing to argue with than a blocker. */
function blockerSummary(analysis: CoordinatorTicket["latest_analysis"]) {
  const codes = analysis?.risk_assessment?.blocker_codes || [];
  return codes.length > 0 ? codes.map(formatBlocker).join(", ") : "Không có sự kiện khẩn cấp";
}

function categoryLabel(value: string | null | undefined, categories: CoordinatorCategory[]) {
  const [match] = resolveCategory(value, categories);
  if (match) return formatCategoryName(match.code, match.display_name);
  return value ? formatCategoryName(value) : "Chưa xác định";
}

function candidateTime(candidate: CoordinatorDuplicateCandidate) {
  if (candidate.completed_at) return `Xong ${formatDateTime(candidate.completed_at)}`;
  return candidate.created_at ? formatDateTime(candidate.created_at) : "—";
}

function agentAnswerLabel(question: CoordinatorAgentQuestionSummary) {
  const chosenLocation = question.answer_payload?.selected_location_label;
  if (typeof chosenLocation === "string" && chosenLocation) return `${question.answer_text || "Chọn vị trí khác"} → ${chosenLocation}`;
  if (question.answer_text) return question.answer_text;
  if (question.answer_upload_id) return "Đã gửi ảnh bổ sung";
  if (["EXPIRED", "CANCELLED"].includes(question.status)) return "Không có câu trả lời";
  return "Chưa trả lời";
}
