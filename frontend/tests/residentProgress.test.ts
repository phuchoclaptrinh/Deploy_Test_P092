import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { formatScheduleMoment } from "../lib/residentDate.ts";
import { buildResidentProgressSteps, RESIDENT_PROGRESS_STAGES } from "../lib/residentProgress.ts";
import type { ResidentTicket } from "../types/api.ts";

const ticket = (overrides: Partial<ResidentTicket> = {}): ResidentTicket => ({
  id: "ticket-1",
  display_code: "PA-000001",
  description: "Thang máy gặp sự cố",
  display_status: "Đã duyệt",
  category_display_name: "Thang máy",
  priority_description: "Cần xử lý sớm",
  progress_text: "Đã duyệt, đang phân công kỹ thuật viên",
  expected_start_at: null,
  location_label: "Thang máy · Tầng 4",
  reporter_name: "Cư dân",
  is_reporter: true,
  lifecycle_group: "ACTIVE",
  invalid_reason_text: null,
  created_at: "2026-09-01T10:22:00Z",
  updated_at: "2026-09-01T10:33:00Z",
  available_actions: [],
  attachments: [],
  timeline: [
    { display_status: "Mới", reason: null, created_at: "2026-09-01T10:22:00Z" },
    { display_status: "Đã duyệt", reason: null, created_at: "2026-09-01T10:33:00Z" },
  ],
  ...overrides,
});

test("resident progress always exposes five normal milestones", () => {
  const steps = buildResidentProgressSteps(ticket());
  assert.deepEqual(steps.map((step) => step.label), [...RESIDENT_PROGRESS_STAGES]);
  assert.deepEqual(steps.map((step) => step.state), ["done", "current", "future", "future", "future"]);
});

test("an assigned ticket advances to the KTV đã nhận việc milestone", () => {
  const steps = buildResidentProgressSteps(ticket({
    display_status: "Đã gán kỹ thuật viên",
    progress_text: "Đã có kỹ thuật viên, chờ tới lịch xử lý",
    expected_start_at: "2026-09-04T08:33:00Z",
    technician: { id: "technician-1", full_name: "KTV Demo1" },
  }));

  assert.deepEqual(steps.map((step) => step.label), [...RESIDENT_PROGRESS_STAGES]);
  assert.deepEqual(steps.map((step) => step.state), ["done", "done", "current", "future", "future"]);
  assert.equal(steps[2].note, "Kỹ thuật viên đã được phân công xử lý.");
  assert.equal(steps[2].time, null);
});

test("an assignment row from the backend is normalized without duplication", () => {
  const steps = buildResidentProgressSteps(ticket({
    display_status: "Đã gán kỹ thuật viên",
    technician: { id: "technician-1", full_name: "KTV Demo1" },
    timeline: [
      { display_status: "Mới", reason: null, created_at: "2026-09-01T10:22:00Z" },
      { display_status: "Đã duyệt", reason: null, created_at: "2026-09-01T10:33:00Z" },
      { display_status: "Đã gán kỹ thuật viên", reason: null, created_at: "2026-09-01T10:35:00Z" },
    ],
  }));

  assert.equal(steps.filter((step) => step.label === "KTV đã nhận việc").length, 1);
  assert.equal(steps[2].time, "2026-09-01T10:35:00Z");
});

test("the expected-start timestamp uses the resident-facing Vietnam format", () => {
  assert.equal(formatScheduleMoment("2026-08-28T05:17:00Z"), "12:17 · 28 Th8");

  const statusSource = readFileSync(new URL("../lib/residentStatus.ts", import.meta.url), "utf8");
  assert.ok(statusSource.includes("Dự kiến bắt đầu xử lý trước ${formatScheduleMoment(ticket.expected_start_at)}"));
});
