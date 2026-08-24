"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, SlidersHorizontal, UserPlus, X } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { approveCoordinatorTicket, assignCoordinatorTicket, getCoordinatorTicket, listBackendCategories, listCoordinatorTechnicians, loadAttachmentImages, overrideCoordinatorClassification, rejectCoordinatorManualReview, resolveCoordinatorManualReview } from "@/api/backend.api";
import { IncidentGallery } from "@/components/IncidentImage";
import { ManagerManualReview } from "@/components/manager/ManagerManualReview";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { RoleShell } from "@/components/RoleShell";
import { PriorityBadge } from "@/components/StatusBadge";
import { formatCategoryName } from "@/lib/category";
import { formatTicketCode } from "@/lib/display";
import { markManagerTicketSeen } from "@/lib/managerTicketSeen";
import { formatDateTime } from "@/lib/mockService";
import type { TicketImage } from "@/lib/types";
import type { CoordinatorCategory, CoordinatorTicket, TechnicianSummary } from "@/types/api";

type Mode = "none" | "approve" | "override" | "assign" | "manual";

const managerStatusLabels: Record<string, string> = {
  NEW: "Mới",
  WAITING_RESIDENT_INFO: "Chờ cư dân bổ sung",
  APPROVED: "Đã duyệt",
  IN_PROGRESS: "Đang xử lý",
  COMPLETED: "Hoàn thành",
  UNRESOLVABLE: "Không xử lý được",
  CANCELLED: "Đã hủy",
  INVALID: "Không hợp lệ",
};

const classificationStatusLabels: Record<string, string> = {
  PENDING: "Chờ phân loại",
  PROCESSING: "Đang phân loại",
  RESOLVED: "Đã phân loại",
  MANUAL_REVIEW: "Chờ BQL xác nhận",
  FAILED: "Phân loại thất bại",
};

export default function ManagerTicketDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [ticket, setTicket] = useState<CoordinatorTicket | null>(null);
  const [images, setImages] = useState<TicketImage[]>([]);
  const [categories, setCategories] = useState<CoordinatorCategory[]>([]);
  const [technicians, setTechnicians] = useState<TechnicianSummary[]>([]);
  const [mode, setMode] = useState<Mode>("none");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const supportData = Promise.all([listBackendCategories(), listCoordinatorTechnicians()]);
      const next = await getCoordinatorTicket(id);
      setTicket(next);
      markManagerTicketSeen(id);
      setError("");
      void loadAttachmentImages(next.attachments || [], "manager").then(setImages).catch(() => setImages([]));
      void supportData.then(([categoryRows, roster]) => {
        setCategories(categoryRows.filter((row) => row.is_active));
        setTechnicians(roster.filter((row) => row.is_active));
      }).catch(() => undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được ticket.");
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);
  const announceTicketUpdate = () => window.localStorage.setItem("fixit-ticket-updated", JSON.stringify({ id, updatedAt: Date.now() }));
  const run = async (action: () => Promise<unknown>, success: string) => { setBusy(true); try { await action(); announceTicketUpdate(); setMessage(success); setMode("none"); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể cập nhật."); } finally { setBusy(false); } };
  const assignTechnician = async (technicianId: string) => {
    setBusy(true);
    setError("");
    try {
      await assignCoordinatorTicket(id, technicianId);
      announceTicketUpdate();
      router.replace("/manager");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể gán kỹ thuật viên.");
      setBusy(false);
      setMode("none");
    }
  };
  if (!ticket) return <RoleShell role="manager" title="Chi tiết phản ánh"><div className="emptyState managerDetailLoading">{error ? <strong>{error}</strong> : <><span className="spinner" /><strong>Đang tải phản ánh...</strong></>}</div></RoleShell>;
  const displayedStatus = managerTicketDisplayStatus(ticket);
  const canApprove = ticket.status === "NEW" && ticket.available_actions.includes("APPROVE");
  const canAssign = ticket.status === "APPROVED" && ticket.available_actions.includes("ASSIGN");
  const canOverride = ticket.status === "NEW" && ticket.available_actions.includes("OVERRIDE_CLASSIFICATION");
  const canManualReview = ticket.available_actions.includes("RESOLVE_MANUAL_REVIEW");
  return <RoleShell role="manager" eyebrow="Phản ánh cư dân" title="Chi tiết phản ánh" subtitle={`#${formatTicketCode(ticket.id)} · ${formatDateTime(ticket.created_at)}`}>
    {message && <div className="managerFeedbackBanner" role="status" aria-live="polite"><span className="managerFeedbackIcon"><CheckCircle2 size={19} /></span><div><strong>Cập nhật thành công</strong><p>{message}</p></div><button onClick={() => setMessage("")}><X size={16} /></button></div>}
    {error && <div className="alert error">{error}</div>}
    <div className="managerPageStack"><div className="managerDetailContext"><Link href="/manager"><ArrowLeft size={15} />Danh sách phản ánh</Link><div>{ticket.priority && <PriorityBadge priority={ticket.priority} />}<span className={`badge managerStatusBadge ${displayedStatus.tone}`}>{displayedStatus.label}</span></div></div>
      <div className="managerTicketWorkspace"><ManagerSurface className="managerTicketEvidence" title="Nội dung cư dân gửi"><IncidentGallery images={images} alt="Ảnh phản ánh" className="managerTicketGallery" /><div className="managerTicketDescription"><span>Mô tả phản ánh</span><p>{ticket.description || "Cư dân không nhập mô tả."}</p></div><div className="managerTicketMetaGrid"><Meta label="Vị trí" value={ticket.location_label || "Chưa xác định"} /><Meta label="Gửi lúc" value={formatDateTime(ticket.created_at)} /></div></ManagerSurface>
        <aside className="managerTicketSideStack"><ManagerSurface className="managerTicketDecision" title="Phân loại và xử lý"><div className="managerTicketSummary"><Meta label="Danh mục" value={formatCategoryName(ticket.category)} /><div className="metaItem"><span>Mức ưu tiên</span>{ticket.priority ? <PriorityBadge priority={ticket.priority} /> : <strong>Chưa xác định</strong>}</div><Meta label="Điểm" value={managerScoreValue(ticket)} /><Meta label="Phân loại" value={classificationStatusLabels[ticket.classification_status] || "Chưa xác định"} /><Meta label="KTV phụ trách" value={managerTechnician(ticket)} /></div><div className="managerTicketActions">{canManualReview && <button className="button" disabled={busy} onClick={() => setMode("manual")}><CheckCircle2 size={17} />Duyệt phân loại thủ công</button>}{canApprove && <button className="button" disabled={busy} onClick={() => setMode("approve")}><CheckCircle2 size={17} />Duyệt phản ánh</button>}{canAssign && <button className="button" disabled={busy} onClick={() => setMode("assign")}><UserPlus size={17} />Phân công kỹ thuật viên</button>}{canOverride && <button className="button secondary" disabled={busy} onClick={() => setMode("override")}><SlidersHorizontal size={17} />Chỉnh phân loại</button>}</div></ManagerSurface></aside>
      </div>
    </div>
    {mode === "approve" && <Modal title={`Duyệt phản ánh · #${formatTicketCode(ticket.id)}`} onClose={() => setMode("none")}><ApproveForm ticket={ticket} busy={busy} cancel={() => setMode("none")} submit={() => void run(() => approveCoordinatorTicket(id), "Đã duyệt phản ánh.")} /></Modal>}
    {mode === "override" && <Modal title={`Sửa đổi Category / Priority · #${formatTicketCode(ticket.id)}`} onClose={() => setMode("none")}><OverrideForm ticket={ticket} categories={categories} busy={busy} cancel={() => setMode("none")} submit={(categoryId, priority, reason) => void run(() => overrideCoordinatorClassification(id, categoryId, priority, reason), "Đã lưu thay đổi.")} /></Modal>}
    {mode === "assign" && <Modal title={`Phân công kỹ thuật viên · #${formatTicketCode(ticket.id)}`} onClose={() => setMode("none")}><AssignForm technicians={technicians} busy={busy} submit={assignTechnician} /></Modal>}
    {mode === "manual" && <Modal title={`Duyệt phân loại thủ công · #${formatTicketCode(ticket.id)}`} onClose={() => setMode("none")}><ManagerManualReview ticket={ticket} images={images} categories={categories} busy={busy} onResolve={(categoryId, source, reason, severity) => void run(() => resolveCoordinatorManualReview(id, categoryId, source, reason, severity), "Đã xác nhận danh mục và tính lại điểm. Phản ánh vẫn cần được duyệt.")} onReject={(reason) => void run(() => rejectCoordinatorManualReview(id, reason), "Đã loại phản ánh không hợp lệ.")} /></Modal>}
  </RoleShell>;
}

function managerStatusTone(status: string) {
  if (status === "COMPLETED") return "success";
  if (status === "WAITING_RESIDENT_INFO") return "warning";
  if (["UNRESOLVABLE", "INVALID"].includes(status)) return "danger";
  if (status === "CANCELLED") return "neutral";
  return "info";
}

function managerTicketDisplayStatus(ticket: CoordinatorTicket) {
  if (["PENDING", "PROCESSING"].includes(ticket.classification_status)) return { label: "Đang phân tích", tone: "processing" };
  return { label: managerStatusLabels[ticket.status] || "Chưa xác định", tone: managerStatusTone(ticket.status) };
}

function managerScoreValue(ticket: CoordinatorTicket) {
  if (ticket.score_total != null) return ticket.score_total.toString();
  if (ticket.red_flag_detected) return "Cờ đỏ - không tính điểm";
  if (["PENDING", "PROCESSING", "MANUAL_REVIEW"].includes(ticket.classification_status)) return "Chờ tính điểm";
  return "Chưa có";
}

function managerTechnician(ticket: CoordinatorTicket) {
  if (!ticket.active_assignment_id) return "Chưa gán";
  return ticket.active_technician_name || "Kỹ thuật viên";
}

function Meta({ label, value }: { label: string; value: string }) { return <div className="metaItem"><span>{label}</span><strong>{value}</strong></div>; }
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) { return <div className="modalBackdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="managerModal"><header><strong>{title}</strong><button className="iconButton" title="Đóng" aria-label="Đóng" onClick={onClose}><X size={17} /></button></header><div className="managerModalBody">{children}</div></section></div>; }
function ApproveForm({ ticket, busy, cancel, submit }: { ticket: CoordinatorTicket; busy: boolean; cancel: () => void; submit: () => void }) { return <div className="modalForm managerApproveForm"><p className="helper">Kiểm tra kết quả cuối trước khi chuyển phản ánh sang bước phân công.</p><div className="managerApprovalSummary"><Meta label="Danh mục" value={formatCategoryName(ticket.category)} /><div className="metaItem"><span>Mức ưu tiên</span>{ticket.priority ? <PriorityBadge priority={ticket.priority} /> : <strong>Chưa xác định</strong>}</div><Meta label="Điểm" value={managerScoreValue(ticket)} /></div><div className="modalActions"><button className="button secondary" disabled={busy} onClick={cancel}>Hủy</button><button className="button" disabled={busy} onClick={submit}><CheckCircle2 size={16} />Xác nhận duyệt</button></div></div>; }
function OverrideForm({ ticket, categories, busy, cancel, submit }: { ticket: CoordinatorTicket; categories: CoordinatorCategory[]; busy: boolean; cancel: () => void; submit: (category: string, priority: string, reason: string) => void }) { const [category, setCategory] = useState(ticket.category_id || categories[0]?.id || ""), [priority, setPriority] = useState<string>(ticket.priority || "P2"), [reason, setReason] = useState(""); const changed = category !== ticket.category_id || priority !== ticket.priority; return <div className="modalForm managerOverrideForm"><div className="managerCurrentClassification"><Meta label="Category hiện tại" value={formatCategoryName(ticket.category)} /><Meta label="Priority hiện tại" value={ticket.priority || "Chưa xác định"} /></div><div className="formGrid"><div className="field"><label>Category mới</label><select value={category} onChange={(event) => setCategory(event.target.value)}>{categories.map((row) => <option value={row.id} key={row.id}>{formatCategoryName(row.code, row.display_name)}</option>)}</select></div><div className="field"><label>Priority mới</label><select value={priority} onChange={(event) => setPriority(event.target.value)}><option>P1</option><option>P2</option><option>P3</option></select></div></div><div className="field"><label>Lý do điều chỉnh *</label><textarea placeholder="Nhập lý do để lưu vào lịch sử thay đổi..." value={reason} onChange={(event) => setReason(event.target.value)} /></div><p className="helper">Thay đổi sẽ được lưu trong lịch sử hệ thống.</p><div className="modalActions"><button className="button secondary" disabled={busy} onClick={cancel}>Hủy</button><button className="button" disabled={busy || !category || !changed || reason.trim().length < 3} onClick={() => submit(category, priority, reason.trim())}>Lưu thay đổi</button></div></div>; }
function AssignForm({ technicians, busy, submit }: { technicians: TechnicianSummary[]; busy: boolean; submit: (id: string) => void }) { const [selected, setSelected] = useState(technicians.find((row) => row.is_available)?.user_id || ""); return <div className="managerAssignForm"><div className="managerTechOptions">{technicians.map((row) => <label className={`managerTechOption${row.is_available ? " match" : ""}`} key={row.user_id}><input type="radio" checked={selected === row.user_id} disabled={!row.is_available} onChange={() => setSelected(row.user_id)} /><span><strong>{row.full_name || row.user_id.slice(0, 8)}</strong><small>{row.skill_category_ids.length} nhóm chuyên môn</small></span><b className={row.is_available ? "available" : "busy"}>{row.is_available ? "Rảnh" : "Bận"}</b></label>)}</div><div className="modalActions"><button className="button" disabled={busy || !selected} onClick={() => submit(selected)}><UserPlus size={16} />Phân công</button></div></div>; }
