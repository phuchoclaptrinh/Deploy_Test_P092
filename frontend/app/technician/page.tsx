"use client";

import Link from "next/link";
import { ClipboardCheck, Clock3, MapPin } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { listTechnicianAssignments } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { PriorityBadge } from "@/components/StatusBadge";
import { formatTicketCode } from "@/lib/display";
import { formatDateTime } from "@/lib/mockService";
import type { TechnicianAssignment } from "@/types/api";

type QueueFilter = "open" | "today" | "completed";

const openStatuses: TechnicianAssignment["status"][] = ["ASSIGNED", "ACCEPTED", "IN_PROGRESS"];
const statusLabels: Record<TechnicianAssignment["status"], string> = {
  ASSIGNED: "Đã gán",
  ACCEPTED: "Đã nhận việc",
  IN_PROGRESS: "Đang xử lý",
  COMPLETED: "Đã xong",
  REJECTED: "Đã từ chối",
  REASSIGNED: "Đã phân lại",
  UNABLE_TO_HANDLE: "Không thể xử lý",
};
const statusProgress: Record<TechnicianAssignment["status"], number> = {
  ASSIGNED: 25,
  ACCEPTED: 50,
  IN_PROGRESS: 75,
  COMPLETED: 100,
  REJECTED: 100,
  REASSIGNED: 100,
  UNABLE_TO_HANDLE: 100,
};

function assignedToday(assignedAt: string) {
  const assigned = new Date(assignedAt);
  const today = new Date();
  return assigned.getFullYear() === today.getFullYear() && assigned.getMonth() === today.getMonth() && assigned.getDate() === today.getDate();
}

export default function TechnicianQueuePage() {
  const [tickets, setTickets] = useState<TechnicianAssignment[]>([]);
  const [filter, setFilter] = useState<QueueFilter>("open");
  const [error, setError] = useState("");
  const load = useCallback(async () => { try { const assignments = await listTechnicianAssignments(); setTickets(assignments); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không tải được công việc."); } }, []);
  useEffect(() => { load(); const timer = window.setInterval(load, 15000); return () => window.clearInterval(timer); }, [load]);
  const openCount = tickets.filter((item) => openStatuses.includes(item.status)).length;
  const visibleTickets = useMemo(() => tickets
    .filter((item) => filter === "open" ? openStatuses.includes(item.status) : filter === "today" ? assignedToday(item.assigned_at) && !["REJECTED", "REASSIGNED", "UNABLE_TO_HANDLE"].includes(item.status) : item.status === "COMPLETED")
    .sort((left, right) => Date.parse(right.assigned_at) - Date.parse(left.assigned_at)), [filter, tickets]);
  const emptyMessage = filter === "today" ? "Không có công việc hôm nay" : filter === "completed" ? "Chưa có công việc đã xong" : "Không có công việc đang mở";

  return <RoleShell role="technician" title="Việc của tôi">
    <div className="technicianQueueFilters" role="group" aria-label="Lọc công việc">
      <button type="button" className={filter === "open" ? "active" : ""} aria-pressed={filter === "open"} onClick={() => setFilter("open")}>Đang mở ({openCount})</button>
      <button type="button" className={filter === "today" ? "active" : ""} aria-pressed={filter === "today"} onClick={() => setFilter("today")}>Hôm nay</button>
      <button type="button" className={filter === "completed" ? "active" : ""} aria-pressed={filter === "completed"} onClick={() => setFilter("completed")}>Đã xong</button>
    </div>
    {error && <div className="alert error">{error}</div>}
    <div className="technicianQueueList">{visibleTickets.map((item) => <Link href={`/technician/tickets/${item.id}`} className={`technicianJobCard priority-${item.ticket.priority || "P1"}`} key={item.id}>
      <header>{item.ticket.priority && <PriorityBadge priority={item.ticket.priority} />}<span className={`technicianJobStatus status-${item.status.toLowerCase()}`}>{statusLabels[item.status]}</span></header>
      <h2>{item.ticket.category_display_name || "Công việc bảo trì"}</h2>
      <p><MapPin size={14} />{item.ticket.location_label || "Chưa xác định"}</p>
      <span className="technicianJobProgress" aria-hidden="true"><i style={{ width: `${statusProgress[item.status]}%` }} /></span>
      <footer><span title={item.ticket.id}>#{formatTicketCode(item.ticket.id)}</span><strong><Clock3 size={13} />{formatDateTime(item.assigned_at)}</strong></footer>
    </Link>)}{visibleTickets.length === 0 && <div className="technicianQueueEmpty"><ClipboardCheck size={28} /><strong>{emptyMessage}</strong>{filter === "open" && <span></span>}</div>}</div>
  </RoleShell>;
}
