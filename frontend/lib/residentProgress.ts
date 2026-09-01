import type { ResidentTicket } from "../types/api";

export type ResidentFlowState = "done" | "current" | "future";
export type ResidentFlowStep = {
  key: string;
  label: string;
  note: string;
  time: string | null;
  state: ResidentFlowState;
};

export const RESIDENT_PROGRESS_STAGES = [
  "Mới",
  "Đã duyệt",
  "KTV đã nhận việc",
  "Đang xử lý",
  "Hoàn thành",
] as const;

const ASSIGNED_STAGE = "KTV đã nhận việc";

const stageNotes: Record<string, string> = {
  "Mới": "Bạn đã gửi phản ánh.",
  "Đã duyệt": "Ban quản lý đã tiếp nhận phản ánh.",
  [ASSIGNED_STAGE]: "Kỹ thuật viên đã được phân công xử lý.",
  "Đang xử lý": "Kỹ thuật viên đang xử lý sự cố.",
  "Hoàn thành": "Sự cố đã được xử lý xong.",
  "Đã hủy": "Phản ánh đã được hủy.",
  "Không xử lý được": "Ban quản lý không xử lý được sự cố này.",
  "Không hợp lệ": "Phản ánh chưa được tiếp nhận.",
  "Đã gộp phản ánh": "Sự cố này đã được báo và đang được xử lý.",
  "Chờ bổ sung thông tin": "Ban quản lý đang chờ thêm thông tin.",
};

const normalize = (value: string) => value.trim().toLocaleLowerCase("vi");

function canonicalStage(label: string) {
  const normalized = normalize(label);
  if (normalized === "đã gán kỹ thuật viên" || normalized === "có kỹ thuật viên") return ASSIGNED_STAGE;
  return label;
}

function reachedTechnicianAssignment(ticket: ResidentTicket, history: ResidentFlowStep[]) {
  const display = canonicalStage(ticket.display_status);
  return display === ASSIGNED_STAGE
    || display === "Đang xử lý"
    || display === "Hoàn thành"
    || Boolean(ticket.technician)
    || Boolean(ticket.expected_start_at)
    || history.some((step) => step.label === ASSIGNED_STAGE || step.label === "Đang xử lý" || step.label === "Hoàn thành");
}

/** Build the five resident-facing milestones from backend history.
 *
 * `ASSIGNED` has no separate technician acknowledgement action. The friendly
 * "KTV đã nhận việc" milestone represents the durable assignment that already
 * exists. Older timeline payloads do not contain that row, so it is inserted
 * before work starts without inventing a timestamp.
 */
export function buildResidentProgressSteps(ticket: ResidentTicket): ResidentFlowStep[] {
  const history = ticket.timeline.map((item, index): ResidentFlowStep => {
    const label = canonicalStage(item.display_status);
    return {
      key: `${item.created_at}-${index}`,
      label,
      note: item.reason?.trim() || stageNotes[label] || "",
      time: item.created_at,
      state: "done",
    };
  });

  if (!history.length) {
    history.push({ key: "created", label: "Mới", note: stageNotes["Mới"], time: ticket.created_at, state: "done" });
  }

  if (reachedTechnicianAssignment(ticket, history) && !history.some((step) => step.label === ASSIGNED_STAGE)) {
    const workStartedAt = history.findIndex((step) => step.label === "Đang xử lý" || step.label === "Hoàn thành");
    const assigned: ResidentFlowStep = {
      key: "assigned",
      label: ASSIGNED_STAGE,
      note: stageNotes[ASSIGNED_STAGE],
      time: null,
      state: "done",
    };
    if (workStartedAt >= 0) history.splice(workStartedAt, 0, assigned);
    else history.push(assigned);
  }

  history[history.length - 1].state = "current";

  // A finished or rejected report stops where it stopped. Active reports show
  // every remaining milestone, including technician assignment.
  if (ticket.lifecycle_group !== "ACTIVE") return history;
  const reached = RESIDENT_PROGRESS_STAGES.indexOf(history[history.length - 1].label as typeof RESIDENT_PROGRESS_STAGES[number]);
  if (reached < 0) return history;
  const ahead = RESIDENT_PROGRESS_STAGES.slice(reached + 1);
  return [
    ...history,
    ...ahead.map((label, index): ResidentFlowStep => ({
      key: `next-${label}`,
      label,
      note: index === ahead.length - 1 ? "Chưa hoàn thành" : "Chưa bắt đầu",
      time: null,
      state: "future",
    })),
  ];
}
