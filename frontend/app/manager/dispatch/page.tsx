"use client";

import { AlertTriangle, Bot, CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { listAtRiskDecisions, listDispatchEvents } from "@/api/backend.api";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { RoleShell } from "@/components/RoleShell";
import { formatDateTime } from "@/lib/managerTicket";
import { atRiskNeedsAttention, decisionSourceLabel, slackLabel } from "@/lib/visualAssignment";
import type { AtRiskDecision, DispatchEvent } from "@/types/api";

const STATUS_LABELS: Record<string, string> = {
  PENDING: "Đang chờ",
  CLAIMED: "Đang xử lý",
  ASSIGNED: "Đã phân công",
  ESCALATED: "Chuyển Ban quản lý",
  SUPERSEDED: "Đã có người phân tay",
  FAILED: "Lỗi kỹ thuật",
};

const ESCALATION_LABELS: Record<string, string> = {
  NO_ELIGIBLE_TECHNICIAN: "Không có kỹ thuật viên đủ điều kiện",
  NO_FEASIBLE_PLACEMENT: "Không xếp được vào lịch của ai",
  AUTO_ASSIGNMENT_DISABLED: "Phân việc tự động đã tắt",
  TICKET_NOT_ELIGIBLE: "Phản ánh không còn đủ điều kiện",
  P5_EMERGENCY: "Phản ánh khẩn cấp P5",
};

/** §10: what Automatic Assignment did, for the people who have to answer for it.
 *
 *  Two lists rather than one filtered table, because they answer different
 *  questions. The at-risk list is the one a manager reviews: every row on it is
 *  a trade-off that was made on their behalf, and the `decision_source` column
 *  says whether an agent actually reasoned about it or whether it timed out and
 *  the least-late option was taken instead. The queue below is the operational
 *  view -- what is waiting, what escalated, and why.
 */
export default function ManagerDispatchPage() {
  const [decisions, setDecisions] = useState<AtRiskDecision[]>([]);
  const [events, setEvents] = useState<DispatchEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const [nextDecisions, nextEvents] = await Promise.all([listAtRiskDecisions(), listDispatchEvents()]);
      setDecisions(nextDecisions);
      setEvents(nextEvents);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được dữ liệu phân việc tự động.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);
  useEffect(() => { void load(true); const timer = window.setInterval(() => void load(), 20000); return () => window.clearInterval(timer); }, [load]);

  const escalated = events.filter((event) => event.status === "ESCALATED");

  return (
    <RoleShell role="manager" title="Phân việc tự động">
      <ManagerSurface
        title="Quyết định có rủi ro trễ lịch"
        description="Những phản ánh hệ thống vẫn phân công dù lịch của kỹ thuật viên bị ảnh hưởng."
        actions={<button type="button" className="button secondary" onClick={() => void load(true)}><RefreshCw size={16} />Tải lại</button>}
      >
        {error && <div className="alert error">{error}</div>}
        {loading ? (
          <div className="mdCardState"><div className="spinner" /><h3>Đang tải...</h3></div>
        ) : decisions.length === 0 ? (
          <div className="mdCardState"><CheckCircle2 size={26} /><h3>Chưa có quyết định rủi ro nào</h3><p>Mọi phản ánh đều được xếp lịch an toàn.</p></div>
        ) : (
          <div className="mdTableScroll">
            <table className="mdTable">
              <thead><tr><th>Phản ánh</th><th>Kỹ thuật viên</th><th>Nguồn quyết định</th><th>Mức trễ</th><th>Lý do</th><th>Thời điểm</th></tr></thead>
              <tbody>
                {decisions.map((decision) => (
                  <tr key={decision.id} className={atRiskNeedsAttention(decision) ? "mdRow needsAttention" : "mdRow"}>
                    <td data-label="Phản ánh">{decision.ticket_display_code}</td>
                    <td data-label="Kỹ thuật viên">{decision.technician_name || "—"}</td>
                    <td data-label="Nguồn quyết định">
                      <span className={`mdPill ${decision.decision_source === "AGENT" ? "processing" : "warning"}`}>
                        {decision.decision_source === "AGENT" ? <Bot size={12} /> : <ShieldAlert size={12} />}
                        {decisionSourceLabel(decision.decision_source)}
                      </span>
                    </td>
                    <td data-label="Mức trễ">{slackLabel(decision.slack_seconds)}</td>
                    <td data-label="Lý do">{decision.reason || "—"}</td>
                    <td data-label="Thời điểm">{formatDateTime(decision.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ManagerSurface>

      <ManagerSurface
        title="Hàng đợi phân việc tự động"
        description={escalated.length ? `${escalated.length} phản ánh đang chờ Ban quản lý xử lý thủ công.` : "Không có phản ánh nào cần xử lý thủ công."}
      >
        {events.length === 0 ? (
          <div className="mdCardState"><h3>Hàng đợi trống</h3></div>
        ) : (
          <div className="mdTableScroll">
            <table className="mdTable">
              <thead><tr><th>Phản ánh</th><th>Ưu tiên</th><th>Trạng thái</th><th>Kỹ thuật viên</th><th>Bắt đầu dự kiến</th><th>Ghi chú</th></tr></thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id} className="mdRow">
                    <td data-label="Phản ánh">{event.ticket_display_code}</td>
                    <td data-label="Ưu tiên"><span className={`mdPriority mdPriority-${event.priority}`}><i aria-hidden="true" />{event.priority}</span></td>
                    <td data-label="Trạng thái">
                      <span className={`mdPill ${event.status === "ESCALATED" ? "danger" : event.status === "ASSIGNED" ? "success" : "neutral"}`}>
                        {STATUS_LABELS[event.status] || event.status}
                      </span>
                    </td>
                    <td data-label="Kỹ thuật viên">{event.selected_technician_name || "—"}</td>
                    <td data-label="Bắt đầu dự kiến">{event.planned_start_at ? formatDateTime(event.planned_start_at) : "—"}</td>
                    <td data-label="Ghi chú">
                      {event.escalation_reason
                        ? <span className="techRiskWarning"><AlertTriangle size={13} />{ESCALATION_LABELS[event.escalation_reason] || event.escalation_reason}</span>
                        : event.risk_state === "AT_RISK" ? "Có rủi ro trễ lịch" : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ManagerSurface>
    </RoleShell>
  );
}
