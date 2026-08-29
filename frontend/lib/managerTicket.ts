/** Formatting shared by the Coordinator dashboard, detail panel and clusters. */

const dateTimeFormat = new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

const clockFormat = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });

/** The submitted-at cell reads time first, date underneath: two tickets from the
 *  same shift are compared by the clock, and the date is only the tie-breaker. */
export function formatClock(value: string | null | undefined) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? clockFormat.format(date) : "—";
}

export function formatDay(value: string | null | undefined) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? dayFormat.format(date) : "";
}

/** Date and time, so a submitted-at cell is unambiguous across months. */
export function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormat.format(date);
}

/**
 * Deadline wording computed only from the backend `sla_due_at`.
 * A ticket without one shows an em dash: the UI never invents a deadline.
 */
export function deadlineLabel(slaDueAt: string | null | undefined, now: number = Date.now()) {
  if (!slaDueAt) return "—";
  const due = Date.parse(slaDueAt);
  if (Number.isNaN(due)) return "—";
  const diff = due - now;
  const overdue = diff < 0;
  const minutes = Math.floor(Math.abs(diff) / 60_000);
  const amount = minutes >= 1440
    ? `${Math.floor(minutes / 1440)} ngày`
    : minutes >= 60
      ? `${Math.floor(minutes / 60)} giờ`
      : `${Math.max(minutes, 1)} phút`;
  return overdue ? `Hết hạn ${amount}` : `Còn ${amount}`;
}

export function isOverdue(slaDueAt: string | null | undefined, now: number = Date.now()) {
  if (!slaDueAt) return false;
  const due = Date.parse(slaDueAt);
  return !Number.isNaN(due) && due < now;
}
