"use client";

import { AlertTriangle, Loader2, Power, PowerOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { getAutoAssignmentToggle, setAutoAssignment } from "@/api/backend.api";
import {
  AUTO_ASSIGNMENT_CONFIRMATION,
  AUTO_ASSIGNMENT_OFF_NOTICE,
  toggleSummary,
} from "@/lib/visualAssignment";
import type { AutoAssignmentToggle } from "@/types/api";

/** §2's ON/OFF control, and the modal that has to be read before it goes on.
 *
 *  The asymmetry here is about consent, not permission. §9 removed the rule
 *  that autonomy could only be enabled after confirming a proposal, so both
 *  directions are one request — but turning it **on** hands out work with
 *  nobody looking, so the exact §2 wording is shown first and `acknowledged` is
 *  only ever sent from this modal's confirm button. The backend refuses
 *  `enabled: true` without it, so skipping the modal is not a shortcut, it is
 *  a 400.
 *
 *  Turning it **off** shows a shorter notice, because the thing people get
 *  wrong about switching off is assuming it recalls work already assigned. It
 *  does not, and the notice says so.
 */
export function AutoAssignmentControl({ onChanged }: { onChanged?: () => void }) {
  const [toggle, setToggle] = useState<AutoAssignmentToggle | null>(null);
  const [pending, setPending] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const load = useCallback(async () => {
    try {
      const next = await getAutoAssignmentToggle();
      if (mounted.current) setToggle(next);
    } catch {
      // A background read failing must not replace the manager's screen with a
      // transport message; the explicit action below surfaces its own errors.
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const apply = async (enabled: boolean) => {
    setBusy(true);
    setError("");
    try {
      const next = await setAutoAssignment(enabled, { acknowledged: true, expectedVersion: toggle?.version });
      if (!mounted.current) return;
      setToggle(next);
      setPending(null);
      onChanged?.();
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "Không đổi được trạng thái.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const enabled = Boolean(toggle?.enabled);
  return (
    <>
      <button
        type="button"
        className={`button${enabled ? "" : " secondary"}`}
        onClick={() => setPending(!enabled)}
        aria-pressed={enabled}
        title={toggleSummary(toggle)}
      >
        {enabled ? <Power size={17} /> : <PowerOff size={17} />}
        {enabled ? "Phân việc tự động: BẬT" : "Phân việc tự động: TẮT"}
      </button>

      {pending !== null && (
        <div className="modalBackdrop" role="dialog" aria-modal="true" aria-labelledby="autoAssignTitle">
          <div className="managerModal">
            <header><strong id="autoAssignTitle">{pending ? "Bật phân việc tự động?" : "Tắt phân việc tự động?"}</strong></header>
            <div className="managerModalBody">
              <p className="autoAssignExplainer">
                {pending ? AUTO_ASSIGNMENT_CONFIRMATION : AUTO_ASSIGNMENT_OFF_NOTICE}
              </p>
              {!pending && Boolean(toggle?.open_event_count) && (
                <p className="autoAssignWarning">
                  <AlertTriangle size={14} aria-hidden="true" />
                  {toggle?.open_event_count} phản ánh đang chờ trong hàng đợi sẽ được chuyển cho Ban quản lý.
                </p>
              )}
              {error && <div className="alert error">{error}</div>}
              <div className="modalActions">
                <button type="button" className="button secondary" onClick={() => { setPending(null); setError(""); }} disabled={busy}>
                  Huỷ
                </button>
                <button type="button" className="button" onClick={() => void apply(pending)} disabled={busy}>
                  {busy ? <><Loader2 size={16} className="spin" />Đang lưu...</> : pending ? "Bật phân việc tự động" : "Tắt phân việc tự động"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
