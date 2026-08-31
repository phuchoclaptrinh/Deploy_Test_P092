"use client";

import { AlertCircle, Camera, CheckCircle2, CircleX, Clock3, MapPin, Play, Plus, Send, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ChangeEvent, useEffect, useState } from "react";
import { completeTechnicianAssignment, getTechnicianAssignment, loadAttachmentImages, rejectTechnicianAssignment, startTechnicianAssignment, unableTechnicianAssignment, uploadCompletionImage } from "@/api/backend.api";
import { IncidentGallery } from "@/components/IncidentImage";
import { RoleShell } from "@/components/RoleShell";
import { PriorityBadge } from "@/components/StatusBadge";
import { formatDateTime } from "@/lib/mockService";
import type { TicketImage } from "@/lib/types";
import type { TechnicianAssignment } from "@/types/api";

const MAX_COMPLETION_IMAGES = 5;
const MAX_COMPLETION_IMAGE_SIZE = 2 * 1024 * 1024;
const MAX_COMPLETION_TOTAL_SIZE = 5 * 1024 * 1024;
/** The backend refuses a start that is not `planned_order === 0`, so the screen
 *  explains the rule instead of letting the request fail. Only Building
 *  Management can change the order, and it does so through an audited action. */
const QUEUE_HEAD_HINT = "Chỉ được bắt đầu công việc đang ở vị trí 'Làm ngay'. Hãy xử lý xong công việc đứng trước, hoặc đề nghị BQL xếp lại lịch.";
const VIETNAMESE_MONTHS = ["Th1", "Th2", "Th3", "Th4", "Th5", "Th6", "Th7", "Th8", "Th9", "Th10", "Th11", "Th12"];
const technicianStatusLabels: Record<TechnicianAssignment["status"], string> = {
  ASSIGNED: "Đã gán",
  IN_PROGRESS: "Đang xử lý",
  COMPLETED: "Đã hoàn thành",
  REJECTED: "Đã từ chối",
  REASSIGNED: "Đã phân lại",
  UNABLE_TO_HANDLE: "Không thể xử lý",
};
function formatSlaDeadline(value: string | null) {
  if (!value) return null;
  const deadline = new Date(value);
  if (Number.isNaN(deadline.getTime())) return null;
  const hour = String(deadline.getHours()).padStart(2, "0");
  const minute = String(deadline.getMinutes()).padStart(2, "0");
  return `ngày ${deadline.getDate()} ${VIETNAMESE_MONTHS[deadline.getMonth()]}, ${hour}:${minute}`;
}

export default function TechnicianTicketDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [ticket, setTicket] = useState<TechnicianAssignment | null>(null);
  const [images, setImages] = useState<TicketImage[]>([]);
  const [mode, setMode] = useState<"none" | "complete" | "cannot" | "reject">("none");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [completionImages, setCompletionImages] = useState<TicketImage[]>([]);
  const [validationPopup, setValidationPopup] = useState<{ title: string; message: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => { try { const next = await getTechnicianAssignment(id); setTicket(next); setImages(await loadAttachmentImages(next.ticket.attachments || [], "technician")); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không tải được công việc."); } };
  useEffect(() => { load(); }, [id]);
  if (!ticket) return <RoleShell role="technician" title="Chi tiết công việc" backHref="/technician"><div className="emptyState"><h3>{error || "Đang tải..."}</h3><Link className="button" href="/technician">Về danh sách</Link></div></RoleShell>;
  // §4: the planned start is the only forward-looking time left in the model.
  // Not a deadline and not a completion promise -- the scheduler's estimate of
  // when this technician reaches this job.
  const plannedStart = formatSlaDeadline(ticket.planned_start_at ?? null);
  // The backend refuses to start anything but the head of the queue, so the
  // button says so before it is pressed rather than after. `planned_order` is
  // the scheduler's number, which is the same one the queue screen labels
  // "Làm ngay".
  const isQueueHead = ticket.planned_order === 0;
  const run = async (action: () => Promise<unknown>) => { setBusy(true); try { await action(); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể cập nhật."); } finally { setBusy(false); } };
  const readFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files || []);
    event.target.value = "";
    if (!selected.length) return;
    if (completionImages.length + selected.length > MAX_COMPLETION_IMAGES) {
      setValidationPopup({ title: "Quá số lượng ảnh", message: `Chỉ được tải tối đa ${MAX_COMPLETION_IMAGES} ảnh xác nhận.` });
      return;
    }
    if (selected.some((file) => file.size > MAX_COMPLETION_IMAGE_SIZE)) {
      setValidationPopup({ title: "Ảnh có dung lượng lớn", message: "Mỗi ảnh được phép có dung lượng tối đa 2 MB." });
      return;
    }
    const currentSize = completionImages.reduce((total, item) => total + (item.size || Math.round(item.dataUrl.length * .75)), 0);
    if (currentSize + selected.reduce((total, file) => total + file.size, 0) > MAX_COMPLETION_TOTAL_SIZE) {
      setValidationPopup({ title: "Tổng dung lượng quá lớn", message: "Tổng dung lượng ảnh xác nhận tối đa là 5 MB." });
      return;
    }
    try {
      const additions = await Promise.all(selected.map((file) => new Promise<TicketImage>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({ name: file.name, dataUrl: String(reader.result), size: file.size });
        reader.onerror = () => reject(new Error(`Không thể đọc ảnh ${file.name}.`));
        reader.readAsDataURL(file);
      })));
      setCompletionImages((current) => [...current, ...additions]);
    } catch (readError) {
      setValidationPopup({ title: "Không thể đọc ảnh", message: readError instanceof Error ? readError.message : "Vui lòng chọn lại ảnh xác nhận." });
    }
  };
  const submitComplete = () => {
    if (!note.trim()) {
      setValidationPopup({ title: "Chưa có ghi chú xử lý", message: "Vui lòng nhập nội dung đã xử lý trước khi xác nhận hoàn thành." });
      return;
    }
    if (!completionImages.length) {
      setValidationPopup({ title: "Chưa có ảnh xác nhận", message: "Vui lòng chụp hoặc tải lên ít nhất một ảnh sau khi xử lý." });
      return;
    }
    void run(async () => { const uploadIds = await Promise.all(completionImages.map(uploadCompletionImage)); await completeTechnicianAssignment(id, note.trim(), uploadIds); setNote(""); setCompletionImages([]); setMode("none"); });
  };
  const submitCannot = () => {
    if (reason.trim().length < 3) {
      setValidationPopup({ title: "Chưa có lý do", message: "Vui lòng mô tả lý do không thể xử lý và đề xuất bước tiếp theo." });
      return;
    }
    void run(async () => { await unableTechnicianAssignment(id, reason.trim()); setReason(""); setMode("none"); });
  };

  const submitReject = () => {
    if (reason.trim().length < 3) {
      setValidationPopup({ title: "Chưa có lý do", message: "Vui lòng nhập lý do từ chối để BQL phân lại." });
      return;
    }
    void run(async () => { await rejectTechnicianAssignment(id, reason.trim()); setReason(""); setMode("none"); });
  };
  // See the queue page: P4 is the technician-visible urgent band.
  const urgent = ticket.ticket.priority === "P4" && ["ASSIGNED", "IN_PROGRESS"].includes(ticket.status);

  return <RoleShell role="technician" title="Chi tiết" backHref="/technician">
    {error && <div className="alert error">{error}</div>}<section className={`technicianDetailHero${urgent ? " techUrgent" : ""}`}><div>{ticket.ticket.priority && <PriorityBadge priority={ticket.ticket.priority} />}<span className="badge">{technicianStatusLabels[ticket.status]}</span></div><h1>{ticket.ticket.category_display_name || "Công việc bảo trì"}</h1><p className="technicianLocation"><MapPin size={14} />{ticket.ticket.location_label || "Chưa xác định"}</p>{plannedStart && ticket.status !== "COMPLETED" && <p className="technicianSlaCallout"><Clock3 size={16} /><span>Dự kiến bắt đầu {plannedStart}</span></p>}</section>
    <section className="technicianDetailCard">{images.length > 0 && <IncidentGallery images={images} alt="Ảnh phản ánh" className="technicianIncidentGallery" />}<div><span>Mô tả từ cư dân</span><p>{ticket.ticket.description || "Không có mô tả."}</p></div></section>
    <section className="technicianAssignmentNote"><strong>Thông tin phân công</strong><p>Công việc được giao từ Ban quản lý.</p><small>Giao lúc {formatDateTime(ticket.assigned_at)}</small></section>
    <section className="technicianActionPanel"><h2>Cập nhật công việc</h2>{ticket.status === "ASSIGNED" && <><div className="technicianFinalActions"><button className="button" disabled={busy || !isQueueHead} title={isQueueHead ? undefined : QUEUE_HEAD_HINT} onClick={() => run(() => startTechnicianAssignment(id))}><Play size={17} />Bắt đầu xử lý</button><button className="button secondary" disabled={busy} onClick={() => { setMode("reject"); setError(""); }}><CircleX size={17} />Từ chối</button></div>{!isQueueHead && <p className="technicianQueueHint"><AlertCircle size={15} />{QUEUE_HEAD_HINT}</p>}</>}{ticket.status === "IN_PROGRESS" && <div className="technicianFinalActions"><button className="button" onClick={() => { setMode("complete"); setError(""); }}><CheckCircle2 size={17} />Hoàn thành</button><button className="button secondary" onClick={() => { setMode("cannot"); setError(""); }}><CircleX size={17} />Không xử lý được</button></div>}</section>
    {mode === "complete" && <section className="technicianUpdateForm"><header><CheckCircle2 size={18} /><strong>Xác nhận hoàn thành</strong></header>{error && <div className="alert error">{error}</div>}<div className="field"><label>Ghi chú xử lý *</label><textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Nguyên nhân, vật liệu hoặc linh kiện đã thay..." /></div><div className="technicianCompletionPhotos"><strong>Ảnh sau xử lý *</strong>{completionImages.length === 0 ? <label className="technicianCompletionUpload"><Camera size={23} /><span>Chụp hoặc tải nhiều ảnh</span><small>Tối đa 5 ảnh · mỗi ảnh 2 MB · tổng 5 MB</small><input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={readFiles} /></label> : <><div className="technicianCompletionGrid">{completionImages.map((item, index) => <figure key={`${item.name}-${index}`}><img src={item.dataUrl} alt={`Ảnh xác nhận ${index + 1}`} /><button type="button" title={`Xóa ảnh ${index + 1}`} aria-label={`Xóa ảnh ${index + 1}`} onClick={() => setCompletionImages((current) => current.filter((_, imageIndex) => imageIndex !== index))}><X size={15} /></button><figcaption>Ảnh {index + 1}</figcaption></figure>)}{completionImages.length < MAX_COMPLETION_IMAGES && <label className="technicianCompletionAdd"><Plus size={21} /><span>Thêm ảnh</span><input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={readFiles} /></label>}</div><p className="technicianCompletionCount"><strong>{completionImages.length}/{MAX_COMPLETION_IMAGES} ảnh</strong><span>Có thể chọn thêm hoặc xóa từng ảnh.</span></p></>}</div><button className="button" disabled={busy} onClick={submitComplete}><Send size={16} />Xác nhận hoàn thành</button></section>}
    {mode === "cannot" && <section className="technicianUpdateForm"><header><CircleX size={18} /><strong>Không xử lý được</strong></header>{error && <div className="alert error">{error}</div>}<div className="field"><label>Lý do *</label><textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Mô tả trở ngại và đề xuất bước tiếp theo..." /></div><button className="button danger" disabled={busy} onClick={submitCannot}>Xác nhận</button></section>}
    {mode === "reject" && <section className="technicianUpdateForm"><header><CircleX size={18} /><strong>Từ chối công việc</strong></header>{error && <div className="alert error">{error}</div>}<div className="field"><label>Lý do *</label><textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Ví dụ: không đúng chuyên môn, đang quá tải, cần KTV khác..." /></div><button className="button danger" disabled={busy} onClick={submitReject}>Gửi cho BQL</button></section>}
    {ticket.status === "COMPLETED" && <div className="technicianDoneFooter"><Link className="button" href="/technician"><CheckCircle2 size={17} />Xong</Link></div>}
    {validationPopup && <div className="technicianValidationBackdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setValidationPopup(null); }}><section className="technicianValidationDialog" role="alertdialog" aria-modal="true" aria-labelledby="technician-validation-title"><button type="button" className="technicianValidationClose" title="Đóng" aria-label="Đóng" onClick={() => setValidationPopup(null)}><X size={17} /></button><span className="technicianValidationIcon"><AlertCircle size={22} /></span><h2 id="technician-validation-title">{validationPopup.title}</h2><p>{validationPopup.message}</p><button type="button" className="button" autoFocus onClick={() => setValidationPopup(null)}>Đã hiểu</button></section></div>}
  </RoleShell>;
}
