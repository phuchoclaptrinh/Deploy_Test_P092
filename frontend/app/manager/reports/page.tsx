"use client";

import { AlertCircle, BarChart3, CheckCircle2, Clock3, Download, FileText, Gauge, Inbox, TableProperties, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getTechnicianProductivityReport, listCoordinatorTickets } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { ManagerStatCard } from "@/components/manager/DashboardWidgets";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { formatCategoryName } from "@/lib/category";
import { COMPLIANCE_PRIORITIES, PRIORITIES, PRIORITY_SLA_MINUTES, PRIORITY_SLA_TEXT } from "@/lib/risk";
import { categories } from "@/lib/mockService";
import type { Priority, Ticket } from "@/lib/types";
import type { CoordinatorTicket, TechnicianProductivityReport, TicketPriority } from "@/types/api";

type Period = "week" | "month";
type GroupBy = "category" | "priority";
/** Highest first, so the bar that matters is the one on the left.
 *
 *  This list used to be `["P3", "P2", "P1", "P0"]` -- the v1 scale, still here
 *  after the bands became P1-P5. Every P4 and P5 ticket fell into no bucket and
 *  simply vanished from the chart, while a permanently-empty P0 bar took up a
 *  fifth of the width. It is read from `lib/risk` now rather than restated, so
 *  the next change to the scale cannot leave a report behind again. */
const priorityOrder = [...PRIORITIES].reverse();

export default function ManagerReportsPage() {
  const [period, setPeriod] = useState<Period>("month");
  const [groupBy, setGroupBy] = useState<GroupBy>("category");
  const [backendTickets, setBackendTickets] = useState<CoordinatorTicket[]>([]);
  const [productivity, setProductivity] = useState<TechnicianProductivityReport | null>(null);
  useEffect(() => { listCoordinatorTickets().then((value) => setBackendTickets(value.items)); }, []);
  // §2.13: every column comes from the backend report; nothing is derived here.
  useEffect(() => { getTechnicianProductivityReport(period).then(setProductivity).catch(() => setProductivity(null)); }, [period]);
  const tickets = useMemo(() => {
    const cutoff = Date.now() - (period === "week" ? 7 : 30) * 86_400_000;
    return backendTickets.filter((ticket) => Date.parse(ticket.created_at) >= cutoff).map((ticket): Ticket => ({ id: ticket.id, title: ticket.description || "Phản ánh", description: ticket.description || "", unitId: ticket.source_unit_id, floor: ticket.location_label || "", locationType: ticket.location_label || "", category: formatCategoryName(ticket.category), priority: ticket.priority || "P1", score: ticket.risk_score, status: ({ NEW: "new", WAITING_RESIDENT_INFO: "needs_info", APPROVED: "approved", IN_PROGRESS: "in_progress", COMPLETED: "completed", UNRESOLVABLE: "cannot_resolve", INVALID: "invalid", CANCELLED: "cancelled" } as const)[ticket.status] || "new", createdAt: ticket.created_at, updatedAt: ticket.updated_at, dueAt: ticket.sla_due_at || undefined, residentName: "Cư dân", residentPhone: "", imageReadable: true, redFlag: false, timeline: [] }));
  }, [backendTickets, period]);
  const completed = tickets.filter((ticket) => ticket.status === "completed");
  const completedWithSla = completed.filter((ticket) => ticket.dueAt);
  const onTime = completedWithSla.filter((ticket) => Date.parse(ticket.updatedAt) <= Date.parse(ticket.dueAt!));
  const onTimeRate = completedWithSla.length ? Math.round((onTime.length / completedWithSla.length) * 100) : null;
  const overdue = tickets.filter((ticket) => ticket.dueAt && !["completed", "cancelled"].includes(ticket.status) && Date.parse(ticket.dueAt) < Date.now()).length;
  const averageMinutes = completed.length ? completed.reduce((sum, ticket) => sum + elapsedMinutes(ticket), 0) / completed.length : null;
  const reportCategories = [...new Set([...categories, ...tickets.map((ticket) => ticket.category)])];
  const categoryData = reportCategories.map((category) => ({ label: category, value: tickets.filter((ticket) => ticket.category === category).length }));
  const priorityData = priorityOrder.map((priority) => ({ label: priority, value: tickets.filter((ticket) => ticket.priority === priority).length }));

  return <RoleShell role="manager" title="Báo cáo" subtitle="Theo dõi khối lượng, thời gian và mức độ đáp ứng ticket.">
    <div className="managerPageStack">
      <section className="managerSummaryBar reportMetrics">
        <ManagerStatCard icon={<Inbox size={19} />} label="Tổng ticket" value={tickets.length} description={period === "week" ? "Trong 7 ngày" : "Trong 30 ngày"} tone="primary" />
        <ManagerStatCard icon={<CheckCircle2 size={19} />} label="Đúng hạn" value={onTimeRate == null ? "—" : `${onTimeRate}%`} description={completedWithSla.length ? `${onTime.length}/${completedWithSla.length} ticket` : "Chưa có dữ liệu"} tone="green" />
        <ManagerStatCard icon={<Clock3 size={19} />} label="Xử lý trung bình" value={averageMinutes == null ? "—" : formatDuration(averageMinutes)} description={`${completed.length} ticket hoàn thành`} tone="neutral" />
        <ManagerStatCard icon={<AlertCircle size={19} />} label="Đang quá hạn" value={overdue} description="Ticket chưa hoàn thành" tone="danger" />
      </section>
      <section className="reportChartGrid">
        <BarChart title="Ticket theo vấn đề" data={categoryData} />
        <BarChart title="Ticket theo độ ưu tiên" data={priorityData} priority />
      </section>
      <ManagerSurface title="Hiệu suất theo Priority" description="Thời gian thực tế so với thời gian cam kết." eyebrow="Hiệu suất xử lý" icon={<TableProperties size={19} />} actions={<span className="managerCountBadge">Ticket hoàn thành trong kỳ</span>} bodyClassName="managerSurfaceTableBody">
        <div className="tableWrap"><table className="dataTable reportTable"><thead><tr><th>Priority</th><th>Thời gian cam kết</th><th>Thực tế TB</th><th>Đúng hạn</th><th>Quá hạn</th></tr></thead><tbody>{COMPLIANCE_PRIORITIES.map((priority) => <SlaRow key={priority} priority={priority} tickets={completed.filter((ticket) => ticket.priority === priority)} />)}</tbody></table></div>
      </ManagerSurface>
      <ManagerSurface title="Năng suất kỹ thuật viên" description="Số liệu theo kỳ, lấy trực tiếp từ báo cáo backend." eyebrow="Bảng năng suất" icon={<Users size={19} />} actions={<span className="managerCountBadge">{productivity?.period === "week" ? "Tuần hiện tại" : "Tháng hiện tại"}</span>} bodyClassName="managerSurfaceTableBody">
        <div className="tableWrap"><table className="dataTable reportTable"><thead><tr><th>Kỹ thuật viên</th><th>Ngày hoạt động</th><th>Ticket đã xử lý</th><th>Trễ SLA</th><th>Nhận lại từ người khác</th></tr></thead><tbody>
          {(productivity?.rows || []).map((row) => <tr key={row.technician_id}><td>{row.full_name || row.technician_id.slice(0, 8)}</td><td>{row.active_days}</td><td>{row.completed_tickets}</td><td>{row.sla_late_tickets}</td><td>{row.reassigned_from_other_tickets}</td></tr>)}
          {!productivity?.rows.length && <tr><td colSpan={5}>Chưa có dữ liệu năng suất trong kỳ.</td></tr>}
        </tbody></table></div>
      </ManagerSurface>
      <ManagerSurface title="Tải dữ liệu tổng hợp" description="Chọn kỳ và cách nhóm dữ liệu trước khi xuất." eyebrow="Xuất báo cáo định kỳ" icon={<Download size={19} />} actions={<div className="reportExportControls"><label><span>Kỳ báo cáo</span><select value={period} onChange={(event) => setPeriod(event.target.value as Period)}><option value="week">7 ngày gần đây</option><option value="month">30 ngày gần đây</option></select></label><label><span>Nhóm theo</span><select value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}><option value="category">Loại vấn đề</option><option value="priority">Độ ưu tiên</option></select></label><button className="button secondary small" onClick={() => exportCsv(tickets, period, groupBy)}><Download size={15} />CSV</button><button className="button small" onClick={() => window.print()}><FileText size={15} />PDF</button></div>}><div className="managerExportNote"><Gauge size={18} /><span>Dữ liệu được tổng hợp theo bộ lọc kỳ báo cáo hiện tại.</span></div></ManagerSurface>
    </div>
  </RoleShell>;
}

function BarChart({ title, data, priority = false }: { title: string; data: { label: string; value: number }[]; priority?: boolean }) {
  const max = Math.max(1, ...data.map((item) => item.value));
  return <ManagerSurface className="reportChart" title={title} description="Phân bố ticket trong kỳ đang chọn." icon={<BarChart3 size={19} />} bodyClassName="managerChartBody"><div className={`reportBars ${priority ? "priorityBars" : ""}`}>{data.map((item) => <div className="reportBarItem" key={item.label} title={`${item.label}: ${item.value} ticket`}><strong>{item.value}</strong><div className={`reportBar ${priority ? `bar-${item.label}` : ""}`} style={{ height: `${Math.max(item.value ? 14 : 2, (item.value / max) * 100)}%` }} /><span>{shortLabel(item.label)}</span></div>)}</div></ManagerSurface>;
}

function SlaRow({ priority, tickets }: { priority: TicketPriority; tickets: Ticket[] }) {
  const average = tickets.length ? tickets.reduce((sum, ticket) => sum + elapsedMinutes(ticket), 0) / tickets.length : null;
  const limit = PRIORITY_SLA_MINUTES[priority];
  const onTime = tickets.filter((ticket) => elapsedMinutes(ticket) <= limit).length;
  const rate = tickets.length ? Math.round((onTime / tickets.length) * 100) : null;
  return <tr><td><strong>{priority}</strong></td><td>{PRIORITY_SLA_TEXT[priority]}</td><td>{average == null ? "Chưa có dữ liệu" : formatDuration(average)}</td><td>{rate == null ? "—" : `${rate}%`}</td><td>{tickets.length - onTime} ticket</td></tr>;
}

function elapsedMinutes(ticket: Ticket) { return Math.max(0, (Date.parse(ticket.updatedAt) - Date.parse(ticket.createdAt)) / 60_000); }
function shortLabel(label: string) { const labels: Record<string, string> = { "Rò nước": "Nước", "Chập điện": "Điện", "Hỏng khóa / cửa": "Khóa", "Điều hòa / thông gió": "Điều hòa", "Mất điện cục bộ": "Mất điện", "An ninh nghiêm trọng": "An ninh", "Mùi hôi / vệ sinh": "Vệ sinh", "Tiếng ồn": "Ồn" }; return labels[label] || label; }
function formatDuration(minutes: number) { if (minutes < 60) return `${Math.round(minutes)} phút`; const hours = Math.floor(minutes / 60); return `${hours}g ${Math.round(minutes % 60)}p`; }

function exportCsv(tickets: Ticket[], period: Period, groupBy: GroupBy) {
  const groups = new Map<string, Ticket[]>();
  tickets.forEach((ticket) => { const key = groupBy === "category" ? ticket.category : ticket.priority; groups.set(key, [...(groups.get(key) || []), ticket]); });
  const rows = [[groupBy === "category" ? "Loại vấn đề" : "Độ ưu tiên", "Tổng ticket", "Hoàn thành", "Đang xử lý", "Quá hạn"]];
  groups.forEach((items, key) => rows.push([key, String(items.length), String(items.filter((item) => item.status === "completed").length), String(items.filter((item) => item.status === "in_progress").length), String(items.filter((item) => item.dueAt && Date.parse(item.dueAt) < Date.now() && item.status !== "completed").length)]));
  const escape = (value: string) => `"${value.replaceAll('"', '""')}"`;
  const blob = new Blob(["\uFEFF", rows.map((row) => row.map(escape).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `bao-cao-${period}-${groupBy}-${new Date().toISOString().slice(0, 10)}.csv`; anchor.click(); URL.revokeObjectURL(url);
}
