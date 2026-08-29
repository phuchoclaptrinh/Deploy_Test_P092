"use client";

import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, Clock3, Info, Timer, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { ResidentStatusTone, ResidentStatusView } from "@/lib/residentStatus";
import type { TicketImage } from "@/lib/types";

/** C-13 status badge. Tone is reinforced with an icon so meaning never depends on color. */
const toneIcons: Record<ResidentStatusTone, typeof Info> = {
  info: Info,
  attention: AlertTriangle,
  positive: CheckCircle2,
  critical: CircleAlert,
  neutral: Clock3,
};

export function ResidentStatusBadge({ status }: { status: ResidentStatusView }) {
  const Icon = toneIcons[status.tone];
  return <span className={`rdBadge ${status.tone}`}><Icon size={13} />{status.label}</span>;
}

/** C-14 expected-time block. */
export function ResidentExpectedTime({ text }: { text: string | null }) {
  if (!text) return null;
  return <span className="rdExpected"><Timer size={14} />{text}</span>;
}

/** C-23 inline alert. */
export function ResidentAlert({ tone, children, role }: { tone: "info" | "success" | "warning" | "error"; children: React.ReactNode; role?: "alert" | "status" }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "error" ? CircleAlert : tone === "warning" ? AlertTriangle : Info;
  return <div className={`rdAlert ${tone}`} role={role || (tone === "error" ? "alert" : "status")}><Icon size={17} /><div>{children}</div></div>;
}

/** C-22 empty state. */
export function ResidentEmpty({ icon, title, body, action }: { icon: React.ReactNode; title: string; body?: string; action?: React.ReactNode }) {
  return (
    <section className="rdEmpty">
      <span aria-hidden="true">{icon}</span>
      <strong>{title}</strong>
      {body && <p>{body}</p>}
      {action}
    </section>
  );
}

/** C-26 loading skeletons: match the final card height to avoid layout jumps. */
export function ResidentCardSkeletons({ count = 3 }: { count?: number }) {
  return <div className="rdList" aria-hidden="true">{Array.from({ length: count }, (_, index) => <div className="rdSkeleton rdSkeletonCard" key={index} />)}</div>;
}

/** C-24 confirmation dialog: focus starts on the safe action and Escape keeps the draft. */
export function ResidentConfirmDialog({
  title,
  body,
  safeLabel,
  destructiveLabel,
  onSafe,
  onDestructive,
  busy = false,
}: {
  title: string;
  body: string;
  safeLabel: string;
  destructiveLabel: string;
  onSafe: () => void;
  onDestructive: () => void;
  busy?: boolean;
}) {
  const safeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    safeRef.current?.focus();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onSafe(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onSafe]);

  return (
    <div className="rdDialogBackdrop" onClick={(event) => { if (event.target === event.currentTarget) onSafe(); }}>
      <div className="rdDialog" role="alertdialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <p>{body}</p>
        <button className="rdButton" type="button" ref={safeRef} onClick={onSafe}>{safeLabel}</button>
        <button className="rdButton danger" type="button" disabled={busy} onClick={onDestructive}>{busy ? <><span className="rdSpinner" />Đang xử lý…</> : destructiveLabel}</button>
      </div>
    </div>
  );
}

/** O-06 photo viewer. */
export function ResidentPhotoViewer({ images, index, onClose, onChange, labels }: { images: TicketImage[]; index: number; onClose: () => void; onChange: (next: number) => void; labels?: string[] }) {
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft" && index > 0) onChange(index - 1);
      if (event.key === "ArrowRight" && index < images.length - 1) onChange(index + 1);
    };
    document.addEventListener("keydown", keys);
    return () => document.removeEventListener("keydown", keys);
  }, [images.length, index, onChange, onClose]);

  return (
    <div className="rdViewer" role="dialog" aria-modal="true" aria-label="Ảnh đã gửi">
      <header>
        <span>{index + 1}/{images.length}</span>
        <button type="button" onClick={onClose} aria-label="Đóng" title="Đóng"><X size={22} /></button>
      </header>
      <img src={images[index].dataUrl} alt={`${labels?.[index] || "Ảnh đã gửi"} ${index + 1}`} />
      <footer>
        <button type="button" disabled={index === 0} onClick={() => onChange(index - 1)} aria-label="Ảnh trước" title="Ảnh trước"><ChevronLeft size={22} /></button>
        <button type="button" disabled={index === images.length - 1} onClick={() => onChange(index + 1)} aria-label="Ảnh tiếp theo" title="Ảnh tiếp theo"><ChevronRight size={22} /></button>
      </footer>
    </div>
  );
}
