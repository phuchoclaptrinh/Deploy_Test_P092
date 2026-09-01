"use client";

import Link from "next/link";
import { Ban, FileText, GitMerge, Image as ImageIcon, MessageSquarePlus, RotateCcw, SearchX } from "lucide-react";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cancelResidentBackendTicket, getResidentTicket, loadAttachmentImages } from "@/api/backend.api";
import { ResidentAgentAnalysis } from "@/components/ResidentAgentAnalysis";
import { ResidentProgress } from "@/components/resident/ResidentProgress";
import { ResidentShell } from "@/components/resident/ResidentShell";
import { ResidentAlert, ResidentConfirmDialog, ResidentEmpty, ResidentPhotoViewer, ResidentStatusBadge } from "@/components/resident/ResidentUI";
import { formatTicketCode } from "@/lib/display";
import { formatShortDateTime } from "@/lib/residentDate";
import { residentErrorMessage } from "@/lib/residentErrors";
import {
  isCheckingReport,
  isLinkedReport,
  isRejectedReport,
  residentCategoryLabel,
  residentSenderLabel,
  residentStatusView,
  shortPriority,
} from "@/lib/residentStatus";
import { getResidentTicketPrefetch } from "@/lib/residentTicketPrefetch";
import type { TicketImage } from "@/lib/types";
import type { ResidentTicket } from "@/types/api";

/** R-04 Report details with role-safe variants and allowed actions. */
/** How many attachment thumbnails fit one row; the rest collapse into "+N". */
const visiblePhotos = 3;
type ResidentEvidenceImage = TicketImage & { source: "resident" | "technician" };

const backHrefFor = (source: string | null) =>
  source === "requests" || source === "history" ? "/resident/history"
    : source === "notice" || source === "notifications" ? "/resident/notifications"
      : "/resident";

export default function ResidentReportDetailPage() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const backHref = backHrefFor(searchParams.get("from"));
  const justCreated = searchParams.get("created") === "1";

  const prefetched = useMemo(() => getResidentTicketPrefetch(id), [id]);
  const [ticket, setTicket] = useState<ResidentTicket | null>(prefetched?.ticket || null);
  const [images, setImages] = useState<ResidentEvidenceImage[]>(() => (prefetched?.images || []).map((image) => ({ ...image, source: "resident" })));
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [questionOpen, setQuestionOpen] = useState(false);
  const refreshing = useRef(false);
  const attachmentKey = useRef((prefetched?.ticket.attachments || []).map((item) => item.id).join("|"));

  const load = useCallback(async (loadImages = false) => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const next = await getResidentTicket(id);
      setTicket(next);
      const nextKey = (next.attachments || []).map((item) => item.id).join("|");
      if (loadImages || nextKey !== attachmentKey.current) {
        attachmentKey.current = nextKey;
        const loaded = await loadAttachmentImages(next.attachments || [], "resident");
        setImages(loaded.map((image, index) => ({
          ...image,
          source: next.attachments[index]?.attachment_type === "TECHNICIAN_COMPLETION" ? "technician" : "resident",
        })));
      }
      setError("");
    } catch (reason) {
      setError(residentErrorMessage(reason, "Không tải được phản ánh."));
    } finally {
      refreshing.current = false;
    }
  }, [id]);

  useEffect(() => { void load(true); }, [load]);

  const hasTicket = ticket !== null;
  const checking = Boolean(ticket && isCheckingReport(ticket));
  useEffect(() => {
    if (!hasTicket || checking) return;
    const refresh = () => { void load(); };
    const refreshWhenVisible = () => { if (document.visibilityState === "visible") refresh(); };
    const refreshUpdatedTicket = (event: StorageEvent) => {
      if (event.key !== "fixit-ticket-updated" || !event.newValue) return;
      try { if ((JSON.parse(event.newValue) as { id?: string }).id === id) refresh(); } catch { /* Ignore malformed cross-tab data. */ }
    };
    const timer = window.setInterval(refresh, 10_000);
    window.addEventListener("focus", refresh);
    window.addEventListener("storage", refreshUpdatedTicket);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("storage", refreshUpdatedTicket);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [checking, hasTicket, id, load]);

  if (!ticket) {
    return (
      <ResidentShell title="Chi tiết phản ánh" variant="detail" backHref={backHref}>
        {error ? (
          <ResidentEmpty
            icon={<SearchX size={26} />}
            title={error}
            action={<div style={{ display: "flex", gap: 8 }}>
              <button className="rdButton secondary inline" type="button" onClick={() => load(true)}><RotateCcw size={15} />Thử lại</button>
              <Link className="rdButton secondary inline" href="/resident/history">Về danh sách</Link>
            </div>}
          />
        ) : <DetailSkeleton />}
      </ResidentShell>
    );
  }

  const status = residentStatusView(ticket, { waitingForAnswer: questionOpen });
  const priority = shortPriority(ticket.priority_description);
  const linked = isLinkedReport(ticket);
  const rejected = isRejectedReport(ticket);
  const residentImages = images.filter((image) => image.source === "resident");
  const technicianImages = images.filter((image) => image.source === "technician");

  const cancel = async () => {
    setBusy(true);
    setError("");
    try {
      await cancelResidentBackendTicket(ticket.id);
      setConfirmCancel(false);
      setMessage("Đã hủy phản ánh.");
      await load();
    } catch (reason) {
      setConfirmCancel(false);
      setError(residentErrorMessage(reason, "Không hủy được phản ánh."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResidentShell title="Chi tiết phản ánh" variant="detail" backHref={backHref}>
      {justCreated && <ResidentAlert tone="success">Hệ thống đang kiểm tra chi tiết phản ánh.</ResidentAlert>}
      {message && <ResidentAlert tone="success">{message}</ResidentAlert>}
      {error && <ResidentAlert tone="error">{error}<button className="rdTextButton" type="button" onClick={() => load()}><RotateCcw size={15} />Thử lại</button></ResidentAlert>}

      <section className="rdOverview">
        <div className="rdOverviewTop">
          <h1>{residentCategoryLabel(ticket)}</h1>
          <ResidentStatusBadge status={status} />
        </div>
        <p className="rdOverviewMeta">
          <span>Mã {ticket.display_code || formatTicketCode(ticket.id)}</span>
          <span>Gửi lúc {formatShortDateTime(ticket.created_at)}</span>
        </p>
      </section>

      {checking && <ResidentAgentAnalysis ticketId={ticket.id} hideCheckingState onComplete={(next) => { setQuestionOpen(false); setTicket(next); }} onQuestionChange={setQuestionOpen} />}

      {rejected && (
        <section className="rdCard">
          <ResidentAlert tone="warning">
            {ticket.invalid_reason_text || "Phản ánh chưa được tiếp nhận."} Vui lòng tạo phản ánh mới với thông tin rõ hơn.
          </ResidentAlert>
          <Link className="rdButton rdCardAction" href="/resident/new"><MessageSquarePlus size={18} />Gửi phản ánh mới</Link>
        </section>
      )}

      {linked && (
        <section className="rdCard">
          <div className="rdCardHead">
            <span aria-hidden="true"><GitMerge size={19} /></span>
            <div>
              <strong>Sự cố này đã được báo và đang được xử lý</strong>
              <small>Phản ánh gốc: {ticket.duplicate_master_display_code || "đang cập nhật"}</small>
            </div>
          </div>
          <p className="rdReportCategory">Bạn sẽ nhận được cập nhật khi sự cố được xử lý xong.</p>
        </section>
      )}

      <ResidentProgress ticket={ticket} />

      <section className="rdCard">
        <div className="rdCardHead">
          <span aria-hidden="true"><FileText size={19} /></span>
          <div><strong>Thông tin chi tiết</strong><small>Thông tin Ban quản lý nhận được</small></div>
        </div>
        <dl className="rdFacts">
          <div className="rdFactRow"><dt>Vị trí</dt><dd>{ticket.location_label?.trim() || "Chưa cung cấp"}</dd></div>
          {priority && <div className="rdFactRow"><dt>Mức độ</dt><dd>{priority}</dd></div>}
          <div className="rdFactRow"><dt>Người gửi</dt><dd>{residentSenderLabel(ticket)}</dd></div>
          {ticket.technician?.full_name && <div className="rdFactRow"><dt>Kỹ thuật viên</dt><dd>{ticket.technician.full_name}</dd></div>}
        </dl>
      </section>

      <section className="rdCard">
        <strong>Mô tả</strong>
        <p className="rdDescText">{ticket.description?.trim() || "Phản ánh được gửi bằng hình ảnh."}</p>
      </section>

      {ticket.completion_note && (
        <section className="rdCard rdCompletionNote">
          <strong>Ghi chú KTV sau xử lý</strong>
          <p className="rdDescText">{ticket.completion_note}</p>
        </section>
      )}

      {residentImages.length > 0 && (
        <section className="rdCard">
          <div className="rdCardHead">
            <span aria-hidden="true"><ImageIcon size={19} /></span>
            <div><strong>Ảnh bạn đã gửi</strong><small>{residentImages.length} ảnh</small></div>
          </div>
          <div className="rdPhotoStrip">
            {residentImages.slice(0, visiblePhotos).map((image, index) => {
              const hidden = residentImages.length - visiblePhotos;
              return (
                <button type="button" key={`${image.name}-${index}`} onClick={() => setViewerIndex(images.indexOf(image))} aria-label={`Xem ảnh bạn đã gửi ${index + 1}`}>
                  <img src={image.dataUrl} alt={`Ảnh bạn đã gửi ${index + 1}`} />
                  {hidden > 0 && index === visiblePhotos - 1 && <span className="rdPhotoMore" aria-hidden="true">+{hidden}</span>}
                </button>
              );
            })}
          </div>
        </section>
      )}

      {technicianImages.length > 0 && (
        <section className="rdCard rdTechnicianEvidence">
          <div className="rdCardHead">
            <span aria-hidden="true"><ImageIcon size={19} /></span>
            <div><strong>Ảnh KTV sau xử lý</strong><small>{technicianImages.length} ảnh</small></div>
          </div>
          <div className="rdPhotoStrip">
            {technicianImages.slice(0, visiblePhotos).map((image, index) => {
              const hidden = technicianImages.length - visiblePhotos;
              return <button type="button" key={`${image.name}-${index}`} onClick={() => setViewerIndex(images.indexOf(image))} aria-label={`Xem ảnh KTV ${index + 1}`}>
                <img src={image.dataUrl} alt={`Ảnh KTV sau xử lý ${index + 1}`} />
                {hidden > 0 && index === visiblePhotos - 1 && <span className="rdPhotoMore" aria-hidden="true">+{hidden}</span>}
              </button>;
            })}
          </div>
        </section>
      )}

      {justCreated && <Link className="rdButton rdFinishReport" href="/resident">Hoàn thành</Link>}

      {ticket.available_actions.includes("CANCEL") && (
        <button className="rdButton danger" type="button" disabled={busy} onClick={() => setConfirmCancel(true)}><Ban size={18} />Hủy phản ánh</button>
      )}

      {confirmCancel && (
        <ResidentConfirmDialog
          title="Hủy phản ánh này?"
          body="Ban quản lý sẽ dừng xử lý phản ánh."
          safeLabel="Giữ phản ánh"
          destructiveLabel="Hủy phản ánh"
          busy={busy}
          onSafe={() => setConfirmCancel(false)}
          onDestructive={cancel}
        />
      )}

      {viewerIndex !== null && <ResidentPhotoViewer images={images} index={viewerIndex} onClose={() => setViewerIndex(null)} onChange={setViewerIndex} labels={images.map((image) => image.source === "technician" ? "Ảnh KTV sau xử lý" : "Ảnh bạn đã gửi")} />}
    </ResidentShell>
  );
}

function DetailSkeleton() {
  return <div className="rdList" aria-hidden="true">
    <div className="rdSkeleton" style={{ height: 96, borderRadius: 14 }} />
    <div className="rdSkeleton" style={{ height: 180, borderRadius: 14 }} />
    <div className="rdSkeleton" style={{ height: 140, borderRadius: 14 }} />
  </div>;
}
