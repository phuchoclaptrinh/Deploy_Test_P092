"use client";

import { useSearchParams } from "next/navigation";
import { LayoutGrid, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listCoordinatorTickets } from "@/api/backend.api";
import { AutoAssignmentControl } from "@/components/manager/AutoAssignmentControl";
import { VisualAssignmentBoard } from "@/components/manager/VisualAssignmentBoard";
import { ManagerPagination } from "@/components/manager/ManagerPagination";
import { TicketDetailPanel } from "@/components/manager/TicketDetailPanel";
import { RoleShell } from "@/components/RoleShell";
import { formatCategoryName } from "@/lib/category";
import { PRIORITIES, formatRiskScore } from "@/lib/risk";
import { formatTicketCode } from "@/lib/display";
import { assignmentStatusDisplay } from "@/lib/assignmentStatus";
import { formatClock, formatDay, formatDateTime } from "@/lib/managerTicket";
import { getSeenManagerTickets } from "@/lib/managerTicketSeen";
import { EMERGENCY_PENDING_LABEL, isEmergencyReviewPending } from "@/lib/emergencyReview";
import type { CoordinatorTicket } from "@/types/api";

const PAGE_SIZE = 8;
const PANEL_MIN = 380;
const PANEL_MAX = 680;
const PANEL_STORAGE_KEY = "fixit-manager-detail-width";
const labels: Record<string, string> = { NEW: "Mới", WAITING_RESIDENT_INFO: "Chờ cư dân", APPROVED: "Đã duyệt", IN_PROGRESS: "Đang xử lý", COMPLETED: "Hoàn thành", UNRESOLVABLE: "Không xử lý được", CANCELLED: "Đã hủy", INVALID: "Không hợp lệ", LINKED_DUPLICATE: "Đã gộp trùng" };

export default function ManagerDashboard() {
  const searchParams = useSearchParams();
  const [items, setItems] = useState<CoordinatorTicket[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [priority, setPriority] = useState("all");
  const [status, setStatus] = useState("all");
  const [period, setPeriod] = useState("all");
  const [page, setPage] = useState(1);
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [panelWidth, setPanelWidth] = useState(460);
  const shellRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      setItems((await listCoordinatorTickets()).items);
      setSeen(getSeenManagerTickets());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được ticket.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);
  useEffect(() => { void load(true); const timer = window.setInterval(() => { void load(); }, 15000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => {
    const stored = Number(window.localStorage.getItem(PANEL_STORAGE_KEY));
    if (stored >= PANEL_MIN && stored <= PANEL_MAX) setPanelWidth(stored);
  }, []);

  const view = searchParams.get("view");
  // /manager/automation redirects here, so the workspace opens straight away.
  useEffect(() => { if (view === "assignment") setWorkspaceOpen(true); }, [view]);
  const isNew = (ticket: CoordinatorTicket) => ticket.status === "NEW" && ticket.classification_status === "RESOLVED" && !seen.has(ticket.id);
  const filtered = useMemo(() => items.filter((ticket) => {
    // The visible code stays searchable even though the ID column is gone.
    const haystack = `${ticket.id} ${formatTicketCode(ticket.id)} ${ticket.location_label || ""} ${ticket.description || ""}`.toLowerCase();
    const matchesQuery = haystack.includes(query.trim().toLowerCase());
    const matchesCategory = category === "all" || ticket.category === category;
    const matchesPriority = priority === "all" || ticket.priority === priority;
    const matchesStatus = status === "all" || (status === "MANUAL_REVIEW" ? ticket.classification_status === "MANUAL_REVIEW" : ticket.status === status);
    const createdAt = Date.parse(ticket.created_at);
    const matchesPeriod = period === "all" || createdAt >= Date.now() - (period === "day" ? 86_400_000 : 7 * 86_400_000);
    // A notification opens the "new" view. Once approved, the ticket is no
    // longer new, but it still needs the coordinator's next action (assigning
    // a technician), so keep it visible until an active assignment exists.
    const matchesView = view === "new"
      ? isNew(ticket) || (ticket.status === "APPROVED" && !ticket.active_assignment_id)
      : view === "processing" ? ["APPROVED", "IN_PROGRESS"].includes(ticket.status) : view === "overdue" ? isScheduleAtRisk(ticket) : view === "completed" ? ticket.status === "COMPLETED" : true;
    return matchesQuery && matchesCategory && matchesPriority && matchesStatus && matchesPeriod && matchesView;
  }).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)), [category, items, period, priority, query, seen, status, view]);
  useEffect(() => setPage(1), [category, period, query, priority, status, view]);
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const categories = [...new Set(items.map((ticket) => ticket.category).filter(Boolean))] as string[];

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const resize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current || !shellRef.current) return;
    const next = shellRef.current.getBoundingClientRect().right - event.clientX;
    setPanelWidth(Math.min(PANEL_MAX, Math.max(PANEL_MIN, next)));
  };
  const endResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
    window.localStorage.setItem(PANEL_STORAGE_KEY, String(Math.round(panelWidth)));
  };
  const resizeByKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === "ArrowLeft" ? 24 : event.key === "ArrowRight" ? -24 : 0;
    if (!step) return;
    event.preventDefault();
    setPanelWidth((value) => {
      const next = Math.min(PANEL_MAX, Math.max(PANEL_MIN, value + step));
      window.localStorage.setItem(PANEL_STORAGE_KEY, String(Math.round(next)));
      return next;
    });
  };

  const header = <section className="managerDashboardHeader">
    <div><h1>Điều phối ticket</h1><span>Theo dõi, ưu tiên và phân việc trong cùng một màn hình.</span></div>
  </section>;

  return <RoleShell role="manager" title="Điều phối ticket" managerHeader={header}>
    <div
      className={`ticketMasterDetail${selectedTicketId ? " detailOpen" : ""}${workspaceOpen ? " assignmentOpen" : ""}`}
      ref={shellRef}
      style={selectedTicketId ? ({ "--managerDetailWidth": `${panelWidth}px` } as React.CSSProperties) : undefined}
    >
      <div className="ticketListColumn">
        {/* State 2/3 replaces the dashboard table outright rather than sitting
            above it, so the coordinator only ever has one job on screen. */}
        {workspaceOpen ? <VisualAssignmentBoard
          onOpenTicket={setSelectedTicketId}
          onClose={() => setWorkspaceOpen(false)}
          onAssignmentsChanged={() => void load()}
        /> : <div className="mdBoard">
          {/* Title, search and the two actions sit above the table card rather
              than inside it, so the card holds nothing but the rows. */}
          <div className="mdBoardHead">
            <div className="mdBoardTitle"><h2>Danh sách ticket</h2><span>{filtered.length} ticket</span></div>
            <label className="mdSearch"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm mã ticket, vị trí, mô tả..." /></label>
            {/* §1 and §2 are two different workflows and get two buttons.
                The old single "Phân việc tự động" entry point opened a proposal
                workspace that did both jobs badly: it was the only way to place
                work by hand *and* the only way to turn automation on. */}
            <button type="button" className="button secondary" onClick={() => setWorkspaceOpen(true)}><LayoutGrid size={17} />Phân việc trực quan</button>
            <AutoAssignmentControl onChanged={() => void load()} />
          </div>
          <div className="mdFilters">
            <select aria-label="Lọc danh mục" className="mdFilter" value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">Danh mục</option>{categories.map((item) => <option value={item} key={item}>{formatCategoryName(item)}</option>)}</select>
            <select aria-label="Lọc mức ưu tiên" className="mdFilter" value={priority} onChange={(event) => setPriority(event.target.value)}><option value="all">Mức ưu tiên</option>{[...PRIORITIES].reverse().map((band) => <option value={band} key={band}>{band}</option>)}</select>
            <select aria-label="Lọc trạng thái" className="mdFilter" value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">Trạng thái</option><option value="MANUAL_REVIEW">Chờ duyệt thủ công</option>{Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
            <select aria-label="Lọc khoảng thời gian" className="mdFilter" value={period} onChange={(event) => setPeriod(event.target.value)}><option value="all">Khoảng thời gian</option><option value="day">24 giờ qua</option><option value="week">7 ngày qua</option></select>
          </div>
          {error && <div className="alert error mdAlert">{error}</div>}
          {loading ? <section className="mdCard mdCardState"><div className="spinner" /><h3>Đang tải ticket...</h3></section>
            : filtered.length === 0 ? <section className="mdCard mdCardState"><h3>Không có ticket phù hợp</h3><p>Ticket mới và ticket chờ phân công sẽ xuất hiện tại đây.</p></section>
              : <section className="mdCard">
                <div className="mdTableScroll">
                  <table className="mdTable">
                    <thead><tr><th>Thời gian gửi</th><th>Vị trí</th><th>Danh mục</th><th>Ưu tiên</th><th>Điểm</th><th>Trạng thái</th><th>Bắt đầu dự kiến</th><th>KTV</th></tr></thead>
                    <tbody>{visible.map((ticket) => {
                      const displayStatus = managerTicketDisplayStatus(ticket);
                      const manualReview = ticket.classification_status === "MANUAL_REVIEW";
                      const selected = selectedTicketId === ticket.id;
                      const unseen = isNew(ticket);
                      const atRisk = isScheduleAtRisk(ticket);
                      const emergencyUrgent = isEmergencyReviewPending(ticket);
                      return <tr
                        key={ticket.id}
                        tabIndex={0}
                        role="button"
                        aria-pressed={selected}
                        aria-label={`Mở chi tiết ${formatTicketCode(ticket.id)}${emergencyUrgent ? " - chờ duyệt khẩn cấp P5" : ""}`}
                        onClick={() => setSelectedTicketId(ticket.id)}
                        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedTicketId(ticket.id); } }}
                        className={`mdRow mdRow-${manualReview ? "P0" : ticket.priority || "P1"}${selected ? " selected" : ""}${unseen ? " unseen" : ""}${emergencyUrgent ? " emergencyUrgent" : ""}`}
                      >
                        {/* An unseen report is marked by the dot on the clock,
                            not a badge: the row keeps one reading rhythm. */}
                        <td data-label="Thời gian gửi"><span className="mdWhen">{unseen && !emergencyUrgent && <i className="mdNewDot" aria-hidden="true" />}{formatClock(ticket.created_at)}</span><small>{formatDay(ticket.created_at)}</small></td>
                        <td data-label="Vị trí">{ticket.location_label || "Chưa xác định"}</td>
                        <td data-label="Danh mục">{formatCategoryName(ticket.category)}</td>
                        <td data-label="Ưu tiên">{ticket.priority
                          ? <span className={`mdPriority mdPriority-${ticket.priority}`}><i aria-hidden="true" />{ticket.priority}</span>
                          : manualReview ? <span className="mdPill warning">Chờ duyệt</span> : <span className="mdDash">—</span>}</td>
                        <td data-label="Điểm">{managerTicketScore(ticket)}</td>
                        <td data-label="Trạng thái"><span className={`mdPill ${displayStatus.tone}`}>{displayStatus.label}</span></td>
                        {/* §4: the one operational time on this row is when the
                            technician is expected to *start*. The internal
                            completion estimate lives in the detail panel, and no
                            acceptance deadline exists any more. */}
                        <td data-label="Bắt đầu dự kiến"><span className={atRisk ? "mdOverdue" : undefined}>{ticket.planned_start_at ? formatDateTime(ticket.planned_start_at) : "—"}{atRisk && !emergencyUrgent && <i className="mdRiskDot" aria-label="Lịch đang trễ" title="Lịch đang trễ so với cam kết" />}</span></td>
                        <td data-label="KTV">{managerTechnician(ticket)}</td>
                      </tr>;
                    })}</tbody>
                  </table>
                </div>
                <ManagerPagination compact page={page} pageSize={PAGE_SIZE} totalItems={filtered.length} itemLabel="ticket" onPageChange={setPage} />
              </section>}
        </div>}
      </div>
      {selectedTicketId && <div
        className="ticketResizeHandle"
        role="separator"
        aria-orientation="vertical"
        aria-label="Kéo để đổi bề rộng bảng chi tiết"
        tabIndex={0}
        onPointerDown={startResize}
        onPointerMove={resize}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        onKeyDown={resizeByKey}
      />}
      {selectedTicketId && <TicketDetailPanel ticketId={selectedTicketId} onClose={() => setSelectedTicketId(null)} onUpdated={() => void load()} />}
    </div>
  </RoleShell>;
}

function managerTableStatusTone(status: string) {
  if (["APPROVED", "COMPLETED"].includes(status)) return "success";
  if (status === "WAITING_RESIDENT_INFO") return "warning";
  if (["UNRESOLVABLE", "INVALID"].includes(status)) return "danger";
  if (status === "IN_PROGRESS") return "processing";
  return "neutral";
}

function managerTicketDisplayStatus(ticket: CoordinatorTicket) {
  // Checked before the generic manual-review label: an emergency waiting at
  // the emergency gate is in MANUAL_REVIEW too, and "chờ duyệt thủ công" would send a
  // coordinator looking for a classification form that is not offered for it.
  if (isEmergencyReviewPending(ticket)) return { label: EMERGENCY_PENDING_LABEL, tone: "danger" };
  if (ticket.classification_status === "MANUAL_REVIEW") return { label: "Chờ duyệt thủ công", tone: "warning" };
  if (["PENDING", "PROCESSING"].includes(ticket.classification_status)) return { label: "Đang phân tích", tone: "processing" };
  if (ticket.status === "LINKED_DUPLICATE") return { label: "Đã gộp trùng", tone: "neutral" };
  const assignment = assignmentStatusDisplay(ticket.active_assignment_status);
  if (assignment) return assignment;
  return { label: labels[ticket.status] || "Chưa xác định", tone: managerTableStatusTone(ticket.status) };
}

/** The scheduler could not fit this ticket without breaking a commitment, or a
 *  later re-plan pushed it there. `slack_seconds < 0` is the same statement as
 *  AT_RISK -- the backend writes both, and reading either keeps the row honest
 *  if one of them was written by an older path. */
function isScheduleAtRisk(ticket: CoordinatorTicket) {
  if (["COMPLETED", "CANCELLED", "INVALID", "UNRESOLVABLE"].includes(ticket.status)) return false;
  return ticket.assignment_risk_state === "AT_RISK" || (ticket.slack_seconds ?? 0) < 0;
}

function managerTechnician(ticket: CoordinatorTicket) {
  if (ticket.completed_technician_name) return ticket.completed_technician_name;
  if (!ticket.active_assignment_id) return "Chưa gán";
  return ticket.active_technician_name || "Kỹ thuật viên";
}

function managerTicketScore(ticket: CoordinatorTicket) {
  if (ticket.risk_score != null) return formatRiskScore(ticket.risk_score);
  if (["PENDING", "PROCESSING", "MANUAL_REVIEW"].includes(ticket.classification_status)) return "Chờ tính";
  return "Chưa có";
}
