"use client";

import { Bot, History, Repeat, UserCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { listAssignmentHistory } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { ManagerPagination } from "@/components/manager/ManagerPagination";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { assignmentErrorMessage, historyConfirmedBy, historyOrigin, historyRows } from "@/lib/assignment";
import { formatDateTime } from "@/lib/managerTicket";
import type { AssignmentHistoryRecord } from "@/types/api";

const PAGE_SIZE = 8;

/** Confirmed assignment rounds, as a view of their own.
 *
 *  A top-level page rather than a tab inside the assignment workspace: looking
 *  up what was assigned last Tuesday is a different job from assigning
 *  something now, and it must not require opening a draft screen to get to.
 *
 *  Everything rendered here comes from the snapshot the backend froze at
 *  confirmation. Nothing is read from the ticket, the category catalogue or a
 *  user profile as they stand today, so a record does not change when the world
 *  around it does.
 */
export default function AssignmentHistoryPage() {
  const [records, setRecords] = useState<AssignmentHistoryRecord[]>([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    listAssignmentHistory()
      .then(setRecords)
      .catch((reason) => setError(assignmentErrorMessage(reason, "Không tải được lịch sử phân việc.")))
      .finally(() => setLoading(false));
  }, []);

  const rows = historyRows(records);
  const visible = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return <RoleShell role="manager" title="Lịch sử phân việc" subtitle="Các đợt phân việc đã xác nhận, giữ nguyên trạng thái lúc chốt.">
    <ManagerSurface
      title="Đợt phân việc đã xác nhận"
      description="Bản ghi chỉ đọc. Đổi tên danh mục, đổi vị trí hay ngừng hoạt động kỹ thuật viên đều không làm thay đổi đợt đã chốt."
      icon={<History size={19} />}
    >
      {error && <div className="alert error" role="alert">{error}</div>}
      {loading
        ? <div className="emptyState"><div className="spinner" /><h3>Đang tải lịch sử...</h3></div>
        : rows.length === 0
          ? <div className="emptyState"><h3>Chưa có đợt phân việc nào</h3><p>Sau khi bạn xác nhận một bản nháp phân việc, đợt đó sẽ xuất hiện ở đây.</p></div>
          : <>
            <div className="assignHistory">
              {visible.map(({ record, followup, openedBySystem }) => <details key={record.batch_id}>
                <summary>
                  <strong>{formatDateTime(record.confirmed_at)}</strong>
                  <span>{record.ticket_count} ticket · {record.technician_count} kỹ thuật viên</span>
                  <span className="assignHistoryOrigin">
                    {openedBySystem ? <Bot size={13} /> : <UserCheck size={13} />}
                    {historyOrigin(record)} · Xác nhận bởi {historyConfirmedBy(record)}
                  </span>
                  <span className="assignHistoryRepeat"><Repeat size={13} />{followup}</span>
                </summary>

                {record.has_snapshot ? <div className="tableWrap">
                  <table className="dataTable">
                    <thead><tr><th>Ticket</th><th>Danh mục · Vị trí</th><th>Kỹ thuật viên đã nhận</th><th>AI đề xuất</th></tr></thead>
                    <tbody>
                      {record.items.map((item) => <tr key={item.item_id}>
                        <td data-label="Ticket">
                          {item.members.map((member) => <div className="assignHistoryTicket" key={member.ticket_id}>
                            <strong>{member.display_code || "—"}</strong>
                            {member.priority && <span className="assignHistoryPriority">{member.priority}</span>}
                          </div>)}
                        </td>
                        <td data-label="Danh mục · Vị trí">
                          {item.members.map((member) => <div key={member.ticket_id}>
                            {member.category || "Chưa có danh mục"} · {member.location_label || "Chưa xác định"}
                          </div>)}
                        </td>
                        <td data-label="Kỹ thuật viên đã nhận">{item.final_technician_name || "—"}</td>
                        <td data-label="AI đề xuất">
                          {item.proposed_technician_name || "—"}
                          {item.coordinator_override && <span className="badge managerTableStatus warning">BQL thay đổi</span>}
                        </td>
                      </tr>)}
                    </tbody>
                  </table>
                </div> : <p className="assignQueueEmpty">
                  Đợt này được xác nhận trước khi hệ thống lưu bản chụp, nên không hiển thị chi tiết ticket.
                  Dữ liệu hiện tại không được dùng để dựng lại — làm vậy sẽ hiển thị trạng thái hôm nay chứ không phải lúc chốt.
                </p>}
              </details>)}
            </div>
            <ManagerPagination page={page} pageSize={PAGE_SIZE} totalItems={rows.length} itemLabel="đợt" onPageChange={setPage} />
          </>}
    </ManagerSurface>
  </RoleShell>;
}
