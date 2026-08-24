/** Mức độ nghiêm trọng — the three values the backend scores from (§9.5).
 *  Shared by both manual-review entry points so the Coordinator sees the same
 *  wording in the queue page and in the ticket side panel. */
export type TicketSeverity = "LOW" | "MEDIUM" | "HIGH";

export const SEVERITY_LABELS: Record<TicketSeverity, string> = {
  LOW: "Thấp",
  MEDIUM: "Trung bình",
  HIGH: "Cao",
};

export const SEVERITY_OPTIONS: TicketSeverity[] = ["LOW", "MEDIUM", "HIGH"];

export function formatSeverity(severity: TicketSeverity | null | undefined) {
  return severity ? SEVERITY_LABELS[severity] : "Chưa xác định";
}
