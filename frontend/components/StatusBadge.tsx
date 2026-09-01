import { priorityLabels, statusLabels } from "@/lib/mockService";
import type { Priority, TicketStatus } from "@/lib/types";

export function StatusBadge({ status }: { status: TicketStatus }) {
  return <span className={`badge status-${status}`}>{statusLabels[status]}</span>;
}

/** The band, coloured by how much it demands of the reader.
 *
 *  P5 gets its own tone because it is the one that means "stop what you are
 *  doing"; P1 and P2 share one because a manager acts on neither differently.
 *  Spending five distinct colours on five bands would make the emergency
 *  compete with four other things for attention.
 */
export function PriorityBadge({ priority, friendly = false }: { priority: Priority; friendly?: boolean }) {
  return <span className={`badge priority-${priority}`}>{friendly ? priorityLabels[priority] : priority}</span>;
}
