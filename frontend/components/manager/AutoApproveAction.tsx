"use client";

import { CheckCheck, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { approveCoordinatorTicket } from "@/api/backend.api";
import { AUTO_APPROVE_NOTICE, assignmentErrorMessage, autoApprovableTickets } from "@/lib/assignment";
import type { CoordinatorTicket } from "@/types/api";

type Props = {
  tickets: CoordinatorTicket[];
  disabled?: boolean;
  onApproved: (approved: number, failed: number) => void;
};

/** Bulk approval of the reports that are already fully classified.
 *
 *  Each report still goes through `POST /coordinator/tickets/{id}/approve`, one
 *  call each, rather than a bulk endpoint: that keeps the per-ticket status
 *  transition, audit entry and notification exactly as a manual approval, and a
 *  report that has become ineligible since the list was fetched simply fails on
 *  its own without taking the rest of the run down.
 */
export function AutoApproveAction({ tickets, disabled, onApproved }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const confirmRef = useRef<HTMLButtonElement>(null);
  const eligible = autoApprovableTickets(tickets);

  useEffect(() => { if (open) confirmRef.current?.focus(); }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, open]);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const results = await Promise.allSettled(eligible.map((ticket) => approveCoordinatorTicket(ticket.id)));
      const approved = results.filter((result) => result.status === "fulfilled").length;
      const failed = results.length - approved;
      setOpen(false);
      onApproved(approved, failed);
    } catch (reason) {
      setError(assignmentErrorMessage(reason, "Không tự động duyệt được."));
    } finally {
      setBusy(false);
    }
  };

  return <>
    <button type="button" className="button secondary" disabled={disabled} onClick={() => { setError(""); setOpen(true); }}>
      <CheckCheck size={16} />Tự động duyệt
    </button>

    {open && <div className="managerModalBackdrop" role="dialog" aria-modal="true" aria-labelledby="autoApproveTitle">
      <div className="managerModalDialog">
        <h3 id="autoApproveTitle">Tự động duyệt ticket</h3>
        <p>{AUTO_APPROVE_NOTICE}</p>
        <p className="managerModalCount">
          {eligible.length > 0
            ? <><strong>{eligible.length}</strong> ticket đã có phân loại từ hệ thống sẽ được duyệt và chuyển sang <strong>Đã duyệt · chờ phân việc</strong>.</>
            : "Hiện chưa có ticket nào đã được hệ thống phân loại đủ điều kiện để duyệt."}
        </p>
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="managerModalActions">
          <button type="button" className="button secondary" disabled={busy} onClick={() => setOpen(false)}>Hủy</button>
          <button type="button" className="button" ref={confirmRef} disabled={busy || eligible.length === 0} onClick={() => void run()}>
            {busy ? <Loader2 size={16} className="spin" /> : <CheckCheck size={16} />}Xác nhận tự động duyệt
          </button>
        </div>
      </div>
    </div>}
  </>;
}
