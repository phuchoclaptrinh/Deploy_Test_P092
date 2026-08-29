"use client";

import Link from "next/link";
import { AlertTriangle, ClipboardCheck, MapPin, PlayCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getTechnicianQueue, listTechnicianAssignments } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { PriorityBadge } from "@/components/StatusBadge";
import { formatTicketCode } from "@/lib/display";
import type { TechnicianAssignment, TechnicianQueue } from "@/types/api";

type QueueFilter = "open" | "today" | "completed";

const statusLabels: Record<TechnicianAssignment["status"], string> = {
  ASSIGNED: "Đã gán",
  IN_PROGRESS: "Đang xử lý",
  COMPLETED: "Đã xong",
  REJECTED: "Đã từ chối",
  REASSIGNED: "Đã phân lại",
  UNABLE_TO_HANDLE: "Không thể xử lý",
};

/** §4: position in the planned order, as a label. Item 0 is what to do now. */
const orderLabel = (index: number) => (index === 0 ? "Làm ngay" : index === 1 ? "Tiếp theo" : `Thứ ${index + 1}`);

function assignedToday(assignedAt: string) {
  const assigned = new Date(assignedAt);
  const today = new Date();
  return assigned.getFullYear() === today.getFullYear() && assigned.getMonth() === today.getMonth() && assigned.getDate() === today.getDate();
}

const startLabel = (value: string | null | undefined) => {
  if (!value) return null;
  const start = new Date(value);
  const today = new Date();
  const sameDay = start.toDateString() === today.toDateString();
  const time = start.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
  return sameDay ? `Bắt đầu ~${time}` : `Bắt đầu ~${time} ${start.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" })}`;
};

export default function TechnicianQueuePage() {
  const [queue, setQueue] = useState<TechnicianQueue | null>(null);
  const [history, setHistory] = useState<TechnicianAssignment[]>([]);
  const [filter, setFilter] = useState<QueueFilter>("open");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      // Two calls with different jobs: the queue is the *ordered* live work
      // (§4), the flat list is everything else the other two tabs show. Sorting
      // the flat list by hand would put this screen's idea of "what to do now"
      // next to the scheduler's, and the two would drift.
      const [nextQueue, all] = await Promise.all([getTechnicianQueue(), listTechnicianAssignments()]);
      setQueue(nextQueue);
      setHistory(all);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được công việc.");
    }
  }, []);
  useEffect(() => { load(); const timer = window.setInterval(load, 15000); return () => window.clearInterval(timer); }, [load]);

  const ordered = queue?.items || [];
  const openCount = ordered.length;
  const visibleTickets = useMemo(() => {
    if (filter === "open") return ordered;
    if (filter === "today") {
      return history
        .filter((item) => assignedToday(item.assigned_at) && !["REJECTED", "REASSIGNED", "UNABLE_TO_HANDLE"].includes(item.status))
        .sort((left, right) => Date.parse(right.assigned_at) - Date.parse(left.assigned_at));
    }
    return history
      .filter((item) => item.status === "COMPLETED")
      .sort((left, right) => Date.parse(right.assigned_at) - Date.parse(left.assigned_at));
  }, [filter, history, ordered]);

  const emptyMessage = filter === "today" ? "Không có công việc hôm nay" : filter === "completed" ? "Chưa có công việc đã xong" : "Không có công việc đang mở";

  return <RoleShell role="technician" title="Việc của tôi">
    <div className="technicianQueueFilters" role="group" aria-label="Lọc công việc">
      <button type="button" className={filter === "open" ? "active" : ""} aria-pressed={filter === "open"} onClick={() => setFilter("open")}>Đang mở ({openCount})</button>
      <button type="button" className={filter === "today" ? "active" : ""} aria-pressed={filter === "today"} onClick={() => setFilter("today")}>Hôm nay</button>
      <button type="button" className={filter === "completed" ? "active" : ""} aria-pressed={filter === "completed"} onClick={() => setFilter("completed")}>Đã xong</button>
    </div>
    {error && <div className="alert error">{error}</div>}
    {filter === "open" && queue && !queue.within_working_shift && (
      <div className="alert warning">Ngoài ca làm việc (08:00–18:00). Giờ bắt đầu dự kiến tính từ đầu ca tiếp theo.</div>
    )}
    <div className="technicianQueueList">{visibleTickets.map((item, index) => {
      const start = filter === "open" ? startLabel(item.planned_start_at) : null;
      const atRisk = item.risk_state === "AT_RISK" || (item.slack_seconds ?? 0) < 0;
      const p3Urgent = item.ticket.priority === "P3" && ["ASSIGNED", "IN_PROGRESS"].includes(item.status);
      return <Link href={`/technician/tickets/${item.id}`} className={`technicianJobCard priority-${item.ticket.priority || "P1"}${filter === "open" && index === 0 ? " techJobNow" : ""}${p3Urgent ? " techP3Urgent" : ""}`} key={item.id}>
        <header>
          {filter === "open" && <span className={`techOrderBadge${index === 0 ? " now" : ""}`}>{orderLabel(index)}</span>}
          {item.ticket.priority && <PriorityBadge priority={item.ticket.priority} />}
          <span className={`technicianJobStatus status-${item.status.toLowerCase()}`}>{statusLabels[item.status]}</span>
        </header>
        <h2>{item.ticket.category_display_name || "Công việc bảo trì"}</h2>
        <p><MapPin size={14} />{item.ticket.location_label || "Chưa xác định"}</p>
        {/* §4: an estimated start, and a risk warning. Deliberately no progress
            bar towards a completion time -- there is no longer one to move
            towards, and a bar implies otherwise. No acceptance countdown
            either: there is nothing left to acknowledge, only work to start. */}
        {start && <p className="techPlannedStart"><PlayCircle size={14} />{start}</p>}
        {atRisk && filter === "open" && (
          <p className="techRiskWarning"><AlertTriangle size={14} />Lịch đang trễ so với dự kiến</p>
        )}
        <footer>
          <span title={item.ticket.id}>#{formatTicketCode(item.ticket.id)}</span>
        </footer>
      </Link>;
    })}{visibleTickets.length === 0 && <div className="technicianQueueEmpty"><ClipboardCheck size={28} /><strong>{emptyMessage}</strong>{filter === "open" && <span></span>}</div>}</div>
  </RoleShell>;
}
