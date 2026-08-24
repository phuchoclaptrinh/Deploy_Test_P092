import { priorityLabels, statusLabels } from "@/lib/mockService";
import type { Priority, TicketStatus } from "@/lib/types";

export function StatusBadge({ status }: { status: TicketStatus }) {
  return <span className={`badge status-${status}`}>{statusLabels[status]}</span>;
}

export function PriorityBadge({ priority, friendly = false }: { priority: Priority; friendly?: boolean }) {
  return <span className={`badge priority-${priority}`}>{friendly ? priorityLabels[priority] : priority}</span>;
}
