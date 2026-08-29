"use client";

import Link from "next/link";
import { Bot, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Maximize2, ShieldAlert, ShieldX, SlidersHorizontal, UserPlus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { approveCoordinatorTicket, assignCoordinatorTicket, getCoordinatorTicket, listBackendCategories, listCoordinatorTechnicians, loadAttachmentImages, overrideCoordinatorClassification, rejectCoordinatorManualReview, resolveCoordinatorManualReview } from "@/api/backend.api";
import { SeverityField } from "@/components/manager/SeverityField";
import { PriorityBadge } from "@/components/StatusBadge";
import { formatCategoryName } from "@/lib/category";
import { formatTicketCode } from "@/lib/display";
import { assignmentStatusDisplay } from "@/lib/assignmentStatus";
import { formatDateTime } from "@/lib/managerTicket";
import { P3_PENDING_LABEL, P3_PENDING_LOCKED_HINT, P3_PENDING_NOTICE, managerControls } from "@/lib/p3Review";
import { formatSeverity, type TicketSeverity } from "@/lib/severity";
import type { TicketImage } from "@/lib/types";
import type { CoordinatorCategory, CoordinatorTicket, TechnicianSummary } from "@/types/api";

type Mode = "none" | "approve" | "override" | "assign" | "manual";
type PanelImage = TicketImage & { source: "resident" | "technician" };
type PanelTimelineItem = { label: string; createdAt: string; detail?: string };

const statusLabels: Record<string, string> = { NEW: "Mới", WAITING_RESIDENT_INFO: "Chờ cư dân bổ sung", APPROVED: "Đã duyệt", IN_PROGRESS: "Đang xử lý", COMPLETED: "Hoàn thành", UNRESOLVABLE: "Không xử lý được", CANCELLED: "Đã hủy", INVALID: "Không hợp lệ", LINKED_DUPLICATE: "Đã gộp trùng" };

type Props = {
  ticketId: string;
  /** Retained for cluster callers; case membership is no longer rendered in the detail grid. */
  caseInfo?: string | null;
  onClose: () => void;
  onUpdated?: () => void;
};

export function TicketDetailPanel({ ticketId, onClose, onUpdated }: Props) {
  const [ticket, setTicket] = useState<CoordinatorTicket | null>(null);
  const [images, setImages] = useState<PanelImage[]>([]);
  const [categories, setCategories] = useState<CoordinatorCategory[]>([]);
  const [technicians, setTechnicians] = useState<TechnicianSummary[]>([]);
  const [mode, setMode] = useState<Mode>("none");
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [timelineOpen, setTimelineOpen] = useState(true);

  const load = useCallback(async () => {
    try {
      const next = await getCoordinatorTicket(ticketId);
      setTicket(next);
      setError("");
      void loadAttachmentImages(next.attachments || [], "manager")
        .then((loaded) => setImages(loaded.map((image, index) => ({
          ...image,
          source: next.attachments[index]?.attachment_type === "TECHNICIAN_COMPLETION" ? "technician" : "resident",
        }))))
        .catch(() => setImages([]));
      void Promise.all([listBackendCategories(), listCoordinatorTechnicians()])
        .then(([rows, roster]) => { setCategories(rows.filter((row) => row.is_active)); setTechnicians(roster.filter((row) => row.is_active)); })
        .catch(() => undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được ticket.");
    }
  }, [ticketId]);
  useEffect(() => { setTicket(null); setImages([]); void load(); }, [load]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (lightbox !== null) setLightbox(null);
      else if (mode !== "none") setMode("none");
      else onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [lightbox, mode, onClose]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      setMode("none");
      await load();
      onUpdated?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể cập nhật ticket.");
    } finally {
      setBusy(false);
    }
  };

  if (!ticket) return <aside className="ticketDetailPanel" aria-label="Chi tiết ticket"><PanelHeader title="Chi tiết ticket" onClose={onClose} /><div className="ticketPanelLoading">{error || "Đang tải ticket..."}</div></aside>;

  // A ticket held at the emergency gate is *also* in MANUAL_REVIEW, so the
  // generic label and the generic form would both be wrong for it. One shared
  // predicate decides that for every management surface.
  const { p3Pending, canApprove, canAssign, canOverride, canManualReview } = managerControls(ticket);
  const manualReview = !p3Pending && ticket.classification_status === "MANUAL_REVIEW";
  const reporter = ticket.reporter;
  const analysisNote = ticket.latest_analysis?.ai_reason?.trim();
  const displayStatus = panelDisplayStatus(ticket, p3Pending, manualReview);
  const timeline = latestTimeline(ticket);

  return <aside className="ticketDetailPanel" aria-label={`Chi tiết ${formatTicketCode(ticket.id)}`}>
    <PanelHeader title={formatTicketCode(ticket.id)} onClose={onClose} />
    <div className="ticketPanelScroll">
      <div className="ticketPanelStatus">
        <span className={`badge managerTableStatus ${displayStatus.tone}`}>{displayStatus.label}</span>
        {ticket.priority && <PriorityBadge priority={ticket.priority} />}
        {ticket.red_flag_detected && <span className="badge danger">Cờ đỏ</span>}
      </div>

      {/* No attachment means no image region at all - an empty frame says nothing. */}
      {images.length > 0 && <div className="ticketPanelImages">
        {images.map((image, index) => <button className="ticketImageThumb" type="button" key={`${image.name}-${index}`} onClick={() => setLightbox(index)} aria-label={`Xem ảnh ${index + 1}`}>
          <img src={image.dataUrl} alt={`Ảnh ${image.source === "technician" ? "KTV" : "cư dân"} gửi ${index + 1}`} />
          <em className={`ticketImageSource ${image.source}`}>{image.source === "technician" ? "Ảnh KTV sau xử lý" : "Ảnh cư dân gửi"}</em>
          <b><Maximize2 size={13} />Xem ảnh</b>
        </button>)}
      </div>}

      <Section label="Mô tả cư dân"><p className="ticketPanelDescription">{ticket.description || "Cư dân không nhập mô tả."}</p></Section>

      {ticket.completion_note && <section className="ticketPanelCompletionNote">
        <span>Ghi chú KTV sau xử lý{ticket.completed_technician_name ? ` · ${ticket.completed_technician_name}` : ""}</span>
        <p>{ticket.completion_note}</p>
      </section>}

      {analysisNote && <section className="ticketPanelAiNote">
        <header><Bot size={15} /><span>Ghi chú phân tích của AI</span></header>
        <p>{analysisNote}</p>
      </section>}

      <section className="ticketPanelInfoBox">
        <header><strong>Thông tin chi tiết</strong></header>
        <div className="ticketPanelMeta">
        <Meta label="Người gửi" value={reporter?.full_name || "Chưa có tên"} />
        <Meta label="Căn hộ" value={reporter?.unit_code || "Chưa xác định"} />
        <Meta label="Tầng" value={reporter?.floor_label || "Chưa xác định"} />
        <Meta label="Số điện thoại" value={reporter?.phone_e164 || "Chưa có"} />
        <Meta label="Vị trí sự cố" value={ticket.location_label || "Chưa xác định"} />
        <Meta label="Danh mục" value={formatCategoryName(ticket.category)} />
        <Meta label="Mức độ nghiêm trọng" value={formatSeverity(ticket.severity)} />
        <div><span>Mức ưu tiên</span>{ticket.priority ? <PriorityBadge priority={ticket.priority} /> : <strong>Chưa xác định</strong>}</div>
        <Meta label="Điểm" value={scoreValue(ticket)} />
        {/* §4: the planned window is the scheduler's, and the finish is
            internal capacity arithmetic shown to Building Management only --
            never to a resident. There is no acceptance deadline any more; the
            queue position, risk state and slack below are what say whether the
            plan is holding. */}
        <Meta label="Bắt đầu dự kiến" value={ticket.planned_start_at ? formatDateTime(ticket.planned_start_at) : "Chưa có lịch"} />
        <Meta label="Kết thúc dự kiến (nội bộ)" value={ticket.planned_finish_at ? formatDateTime(ticket.planned_finish_at) : "Chưa có lịch"} />
        <Meta label="Vị trí trong hàng việc" value={queuePositionLabel(ticket.planned_order)} />
        <Meta label="Tình trạng lịch" value={riskStateLabel(ticket)} />
        <Meta label="Thời gian dự phòng" value={slackLabel(ticket.slack_seconds)} />
        <Meta label="Kỹ thuật viên" value={panelTechnician(ticket)} />
        <Meta label="Thời gian gửi" value={formatDateTime(ticket.created_at)} />
        <Meta label="Số lần đổi KTV" value={String(ticket.reassignment_count ?? 0)} />
        </div>
      </section>

      {ticket.invalid_reason && <p className="ticketPanelWarning">Lý do không hợp lệ: {ticket.invalid_reason}</p>}

      {timeline.length > 0 && <section className="ticketPanelSection ticketPanelTimelineSection">
        <button type="button" className="ticketPanelSectionToggle" onClick={() => setTimelineOpen((value) => !value)} aria-expanded={timelineOpen}>
          <span>Diễn biến gần nhất</span><small>{timelineOpen ? "Ẩn" : "Hiện"}<ChevronDown size={15} className={timelineOpen ? "open" : ""} /></small>
        </button>
        {timelineOpen && <ul className="ticketPanelTimeline">{timeline.map((row, index) => <li key={`${row.createdAt}-${row.label}-${index}`}>
          <strong>{row.label}</strong>
          {row.detail && <span>{row.detail}</span>}
          <small>{formatDateTime(row.createdAt)}</small>
        </li>)}</ul>}
      </section>}

      {/* The full review controls -- confirm, or downgrade with a reason --
          live on the ticket page, so this sends the coordinator there rather
          than offering a second, smaller version of the same decision. */}
      {p3Pending && <section className="ticketPanelP3">
        <p><ShieldAlert size={15} />{P3_PENDING_NOTICE}</p>
        <small>{P3_PENDING_LOCKED_HINT}</small>
      </section>}

      {error && <div className="alert error">{error}</div>}
    </div>

    {p3Pending && <footer className="ticketPanelActions">
      <Link className="button" href={`/manager/tickets/${ticket.id}`}><ShieldAlert size={16} />Duyệt mức khẩn cấp P3</Link>
    </footer>}

    {!p3Pending && (canApprove || canAssign || canOverride || canManualReview) && <footer className="ticketPanelActions">
      {canManualReview && <button className="button" type="button" onClick={() => setMode("manual")}><CheckCircle2 size={16} />Duyệt phân loại thủ công</button>}
      {canApprove && <button className="button" type="button" onClick={() => setMode("approve")}><CheckCircle2 size={16} />Duyệt phản ánh</button>}
      {canOverride && <button className="button secondary" type="button" onClick={() => setMode("override")}><SlidersHorizontal size={16} />Chỉnh phân loại</button>}
      {canAssign && <button className="button" type="button" onClick={() => setMode("assign")}><UserPlus size={16} />Gán kỹ thuật viên</button>}
    </footer>}

    {mode !== "none" && createPortal(<ActionModal title={modalTitle(mode, ticket)} close={() => setMode("none")}>
      {mode === "approve" && <div className="modalForm">
        <p className="helper">Kiểm tra danh mục và mức ưu tiên trước khi chuyển sang bước phân công.</p>
        <div className="modalActions"><button className="button secondary" type="button" disabled={busy} onClick={() => setMode("none")}>Hủy</button><button className="button" type="button" disabled={busy} onClick={() => void run(() => approveCoordinatorTicket(ticket.id))}>Xác nhận duyệt</button></div>
      </div>}
      {mode === "assign" && <AssignForm technicians={technicians} busy={busy} error={error} cancel={() => setMode("none")} submit={(technicianId) => void run(() => assignCoordinatorTicket(ticket.id, technicianId))} />}
      {mode === "override" && <OverrideForm ticket={ticket} categories={categories} busy={busy} cancel={() => setMode("none")} submit={(categoryId, priority, reason) => void run(() => overrideCoordinatorClassification(ticket.id, categoryId, priority, reason))} />}
      {mode === "manual" && <ManualReviewForm ticket={ticket} categories={categories} busy={busy} cancel={() => setMode("none")} resolve={(categoryId, source, reason, severity) => void run(() => resolveCoordinatorManualReview(ticket.id, categoryId, source, reason, severity))} reject={(reason) => void run(() => rejectCoordinatorManualReview(ticket.id, reason))} />}
    </ActionModal>, document.body)}

    {lightbox !== null && images[lightbox] && createPortal(<div className="ticketLightbox" role="dialog" aria-modal="true" aria-label="Ảnh phản ánh">
      <button type="button" aria-label="Đóng ảnh" className="iconButton" onClick={() => setLightbox(null)}><X size={19} /></button>
      {images.length > 1 && <button type="button" className="ticketLightboxPrev" aria-label="Ảnh trước" onClick={() => setLightbox((lightbox + images.length - 1) % images.length)}><ChevronLeft /></button>}
      <img src={images[lightbox].dataUrl} alt={`Ảnh ${images[lightbox].source === "technician" ? "KTV" : "cư dân"} ${lightbox + 1}`} />
      {images.length > 1 && <button type="button" className="ticketLightboxNext" aria-label="Ảnh sau" onClick={() => setLightbox((lightbox + 1) % images.length)}><ChevronRight /></button>}
    </div>, document.body)}
  </aside>;
}

function modalTitle(mode: Mode, ticket: CoordinatorTicket) {
  const code = formatTicketCode(ticket.id);
  if (mode === "approve") return `Duyệt phản ánh · ${code}`;
  if (mode === "assign") return `Gán kỹ thuật viên · ${code}`;
  if (mode === "manual") return `Duyệt phân loại thủ công · ${code}`;
  return `Chỉnh phân loại · ${code}`;
}

function panelDisplayStatus(ticket: CoordinatorTicket, p3Pending: boolean, manualReview: boolean) {
  if (p3Pending) return { label: P3_PENDING_LABEL, tone: "danger" as const };
  if (manualReview) return { label: "Chờ duyệt thủ công", tone: "warning" as const };
  if (["PENDING", "PROCESSING"].includes(ticket.classification_status)) return { label: "Đang phân tích", tone: "processing" as const };
  const assignment = assignmentStatusDisplay(ticket.active_assignment_status);
  if (assignment) return assignment;
  if (["APPROVED", "COMPLETED"].includes(ticket.status)) return { label: statusLabels[ticket.status], tone: "success" as const };
  if (["UNRESOLVABLE", "INVALID"].includes(ticket.status)) return { label: statusLabels[ticket.status], tone: "danger" as const };
  if (ticket.status === "IN_PROGRESS") return { label: statusLabels[ticket.status], tone: "processing" as const };
  return { label: statusLabels[ticket.status] || "Chưa xác định", tone: "neutral" as const };
}

/** §4: the scheduler's own numbering, shown as the technician sees it. Nothing
 *  here is derived on the client -- `planned_order` is the backend's, and it is
 *  the same value that decides which job a technician may actually start. */
function queuePositionLabel(order: number | null | undefined) {
  if (order == null) return "Chưa xếp lịch";
  if (order === 0) return "Làm ngay";
  if (order === 1) return "Tiếp theo";
  return `Thứ ${order + 1}`;
}

function riskStateLabel(ticket: CoordinatorTicket) {
  if (!ticket.active_assignment_id) return "Chưa xếp lịch";
  if (ticket.assignment_risk_state === "AT_RISK" || (ticket.slack_seconds ?? 0) < 0) return "Trễ lịch (AT_RISK)";
  if (ticket.assignment_risk_state === "SAFE") return "Đúng lịch (SAFE)";
  return "Chưa xác định";
}

/** Working seconds of headroom against the committed finish. Negative is the
 *  whole signal, so the sign is spelled out rather than left to a minus. */
function slackLabel(seconds: number | null | undefined) {
  if (seconds == null) return "Chưa có";
  const minutes = Math.round(Math.abs(seconds) / 60);
  const amount = minutes >= 60 ? `${Math.floor(minutes / 60)} giờ ${minutes % 60} phút` : `${minutes} phút`;
  return seconds < 0 ? `Trễ ${amount}` : `Còn dư ${amount}`;
}

function panelTechnician(ticket: CoordinatorTicket) {
  if (ticket.completed_technician_name) return ticket.completed_technician_name;
  if (!ticket.active_assignment_id) return "Chưa gán";
  return ticket.active_technician_name || "Kỹ thuật viên";
}

function scoreValue(ticket: CoordinatorTicket) {
  if (ticket.score_total != null) return String(ticket.score_total);
  if (ticket.red_flag_detected) return "Cờ đỏ · không tính điểm";
  if (["PENDING", "PROCESSING", "MANUAL_REVIEW"].includes(ticket.classification_status)) return "Chờ tính điểm";
  return "Chưa có";
}

function latestTimeline(ticket: CoordinatorTicket) {
  const ticketEvents: PanelTimelineItem[] = (ticket.timeline || []).map((row) => ({
    label: row.to_status ? statusLabels[row.to_status] || "Đã cập nhật" : "Đã cập nhật",
    createdAt: row.created_at,
    detail: undefined,
  }));
  const assignment = assignmentStatusDisplay(ticket.active_assignment_status);
  if (ticket.active_assignment_id && assignment && ticket.active_assignment_updated_at) {
    ticketEvents.push({
      label: assignment.label,
      detail: `KTV: ${ticket.active_technician_name || "Chưa xác định"}`,
      createdAt: ticket.active_assignment_updated_at,
    });
  }
  return ticketEvents.sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt)).slice(-4);
}

function PanelHeader({ title, onClose }: { title: string; onClose: () => void }) {
  return <header className="ticketPanelHeader"><strong>{title}</strong><button className="iconButton" type="button" onClick={onClose} aria-label="Đóng chi tiết ticket"><X size={18} /></button></header>;
}
function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="ticketPanelSection"><span>{label}</span>{children}</section>;
}
function Meta({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}
function ActionModal({ title, close, children }: { title: string; close: () => void; children: React.ReactNode }) {
  return <div className="modalBackdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <section className="managerModal" role="dialog" aria-modal="true" aria-label={title}>
      <header><strong>{title}</strong><button className="iconButton" type="button" onClick={close} aria-label="Đóng"><X size={17} /></button></header>
      <div className="managerModalBody">{children}</div>
    </section>
  </div>;
}

function AssignForm({ technicians, busy, error, cancel, submit }: { technicians: TechnicianSummary[]; busy: boolean; error: string; cancel: () => void; submit: (id: string) => void }) {
  const [selected, setSelected] = useState(technicians.find((row) => row.is_available)?.user_id || "");
  return <div className="managerAssignForm">
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="managerTechOptions">
      {technicians.map((row) => <label className={`managerTechOption${row.is_available ? " match" : ""}`} key={row.user_id}>
        <input type="radio" name="panel-technician" checked={selected === row.user_id} disabled={!row.is_available} onChange={() => setSelected(row.user_id)} />
        <span><strong>{row.full_name || row.user_id.slice(0, 8)}</strong><small>{row.skill_category_ids.length} nhóm chuyên môn</small></span>
        <b className={row.is_available ? "available" : "busy"}>{row.is_available ? "Rảnh" : "Bận"}</b>
      </label>)}
      {!technicians.length && <div className="emptyState">Chưa có kỹ thuật viên khả dụng.</div>}
    </div>
    <div className="modalActions"><button className="button secondary" type="button" disabled={busy} onClick={cancel}>Hủy</button><button className="button" type="button" disabled={busy || !selected} onClick={() => submit(selected)}><UserPlus size={16} />Phân công</button></div>
  </div>;
}

function OverrideForm({ ticket, categories, busy, cancel, submit }: { ticket: CoordinatorTicket; categories: CoordinatorCategory[]; busy: boolean; cancel: () => void; submit: (category: string, priority: string, reason: string) => void }) {
  const [category, setCategory] = useState(ticket.category_id || categories[0]?.id || "");
  const [priority, setPriority] = useState<string>(ticket.priority || "P2");
  const [reason, setReason] = useState("");
  const changed = category !== ticket.category_id || priority !== ticket.priority;
  return <div className="modalForm">
    <div className="formGrid">
      <div className="field"><label>Danh mục mới</label><select value={category} onChange={(event) => setCategory(event.target.value)}>{categories.map((row) => <option value={row.id} key={row.id}>{formatCategoryName(row.code, row.display_name)}</option>)}</select></div>
      <div className="field"><label>Mức ưu tiên mới</label><select value={priority} onChange={(event) => setPriority(event.target.value)}><option>P3</option><option>P2</option><option>P1</option></select></div>
    </div>
    <div className="field"><label>Lý do điều chỉnh *</label><textarea placeholder="Nhập lý do để lưu vào lịch sử thay đổi..." value={reason} onChange={(event) => setReason(event.target.value)} /></div>
    <div className="modalActions"><button className="button secondary" type="button" disabled={busy} onClick={cancel}>Hủy</button><button className="button" type="button" disabled={busy || !category || !changed || reason.trim().length < 3} onClick={() => submit(category, priority, reason.trim())}>Lưu thay đổi</button></div>
  </div>;
}

function ManualReviewForm({ ticket, categories, busy, cancel, resolve, reject }: { ticket: CoordinatorTicket; categories: CoordinatorCategory[]; busy: boolean; cancel: () => void; resolve: (categoryId: string, source: "IMAGE" | "TEXT" | "OTHER", reason: string, severity: TicketSeverity | null) => void; reject: (reason: string) => void }) {
  const analysis = ticket.latest_analysis;
  // One evidence Category per source now, not a list: the agent reports what the
  // text suggested and what the photos suggested, and never merges them.
  const pick = (value: string | null | undefined) => {
    const match = value ? categories.find((row) => row.id === value || row.code === value) : undefined;
    return match ? [match] : [];
  };
  const imageCategories = pick(analysis?.image_category_id);
  const textCategories = pick(analysis?.text_category_id);
  const [decision, setDecision] = useState<"valid" | "invalid">("valid");
  const [source, setSource] = useState<"IMAGE" | "TEXT" | "OTHER">(imageCategories[0] ? "IMAGE" : textCategories[0] ? "TEXT" : "OTHER");
  const options = source === "IMAGE" ? imageCategories : source === "TEXT" ? textCategories : categories;
  const [categoryId, setCategoryId] = useState(options[0]?.id || "");
  const [reason, setReason] = useState("BQL xác nhận kết quả phân loại.");
  const [rejectReason, setRejectReason] = useState("");
  // No severity from the analysis means the backend has nothing to score from:
  // the Coordinator must name one, and nothing here fills it in for them.
  const severityMissing = !ticket.severity;
  const [severity, setSeverity] = useState<TicketSeverity | "">("");
  const canResolve = !busy && decision === "valid" && Boolean(categoryId) && reason.trim().length >= 3 && (!severityMissing || severity !== "");
  const chooseSource = (next: "IMAGE" | "TEXT" | "OTHER") => {
    const list = next === "IMAGE" ? imageCategories : next === "TEXT" ? textCategories : categories;
    setSource(next);
    setCategoryId(list[0]?.id || "");
  };
  return <div className="managerManualDecisionGrid managerPanelManualReview">
    <section className={`managerManualDecisionCard${decision === "valid" ? " active" : ""}`}>
      <label className="managerManualDecisionTitle"><input type="radio" name="panel-manual-decision" checked={decision === "valid"} onChange={() => setDecision("valid")} /><span><strong>Xác nhận Category hợp lệ</strong><small>Chọn kết quả đúng để hệ thống tính lại mức ưu tiên.</small></span></label>
      <div className="managerManualSourceOptions">
        <button type="button" className={source === "IMAGE" ? "active" : ""} disabled={!imageCategories.length} onClick={() => { setDecision("valid"); chooseSource("IMAGE"); }}>Theo ảnh</button>
        <button type="button" className={source === "TEXT" ? "active" : ""} disabled={!textCategories.length} onClick={() => { setDecision("valid"); chooseSource("TEXT"); }}>Theo văn bản</button>
        <button type="button" className={source === "OTHER" ? "active" : ""} onClick={() => { setDecision("valid"); chooseSource("OTHER"); }}>Danh mục khác</button>
      </div>
      <div className="field"><label htmlFor="panel-manual-category">Danh mục xác nhận</label><select id="panel-manual-category" value={categoryId} onChange={(event) => { setDecision("valid"); setCategoryId(event.target.value); }}>{options.map((row) => <option value={row.id} key={row.id}>{formatCategoryName(row.code, row.display_name)}</option>)}</select></div>
      <SeverityField id="panel-manual-severity" missing={severityMissing} stored={ticket.severity} value={severity} onChange={(next) => { setDecision("valid"); setSeverity(next); }} />
      <div className="field"><label htmlFor="panel-manual-reason">Ghi chú xác nhận</label><textarea id="panel-manual-reason" value={reason} onChange={(event) => { setDecision("valid"); setReason(event.target.value); }} /></div>
      <p className="managerManualStepHint">Hệ thống tính lại điểm số; ticket vẫn cần được duyệt trước khi phân công.</p>
      <button className="button" type="button" disabled={!canResolve} onClick={() => resolve(categoryId, source, reason.trim(), severityMissing ? severity || null : null)}><CheckCircle2 size={16} />Xác nhận & tính lại điểm</button>
    </section>
    <section className={`managerManualDecisionCard invalid${decision === "invalid" ? " active" : ""}`}>
      <label className="managerManualDecisionTitle"><input type="radio" name="panel-manual-decision" checked={decision === "invalid"} onChange={() => setDecision("invalid")} /><span><strong>Xác nhận ticket không hợp lệ</strong><small>Loại ticket và gửi lý do rõ ràng cho cư dân.</small></span></label>
      <div className="field"><label htmlFor="panel-manual-reject">Lý do gửi cho cư dân *</label><textarea id="panel-manual-reject" placeholder="Nhập lý do rõ ràng..." value={rejectReason} onFocus={() => setDecision("invalid")} onChange={(event) => setRejectReason(event.target.value)} /></div>
      <p className="managerManualStepHint">Ticket sẽ bị loại bỏ và cư dân nhận được thông báo kèm lý do.</p>
      <button className="button danger" type="button" disabled={busy || decision !== "invalid" || rejectReason.trim().length < 3} onClick={() => reject(rejectReason.trim())}><ShieldX size={16} />Loại bỏ ticket</button>
      <button className="button secondary" type="button" disabled={busy} onClick={cancel}>Hủy</button>
    </section>
  </div>;
}
