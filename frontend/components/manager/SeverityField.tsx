"use client";

import { SEVERITY_LABELS, SEVERITY_OPTIONS, formatSeverity, type TicketSeverity } from "@/lib/severity";

type Props = {
  id: string;
  /** True when the analysis produced no severity and the backend needs one. */
  missing: boolean;
  stored: TicketSeverity | null;
  value: TicketSeverity | "";
  disabled?: boolean;
  onChange: (next: TicketSeverity | "") => void;
};

/** Mức độ nghiêm trọng during manual review.
 *
 *  A required choice when the analysis produced none — §9.5 has no default, so
 *  the empty option stays selectable and the caller keeps the confirm button
 *  disabled until a real value is picked. When the report already carries a
 *  severity this is read-only context: §8.3 never asks the Coordinator to
 *  restate a value the system already holds. */
export function SeverityField({ id, missing, stored, value, disabled, onChange }: Props) {
  if (!missing) {
    return <div className="field managerManualSeverityRead"><span>Mức độ nghiêm trọng</span><strong>{formatSeverity(stored)}</strong></div>;
  }
  return <div className="field">
    <label htmlFor={id}>Mức độ nghiêm trọng *</label>
    <select id={id} value={value} required aria-required="true" disabled={disabled} onChange={(event) => onChange(event.target.value as TicketSeverity | "")}>
      <option value="">— Chọn mức độ —</option>
      {SEVERITY_OPTIONS.map((option) => <option value={option} key={option}>{SEVERITY_LABELS[option]}</option>)}
    </select>
    <small>AI chưa xác định được mức độ. Điều phối viên phải chọn để hệ thống tính lại điểm.</small>
  </div>;
}
