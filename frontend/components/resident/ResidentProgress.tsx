"use client";

import { Check, ChevronDown } from "lucide-react";
import { useState } from "react";
import { formatShortDateTime } from "@/lib/residentDate";
import { residentExpectedLabel } from "@/lib/residentStatus";
import type { ResidentTicket } from "@/types/api";

/** R-04 processing progress, collapsed by default.
 *
 *  Collapsed, it still answers the two questions a resident opens the screen
 *  for: where the report stands now, and when it should be handled by. Expanded,
 *  it shows the whole history plus the stages still ahead. The current step
 *  never moves between the two states — the history above and the stages below
 *  are what fold away. */
type FlowState = "done" | "current" | "future";
type FlowStep = { key: string; label: string; note: string; time: string | null; state: FlowState };

/** The stages a report normally passes through, in order. Used only to show
 *  what is still ahead; every completed step comes from the backend timeline. */
const stages = ["Mới", "Đã duyệt", "Đang xử lý", "Hoàn thành"];

const stageNotes: Record<string, string> = {
  "Mới": "Bạn đã gửi phản ánh.",
  "Đã duyệt": "Ban quản lý đã tiếp nhận phản ánh.",
  "Đang xử lý": "Kỹ thuật viên đang xử lý sự cố.",
  "Hoàn thành": "Sự cố đã được xử lý xong.",
  "Đã hủy": "Phản ánh đã được hủy.",
  "Không xử lý được": "Ban quản lý không xử lý được sự cố này.",
  "Không hợp lệ": "Phản ánh chưa được tiếp nhận.",
  "Đã gộp phản ánh": "Sự cố này đã được báo và đang được xử lý.",
  "Chờ bổ sung thông tin": "Ban quản lý đang chờ thêm thông tin.",
};

/** Dot colour for the step a report is sitting on. Everything else is neutral. */
function currentTone(label: string) {
  if (label === "Hoàn thành") return "positive";
  if (label === "Không hợp lệ" || label === "Không xử lý được") return "critical";
  if (label === "Đã hủy") return "neutral";
  return "accent";
}

function buildSteps(ticket: ResidentTicket): FlowStep[] {
  const history = ticket.timeline.map((item, index): FlowStep => ({
    key: `${item.created_at}-${index}`,
    label: item.display_status,
    note: item.reason?.trim() || stageNotes[item.display_status] || "",
    time: item.created_at,
    state: "done",
  }));
  if (!history.length) {
    history.push({ key: "created", label: "Mới", note: stageNotes["Mới"], time: ticket.created_at, state: "done" });
  }
  const current = history[history.length - 1];
  current.state = "current";

  // Only a report still being worked on has stages ahead of it. A finished or
  // rejected one stops where it stopped.
  if (ticket.lifecycle_group !== "ACTIVE") return history;
  const reached = stages.indexOf(current.label);
  if (reached < 0) return history;
  const ahead = stages.slice(reached + 1);
  return [
    ...history,
    ...ahead.map((label, index): FlowStep => ({
      key: `next-${label}`,
      label,
      note: index === ahead.length - 1 ? "Chưa hoàn thành" : "Chưa bắt đầu",
      time: null,
      state: "future",
    })),
  ];
}

export function ResidentProgress({ ticket }: { ticket: ResidentTicket }) {
  const [open, setOpen] = useState(false);
  const steps = buildSteps(ticket);
  const currentIndex = steps.findIndex((step) => step.state === "current");
  const current = steps[currentIndex];
  const past = steps.slice(0, currentIndex);
  const ahead = steps.slice(currentIndex + 1);
  const expected = residentExpectedLabel(ticket);

  return (
    <section className="rdCard rdProgress">
      <button className="rdProgressHead" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        <strong>Tiến trình xử lý</strong>
        <ChevronDown className="rdProgressChevron" size={18} aria-hidden="true" />
      </button>

      <div className="rdFlow">
        <div className="rdFlowFold" data-open={open}><div>{past.map((step) => <FlowRow step={step} key={step.key} />)}</div></div>
        <FlowRow step={current} last={!open && !ahead.length} tone={currentTone(current.label)} timePrefix={open ? "" : "Cập nhật lúc "} />
        <div className="rdFlowFold" data-open={open}><div>{ahead.map((step, index) => <FlowRow step={step} last={index === ahead.length - 1} key={step.key} />)}</div></div>
      </div>

      {expected && (
        <p className="rdProgressDue">Dự kiến xử lý {expected.charAt(0).toLocaleLowerCase("vi") + expected.slice(1)}</p>
      )}
    </section>
  );
}

function FlowRow({ step, last = false, tone, timePrefix = "" }: { step: FlowStep; last?: boolean; tone?: string; timePrefix?: string }) {
  return (
    <div className="rdFlowStep" data-state={step.state} data-tone={tone} data-last={last || undefined}>
      <span className="rdFlowDot" aria-hidden="true">{step.state === "done" && <Check size={11} strokeWidth={3.2} />}</span>
      <strong>{step.label}</strong>
      {step.note && <p>{step.note}</p>}
      {step.time && <time dateTime={step.time}>{timePrefix}{formatShortDateTime(step.time)}</time>}
    </div>
  );
}
