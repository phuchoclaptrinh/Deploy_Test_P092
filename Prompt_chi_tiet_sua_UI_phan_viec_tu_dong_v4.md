
## 3. Existing implementation to reuse

- The workspace already lives in `frontend/components/manager/AssignmentWorkspace.tsx`; `/manager/automation` redirects to `/manager?view=assignment`.
- Proposal API, service, job, and worker already exist. Extend them instead of creating a parallel flow.
- `GET /coordinator/assignment-jobs` and its cancel endpoint already exist, but the frontend does not consume them.
- `AssignmentProposalItem` already stores both `proposed_technician_*` and `final_technician_*`; preserve this distinction.
- `AssignmentProposalService.update_item()` does not currently guarantee that a manually selected technician is still active. `AssignmentService.assign()` has different existing rules; do not accidentally alter the existing manual-assignment behavior outside this scope.

## 4. Backend/API work

### 4.1 Prevent direct enabling of global auto-assignment

- In `AutoAssignmentSettingsService` and `PATCH /coordinator/auto-assignment-settings`, if the current state is `enabled=false` and the request attempts `enabled=true`, return `409` with a clear new code, such as `AUTO_ASSIGNMENT_PROPOSAL_REQUIRED`.
- Do not update the setting or its version when this request is rejected.
- `enabled=false` must remain valid so coordinators can disable global auto-assignment.
- `AssignmentProposalService.confirm_batch(... continue_auto_assignment=true ...)` is the only valid transition from disabled to enabled. Do not bypass the guard through an internal PATCH call.

### 4.2 Empty proposal batches and TTL

- `create_batch()` must still create a batch when no work item is eligible, allowing the coordinator to enable auto-assignment for future tickets through the proposal-first rule.
- For an empty batch, do not call the model with an empty request. Move the batch directly to `READY`, set `ready_at`/`expires_at` using the 600-second TTL, and keep global auto-assignment off until confirmation.
- An empty batch may be confirmed only with `continue_auto_assignment=true`; if there is no assignable row and the checkbox is false, return an actionable error or block it in the UI. A valid empty confirmation enables auto-assignment and creates no assignment or notification.
- Do not change the 20-ticket maximum, priority-descending/oldest-first sorting, or the rule forbidding an incident case from being split.

### 4.3 Allow coordinators to add an active technician to a proposal

- When PATCHing a proposal item with `technician_id`, lock/read the technician and require `is_active=true`. Do not require the technician to appear in the AI candidate snapshot; do not change that snapshot or the AI decision.
- If the technician is inactive or does not exist, reject with a clear domain error; do not update the item or batch version. Do not rely solely on frontend validation.
- At confirmation, verify `final_technician_id` is still active to prevent a state change after the coordinator edits the batch. If a technician is now inactive, create no assignment for that item; mark it `EMPTY`, store a sanitized reason, audit correctly, and still confirm/assign other valid items.
- Do not permit any proposal path to assign an inactive technician.
- Keep `assignment_source=AI_PROPOSAL_CONFIRMED`, use the coordinator as the audit actor, and preserve `proposed_technician_id` to distinguish the AI suggestion from the coordinator’s final choice.

### 4.4 Assignment-job response and cancellation

- Complete `AssignmentJobResponse`, its serializer/helper, and list endpoint so the frontend has at least: `id`, `mode`, `status`, `trigger`, work-item type/id, all `ticket_ids`, `execute_after`, `selected_technician_id` and display name when available, sanitized `decision_reason`, `error_code`, `created_at`, and `completed_at`. Do not expose raw model output, prompts, `error_detail`, stack traces, or unnecessary PII.
- Add or normalize frontend client functions for job listing and job cancellation, with matching TypeScript types.
- A coordinator may cancel a job only when it is `DIRECT`, triggered by `REASSIGN_REJECTED`, and in `SCHEDULED_GRACE` state (the P1/P2 intervention window). All other job/status combinations must return `409 INVALID_STATUS_TRANSITION`. Manual assignment must continue to win races as it does now.
- Do not create a migration for response/validation/service-only changes. If a migration is truly required, stop and state the exact reason before creating one.

## 5. Frontend work

Modify only what is necessary, primarily `frontend/components/manager/AssignmentWorkspace.tsx`, `frontend/api/backend.api.ts`, `frontend/types/api.ts`, and related CSS. Touch `manager/page.tsx` or the ticket detail panel only where wiring data/actions requires it.

### 5.1 Status bar and CTA

- When auto-assignment is off, show `Đang tắt`; the CTA is **Tạo bảng đề xuất để bật**. There must be no direct enable switch/button.
- When auto-assignment is on, show `Đang bật · <delay>` and the **Tắt tự động** CTA. Do not allow creating a proposal from this state.
- While a batch is `BUILDING` or `READY`, disable creation of another batch and show the existing batch only.

### 5.2 Dispatch queues

Replace the generic `Đã duyệt · chờ phân công` queue with these business-meaningful queues:

1. `Chờ duyệt phân loại`: `MANUAL_REVIEW` tickets; never mix these with assignment work.
2. `Cần phân tay`: approved, unassigned tickets while auto is off; `MANUAL_REQUIRED`; `auto_assignment_paused`; no-candidate/fallback failures; and tickets at the reassignment cap. Each row must open its ticket and show a concise reason where available.
3. `Tự động đang chờ/chạy`: jobs in `SCHEDULED_GRACE`, `PRIMARY_RUNNING`, or `FALLBACK_RUNNING`. Show ticket/case, trigger, scheduled time/countdown, and concise status.

- For a `SCHEDULED_GRACE` job triggered by `REASSIGN_REJECTED`, show **Hủy lượt AI và phân tay**. The action cancels the job, refreshes jobs/tickets, then opens the ticket for manual assignment. Do not show this cancel action for P3, initial-delay jobs, running jobs, or fallback jobs.
- Poll active jobs at a reasonable cadence (for example, every five seconds) and clean up timers on unmount; refresh relevant data after every mutation.
- Do not implement a P3 UI-blocking modal/banner.

### 5.3 Proposal board

- Preserve the existing `BUILDING` polling, ten-minute timer, optimistic version behavior, cancel flow, and history.
- Render work items/cases correctly: an item with multiple `ticket_ids` must display every member (code/location/priority from ticket cache/list or API response), not only the first ticket. Never assume an incident case contains one ticket.
- The technician selector/grouping has two clearly distinguished meanings:
  - The technician proposed by AI for the item: label `AI đề xuất`.
  - Other active technicians from the roster: label `BQL bổ sung`.
  - Never render inactive technicians.
- When the coordinator selects a technician other than the AI proposal, show `BQL bổ sung`/`BQL thay đổi` beside the row and show a short confirmation dialog explaining this is not an AI decision. Drag-and-drop and select controls must use the same validation and label semantics.
- For an empty batch, allow confirmation only when **Tiếp tục phân việc tự động cho ticket mới** is checked. The copy must explain: `Không có ticket đủ điều kiện để giao trong đợt này; hệ thống sẽ bật auto cho ticket mới.`
- The confirmation modal summarizes tickets about to be assigned, `EMPTY`/`DESELECTED`/`SKIPPED_MANUAL_WON` rows, the global setting after confirmation, and coordinator overrides, if any.
- Map `PROPOSAL_EXPIRED`, `PROPOSAL_NOT_READY`, version conflicts, active-assignment races, inactive technicians, and no-candidate conditions to clear Vietnamese actions. Never expose technical internals.

### 5.4 Accessibility and consistency

- Reuse the existing visual language (`ManagerSurface`, buttons, badges, alerts, and `tableAction`); do not redesign application navigation or unrelated layouts.
- Drag-and-drop must retain equivalent select/button controls for keyboard and touch users.
- Buttons need loading/disabled state, visible focus, correct labels/ARIA, and timers must not cause focus jumps.
- Use these consistent labels: `Tạo bảng đề xuất để bật`, `Duyệt và phân công`, `Tắt tự động`, `Cần phân tay`, `BQL bổ sung`.

## 6. Do not do any of the following

- Do not modify `src/agents` or the analysis/duplicate Agent logic.
- Do not restore duplicate disputes, resident actions, or a manager duplicate-dispute panel.
- Do not automatically turn off the global setting when an individual ticket/job fails.
- Do not turn `DIRECT` into a proposal or add coordinator approval after AI has made a valid `DIRECT` choice.
- Do not change the AI candidate business filter: active + available + matching skill. The active-only coordinator exception applies only to the final choice while editing a `PROPOSAL`.
- Do not implement P3 blocking UI in this iteration.
- Do not expose raw model responses in the API, UI, or normal logs.

## 7. Required tests and verification

Add/update tests in the existing structure. At minimum cover:

1. Directly enabling auto-assignment through PATCH while it is off returns `409 AUTO_ASSIGNMENT_PROPOSAL_REQUIRED`; disabling remains successful; a valid proposal confirmation is the only path that enables it.
2. An empty batch becomes `READY` without a model call; only confirmation with `continue_auto_assignment=true` enables auto-assignment and creates no assignment.
3. A proposal item accepts an active technician absent from the AI snapshot; audit preserves different proposed/final values.
4. Proposal item editing and confirmation reject inactive technicians; one stale-inactive item does not discard other valid items.
5. Job listing returns sufficient safe metadata for the UI; cancellation is allowed only for `DIRECT + REASSIGN_REJECTED + SCHEDULED_GRACE`.
6. UI/TypeScript: active roster only, case members are rendered, grace CTA appears only under the correct condition, and empty-batch confirmation requires continue-auto.

