"use client";

import { Check, ChevronDown } from "lucide-react";
import { useState } from "react";
import { formatShortDateTime } from "@/lib/residentDate";
import { buildResidentProgressSteps, type ResidentFlowStep } from "@/lib/residentProgress";
import { residentExpectedLabel } from "@/lib/residentStatus";
import type { ResidentTicket } from "@/types/api";

/** R-04 processing progress, collapsed by default.
 *
 *  Collapsed, it still answers the two questions a resident opens the screen
 *  for: where the report stands now, and when it should be handled by. Expanded,
 *  it shows the whole history plus the stages still ahead. The current step
 *  never moves between the two states — the history above and the stages below
 *  are what fold away. */
/** Dot colour for the step a report is sitting on. Everything else is neutral. */
function currentTone(label: string) {
  if (label === "Hoàn thành") return "positive";
  if (label === "Không hợp lệ" || label === "Không xử lý được") return "critical";
  if (label === "Đã hủy") return "neutral";
  return "accent";
}

export function ResidentProgress({ ticket }: { ticket: ResidentTicket }) {
  const [open, setOpen] = useState(false);
  const steps = buildResidentProgressSteps(ticket);
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
        <p className="rdProgressDue">{expected}</p>
      )}
    </section>
  );
}

function FlowRow({ step, last = false, tone, timePrefix = "" }: { step: ResidentFlowStep; last?: boolean; tone?: string; timePrefix?: string }) {
  return (
    <div className="rdFlowStep" data-state={step.state} data-tone={tone} data-last={last || undefined}>
      <span className="rdFlowDot" aria-hidden="true">{step.state === "done" && <Check size={11} strokeWidth={3.2} />}</span>
      <strong>{step.label}</strong>
      {step.note && <p>{step.note}</p>}
      {step.time && <time dateTime={step.time}>{timePrefix}{formatShortDateTime(step.time)}</time>}
    </div>
  );
}
