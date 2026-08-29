type AssignmentStatusDisplay = { label: string; tone: "success" | "processing" | "warning" | "danger" | "neutral" };

/** There is no ACCEPTED state: a technician either has the work or is doing it.
 *  ASSIGNED covers everything before "Bắt đầu xử lý" is pressed. */
const LABELS: Record<string, AssignmentStatusDisplay> = {
  ASSIGNED: { label: "Đã gán", tone: "success" },
  IN_PROGRESS: { label: "Đang xử lý", tone: "processing" },
  REJECTED: { label: "KTV từ chối", tone: "warning" },
  REASSIGNED: { label: "Đã phân lại", tone: "warning" },
  COMPLETED: { label: "Hoàn thành", tone: "success" },
  UNABLE_TO_HANDLE: { label: "Không xử lý được", tone: "danger" },
};

/** The assignment lifecycle is more precise than the ticket's APPROVED state. */
export function assignmentStatusDisplay(status: string | null | undefined): AssignmentStatusDisplay | null {
  return status ? LABELS[status] || { label: "Đã gán", tone: "neutral" } : null;
}
