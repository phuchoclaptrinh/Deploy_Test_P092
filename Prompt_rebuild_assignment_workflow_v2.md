# Implementation Prompt — Rebuild the Assignment Workflow UI

This prompt supersedes the previous assignment-workflow UI prompt. Implement the workflow exactly as defined below. Do not retain a UI state merely because it exists today if it conflicts with this specification.

## 0. Product decision and scope

The current assignment workspace jumps directly into a `BUILDING` proposal and leaves the coordinator with an empty-looking screen. Rebuild it around a deliberate preparation step and a draft-first assignment experience.

The product now has two different automation concepts. Do not conflate them:

1. **DIRECT assignment** is the existing V4 backend flow for a ticket that is eligible for direct AI assignment or reassignment. Keep its backend rules unchanged.
2. **Recurring proposal schedule** is a new, coordinator-facing feature defined in this prompt. It periodically creates a new *draft proposal for review*; it never directly assigns tickets. Its label must therefore be truthful: `Lặp lại mỗi …`.

The recurring proposal schedule is an explicit product change beyond the prior V4 delay-only setting. Implement it as a durable backend schedule with its own persistence and worker processing. Do not fake it by mapping the UI to `continue_auto_assignment + activation_delay`.

Before coding, read:

- `V4_frontend_backend_changes.md`
- `Self_Dev_Docs/agent_backend_contract_v4.md` (§4–§8)
- `Self_Dev_Docs/dac_ta_tinh_nang_luong_nghiep_vu_v4.md` (§2.5–§2.12b, §4.7–§4.8)
- `Self_Dev_Docs/Logic_xử_lý_chính_v4.md` (§10–§15)
- Current implementations in `frontend/app/manager/page.tsx`, `frontend/components/manager/AssignmentWorkspace.tsx`, `frontend/lib/assignment.ts`, `src/services/assignment_proposal_service.py`, `src/workers/assignment_worker.py`, and assignment-proposal API/models.

Write a short product-override note in `docs/` that records the new recurring-proposal behavior and explicitly distinguishes it from V4 `DIRECT` auto-assignment. Do not silently relabel old behavior as a recurring schedule.

## 1. State model — do not merge these states

### State 0 — Ticket dashboard (default)

Keep the existing ticket dashboard and table as the default screen.

Inside the top-right action area of the **ticket-list card header**, immediately beside the search input, show exactly these primary entry actions:

- `Tự động duyệt`
- `Mở phân việc`

Do not place these actions in a detached workspace section or in the page header. Remove the former page-header assignment toggle.

`Mở phân việc` only enters State 1. It must **not** create an AI proposal or call the proposal-create endpoint. This distinction prevents an empty `BUILDING` screen and gives the coordinator a chance to inspect source queues first.

#### Automatic approval

Clicking `Tự động duyệt` opens a confirmation modal with this exact Vietnamese meaning:

> Hệ thống chỉ tự động duyệt những ticket đã có đủ thông tin phân loại. Ticket chưa phân loại vẫn phải do Ban quản lý xem lại và duyệt thủ công.

Actions: `Hủy` and `Xác nhận tự động duyệt`.

On confirmation:

- Only approve tickets whose `available_actions` contains `APPROVE`; this is the backend authority for whether the ticket is fully classified and approvable.
- Call the normal approval endpoint once per eligible ticket so every normal status transition, audit record, and notification remains intact.
- Do not invent a frontend classification rule.
- Refresh the dashboard and show a concise result count, including a partial-failure count if applicable.
- Do not auto-approve tickets not exposed as approvable by the backend. Preserve the current P3 governance behavior; if backend policy must exclude P3, enforce it on the backend rather than only hiding it in the UI.

### State 1 — Assignment preparation (no active proposal)

Entering State 1 fully replaces the dashboard table area, while preserving the overall manager page and any open right-side ticket detail panel.

The upper workspace contains only two source queues. Do **not** show an “open proposal” or “proposal in progress” column when no proposal exists.

```text
Phân việc tự động
14 ticket chờ duyệt                     2 ticket sẵn sàng phân việc

[ Chờ duyệt thủ công ]                  [ Đã duyệt · chưa phân công ]

                                              [ Tạo đề xuất phân việc ]
```

#### Queue A — `Chờ duyệt thủ công`

- Contains tickets not yet approved or without a final usable Category/Priority.
- Display a concise reason per ticket, such as `Chờ duyệt phân loại thủ công`, `Đủ điều kiện tự động duyệt`, `Chưa có danh mục`, or `Chưa có mức ưu tiên`.
- Tickets in this queue are not draggable to technicians and are never included in a proposal.
- Clicking a ticket opens the existing detail panel, where the coordinator can complete manual review/approval.

#### Queue B — `Đã duyệt · chưa phân công`

- Contains only `APPROVED` tickets with no active assignment.
- It is the only input to `Tạo đề xuất phân việc`.
- Display an accurate eligible-ticket count.
- Sort both queues priority descending (`P3`, `P2`, `P1`) and then oldest submission first.
- Clicking a row opens the existing detail panel; do not add a separate “View” button.

Place `Tạo đề xuất phân việc` as the clear primary action at the upper-right of the State 1 workspace/action area, aligned with the queues rather than as a disconnected page section. It is enabled only if Queue B has at least one ticket. Clicking it creates the backend proposal and enters State 2.

### State 2 — AI is building a proposal

After State 1’s `Tạo đề xuất phân việc` action, replace the two queues with a loading surface in the **same workspace footprint**. Do not move the whole page or make the right detail panel disappear.

Show:

```text
Đang tạo đề xuất phân việc

AI đang chuẩn bị quyết định:
✓ Thứ tự ưu tiên ticket
✓ Chuyên môn kỹ thuật viên
● Khối lượng công việc hiện tại
○ Bối cảnh vị trí và hạn xử lý
```

Requirements:

- Animate only the current bullet; respect reduced-motion preferences.
- Render technician-board skeleton columns/cards so the layout previews State 3 instead of leaving a blank page.
- The content is progress/context copy, not a claim that the model may select technicians outside the candidate snapshot. Preserve the backend’s candidate and privacy rules.
- Ticket rows remain clickable wherever rendered; the right detail panel stays usable.
- Provide `Hủy tạo đề xuất` throughout `BUILDING`. It cancels the batch, creates no assignment, and returns to State 1.
- Poll only the active batch until it becomes `READY`, `CANCELLED`, or `EXPIRED`; clean up the timer on unmount.
- Existing legacy `BUILDING` batches with zero items must remain recoverable: show their cancel action. Do not auto-confirm or silently delete them.

### State 3 — Assignment draft (the primary feature screen)

When the batch is `READY`, replace the loading surface with the draft board. This is the main assignment screen.

Header:

```text
Bản nháp phân việc                              8/10 ticket đã được đề xuất
```

Use the actual number of ticket members placed under a technician as the numerator and all batch ticket members as the denominator. A case can contain several ticket members; never count only the first member.

#### Left column — `Chưa phân công`

This column contains:

- `EMPTY` work items for which AI found no suitable technician.
- Items the coordinator deselected from the proposal.
- Items dragged from a technician back out of the proposal.

Never label this column or any item as “deleted”; the ticket is only excluded from the current assignment round, not removed from the system.

The left column uses a detailed, dashboard-like ticket card. For every ticket member show:

- Friendly temporary reference, `Ticket A`, `Ticket B`, … then `Ticket Z`, `Ticket AA`, etc.
- Real ticket ID.
- Category.
- Location.
- Submission time.
- Priority.
- Resolution deadline.

Reference names are keyed to the batch’s immutable item order. Dragging across columns must never rename a row.

Clicking a card/member opens the right detail panel without resetting the draft, current scroll position, drag changes, or schedule choices.

#### Right area — technicians

Render one drop section for every **active** technician.

Each section shows only:

- Technician name.
- The number of tickets proposed in this draft, for example `3 việc`.

Do **not** show workload, capacity, current jobs, duty/on-call state, overload warnings, or a fabricated `3/5` capacity. This screen has no reliable capacity metric. Do not render inactive technicians.

Technician cards are intentionally compact. For each ticket member show at least:

```text
Ticket C · Thấm tường                              P1
Bên trong căn hộ A-1203
Còn 4 giờ
```

The real ticket ID remains accessible from the card/detail panel, but the reference name is the visual handle used during this draft.

#### Editing behavior

- Support drag from unassigned → technician, technician → unassigned, and technician → another technician.
- Update technician counts immediately after a successful move.
- Every card also has an equivalent `<select>` control for keyboard and touch use.
- Drag/drop and `<select>` must use one shared `dropChange`/move function. A no-op move must send no request.
- The coordinator may choose any active technician, including one not in the AI candidate snapshot. Clearly mark it `BQL bổ sung`/`BQL thay đổi`; never imply it was AI’s choice.
- Validate active status in the backend both when an item is edited and when the batch is confirmed. Preserve AI proposed versus final technician in audit/history.

#### Fixed draft action bar

While State 3 is open, place a sticky action bar at the bottom of the proposal workspace, not at the top of the screen. It contains exactly these two actions:

- `Hủy đề xuất`
- `Xác nhận và phân việc`

Example summary:

```text
8 ticket đã gán · 2 ticket chưa gán
```

Partial confirmation is valid. If tickets remain unassigned, make the consequence explicit immediately before confirm:

> 2 ticket sẽ tiếp tục ở trạng thái chưa phân công.

`Hủy đề xuất` cancels the batch, creates no assignment, and returns to State 1. `Xác nhận và phân việc` assigns only placed rows, preserves all manual-wins and optimistic-version behavior, and then opens the result modal in section 2.

## 2. Result modal and truthful recurring proposal schedule

Immediately after a successful confirmation, show a result modal before navigating away:

```text
Đã phân công 8 ticket cho 3 kỹ thuật viên

2 ticket chưa thể phân công và vẫn nằm trong hàng chờ.

Tự động tạo đợt phân việc tiếp theo:
○ Không tự động
○ Lặp lại mỗi 2 giờ
○ Lặp lại mỗi 1 ngày
○ Lặp lại mỗi 3 ngày

[ Hoàn tất ]
```

Rules:

- This is a **recurring draft-generation schedule**, not a one-time assignment delay. Keep the word `Lặp lại` in the UI.
- `Hoàn tất` persists the selected schedule and returns to the refreshed dashboard.
- The next due run creates a `BUILDING`/`READY` **proposal batch for coordinator review**. It never directly assigns tickets and never replaces a coordinator’s confirmation.
- If no eligible tickets exist at a due time, create no empty visible batch; record/advance the next due time safely and wait for the next interval.
- Never use the existing `continue_auto_assignment` or `activation_delay` fields to pretend this schedule exists. Those are the existing V4 direct-assignment semantics and remain separate.

### Durable backend implementation

Implement this as real persistent behavior, not a browser timer:

- Add a migration and a small persistent singleton schedule model/table (or an equally clear isolated persistence design) containing at least `enabled`, `interval` (`2_HOURS | 1_DAY | 3_DAYS | null`), `next_run_at`, `last_run_at`, version/audit timestamps, and who configured it.
- Add authenticated coordinator GET/PATCH API and corresponding TypeScript types/client calls. Use optimistic versioning or row locks so two coordinators cannot configure/run it twice.
- Extend the existing durable worker to process due schedules idempotently. It must verify there is no `BUILDING`/`READY` proposal batch before creating a scheduled batch.
- A scheduled batch is created by `SYSTEM` / scheduler, not by borrowing a coordinator identity. Persist the creation actor/type accurately; adjust nullable foreign keys/model fields through migration if needed.
- Scheduled creation uses the same batch limit, ordering, case-member limit, candidate snapshot, TTL, model validation, failure/fallback, and no-manual-overwrite rules as a coordinator-created proposal.
- Keep existing `DIRECT` jobs, P3 direct behavior, grace-window cancellation, and global direct-auto settings independent. Do not let this new schedule silently enable DIRECT assignment.

## 3. Assignment history as a top-level view

`Lịch sử phân việc` is a top-level horizontal navigation/view alongside the dashboard, not a small tab inside the assignment-preparation workspace. It must remain reachable without opening State 1.

Show only confirmed batches, newest first. Each row/card shows:

```text
23/08/2026 · 14:20
8 ticket · 3 kỹ thuật viên
AI đề xuất · Xác nhận bởi Nguyễn Văn A
```

Opening a record is read-only and shows:

- Every ticket and assigned technician.
- Final assignment selected by the coordinator.
- Original AI proposal and any `BQL thay đổi` marker.
- Confirmation actor and confirmation time.
- Recurring schedule selected after confirmation, if any.

### Immutable history requirement

History must preserve the exact confirmed state. Do not read category, location, priority, SLA, technician name, or coordinator name live from the current ticket/profile when rendering a confirmed history record.

Add a migration and persist a confirmation snapshot, for example a JSON snapshot on the confirmed batch containing:

- Confirmation time and confirming actor ID/name at the time of confirmation.
- Schedule selection at confirmation time.
- For every proposal item: AI proposed technician ID/name, final technician ID/name, final status, and all member tickets.
- For every member: ticket ID/display code, category, location, priority, submission time, and resolution deadline as they were at confirmation.

Populate this snapshot transactionally with assignment confirmation. Expose a safe read-only history response derived from the snapshot. Never expose raw model output, prompts, secrets, stack traces, or unnecessary resident PII.

## 4. Preserve these safety rules

- No duplicate-dispute UI or API.
- Proposal expiry remains ten minutes; expired batches cannot be confirmed.
- A human manual assignment always wins over a proposal or DIRECT race.
- `EMPTY`/no-candidate rows do not block valid proposal rows.
- Inactive technicians are never selectable and are rejected by the backend.
- Ticket/case rules remain intact: maximum 20 ticket members per batch; never split an incident case; case maximum remains five members.
- Do not change the analysis Agent or resident/technician flow.
- Do not add a global P3 blocking overlay in this task.

## 5. Acceptance tests and verification

Add focused backend and frontend tests for all of the following:

1. State 0’s `Mở phân việc` does not call create-proposal; it only enters State 1.
2. Bulk approval calls the normal approval endpoint only for backend-approvable tickets and reports partial failures.
3. State 1 has exactly two source queues and no third “open proposal” column.
4. Creating a batch moves State 1 → State 2 without layout jump; `BUILDING` shows skeleton/progress and remains cancellable.
5. State 3 renders all incident-case members, retains stable reference names across moves, supports all move directions, and sends no request for a no-op move.
6. Partial confirm assigns only placed tickets and makes remaining unassigned tickets explicit.
7. The recurring setting uses a durable persisted schedule; a due worker run creates a reviewable proposal, never an automatic assignment; it advances safely when no eligible tickets exist and does not duplicate an active batch.
8. Confirmed history remains unchanged after later category/location/priority/SLA/profile-name changes.
9. Scheduled batch creation and history record the correct actor (`SYSTEM` for scheduler creation; named coordinator for confirmation).
10. Existing proposal TTL, manual-wins, active-technician validation, and DIRECT grace cancellation tests still pass.

