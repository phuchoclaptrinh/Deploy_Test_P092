# Replacing the proposal architecture: Visual + Automatic Assignment


> **Superseded in part by `9f0a1b2c3d4e` (2026-08-27).** The acceptance step
> described below — `acceptance_due_at`, the acceptance sweep, the ACCEPTED
> state, "Hạn nhận việc", and the acceptance-based agent metrics — has been
> removed. `docs/assignment_lifecycle.md` is the current statement of the
> technician lifecycle and of the still-undecided start deadline. Everything
> else here (the scheduler, the dispatch worker, the Visual Assignment board,
> the AT_RISK agent's eligibility boundary) is unchanged and still accurate.

What this document is: the delivery record §10 asks for. Sections 1–6 map
one-to-one onto its six items.

The change in one sentence: **one workflow that did two jobs badly has become
two workflows that each do one job.** The proposal workspace was simultaneously
the only way to place work by hand and the only way to turn automation on;
Visual Assignment is now the first, a toggle is the second, and neither depends
on the other.

---

## 1. Migration and removal plan

### 1.1 What was removed

Revision **`8e9f0a1b2c3d`** (`alembic/versions/8e9f0a1b2c3d_replace_proposal_with_dispatch.py`),
following `7d8e9f0a1b2c`. It is **forward-only**: `downgrade()` raises rather
than pretending, because it deletes proposal batches, their items and their
confirmation snapshots, and a downgrade that recreated the empty tables would
report success while the data they existed to hold was gone.

| Removed | Kind | Replaced by |
| --- | --- | --- |
| `assignment_proposal_batches` | table | — |
| `assignment_proposal_items` | table | — |
| `assignment_proposal_item_members` | table | — |
| `assignment_proposal_schedules` | table (recurring proposal schedule) | — |
| `ai_assignment_jobs` | table | `dispatch_events` |
| `ai_assignment_job_members` | table | `dispatch_events` (one ticket per event) |
| `ticket_assignments.assignment_job_id` | column | `ticket_assignments.dispatch_event_id` |
| `auto_assignment_settings.activation_delay` | column | — (§2 assigns immediately) |
| `auto_assignment_settings.activated_by_batch_id` | column | `enabled_by_user_id` |
| `src/assignment_rules/` | package (rule engine) | `src/dispatch/scheduler.py` |
| `src/assignment_agent/` | package (proposal/DIRECT agent) | `src/dispatch/agent/` |
| `config/assignment_rules.yaml` | rule set | — |
| `assignment_proposal_service`, `assignment_schedule_service`, `assignment_job_service`, `assignment_direct_service`, `assignment_decision_engine`, `assignment_candidates`, `assignment_history_service`, `assignment_trigger_service` | services | `src/dispatch/*`, `visual_assignment_service` |
| `src/workers/assignment_worker.py` | worker (5 stages) | `src/workers/dispatch_worker.py` (3) |
| `GET/POST /coordinator/assignment-proposals*` | 6 endpoints | `/coordinator/visual-assignment/*` |
| `GET/PATCH /coordinator/assignment-schedule` | 2 endpoints | — |
| `GET /coordinator/assignment-history*` | 2 endpoints | `/coordinator/dispatch/*` |
| `GET/POST /coordinator/assignment-jobs*` | 2 endpoints | `/coordinator/dispatch/events` |
| `AssignmentWorkspace.tsx`, `lib/assignment.ts`, `AutoApproveAction.tsx` | components | `VisualAssignmentBoard.tsx`, `lib/visualAssignment.ts`, `AutoAssignmentControl.tsx` |
| `/manager/automation`, `/manager/assignment-history` | routes | `/manager/dispatch` |
| The rule that the switch could only be enabled after confirming a batch | business rule | a confirmation modal + `acknowledged` |

### 1.2 Ordering inside the migration

Nothing is dropped while something still points at it:

1. detach `ticket_assignments` from `ai_assignment_jobs`;
2. detach `auto_assignment_settings` from `assignment_proposal_batches`;
3. drop the six tables, **children first**;
4. create `dispatch_events` and `at_risk_decisions`, with RLS and no anonymous grant;
5. add the §4 scheduling columns and the §3 one-IN_PROGRESS index;
6. rewrite `assignment_source` onto the new vocabulary.

Every drop is guarded by an inspector check, so a database that never had the
proposal architecture migrates cleanly too.

### 1.3 Data carried across

* **`acceptance_reassign_at` → `acceptance_due_at`.** The same instant under a
  name that describes the promise rather than the consequence. Copied, not
  dropped: in-flight assignments keep their deadline across the deploy.
* **`assignment_source` rewritten, not widened.** `MANUAL`/`COORDINATOR_MANUAL`
  → `COORDINATOR_MANUAL`; `AI_AUTO` → `AUTO_SCHEDULER`; `AI_PROPOSAL_CONFIRMED`
  → `COORDINATOR_VISUAL`. The last mapping is deliberate: a proposal-confirmed
  assignment was a coordinator approving a table of placements, which is what
  Visual Assignment is now. Mapping it to an automatic source would
  retroactively record a human decision as one nobody made.
* **The switch is reset to off.** An enabled row under the old shape carries no
  provenance, and the new constraint requires an enabled switch to name who
  enabled it. Re-enabling is one click and now records a person.

### 1.4 Deploy order

1. `alembic upgrade head`
2. Restart the API.
3. Replace `python -m src.workers.assignment_worker` with
   `python -m src.workers.dispatch_worker` (compose files, Makefile and
   `docs/operations.md` already say so).
4. Deploy the frontend.

The switch is off after step 1, so nothing is assigned automatically until a
manager turns it on — which also enqueues the backlog that accumulated while it
was off.

### 1.5 Migration verification

The migration was executed successfully against the configured PostgreSQL test
database on 2026-08-26, from revision `7d8e9f0a1b2c` to
`8e9f0a1b2c3d`. Post-migration checks confirmed that the proposal/job tables are
absent, `dispatch_events` and `at_risk_decisions` exist, the scheduling columns
exist, and Automatic Assignment is reset to off. The static migration test
remains part of the suite; this execution additionally verifies the real
PostgreSQL DDL path.

---

## 2. API contracts

All responses use the existing envelope: `{ data, meta, error, request_id }`.

### 2.1 Visual Assignment board data

```
GET /api/v1/coordinator/visual-assignment/board?limit=100
```

Returns the whole board in one request — every unit, every technician column,
and for each pairing what would happen. That is more data than a lazier design
would send, and it is the point: a board that had to ask the server what a drop
would do would either be slow or would let a manager drop first and find out
afterwards.

```jsonc
{
  "generated_at": "2026-08-26T01:00:00Z",
  "within_working_shift": true,          // false outside 08:00–18:00 (ICT)
  "units": [{
    "unit_id": "case:7f3a…",             // "ticket:<id>" or "case:<id>"
    "unit_type": "GROUP",                // GROUP is indivisible (§1)
    "ticket_ids": ["…", "…"],
    "display_codes": ["PA-A1B2C3", "PA-D4E5F6"],
    "category_code": "WATER",
    "priority": "P2",                     // the most urgent member's
    "score": 40.0,
    "submitted_at": "2026-08-26T00:10:00Z",
    "p80_seconds": 28800,                 // two WATER members: 2 × 4h
    "member_count": 2,
    "eligible_technician_ids": ["…"],     // passed every §3 constraint
    "previews": [{
      "technician_id": "…",
      "blocked": false,                   // true ⇒ a §3 constraint fails
      "warnings": ["SCHEDULE_RISK", "OVERLOADED"],
      "planned_start_at": "2026-08-26T01:00:00Z",
      "planned_finish_at": "2026-08-26T11:30:00Z",
      "worst_slack_seconds": -7200
    }]
  }],
  "technicians": [{
    "technician_id": "…", "display_name": "…",
    "is_active": true, "is_available": true,
    "active_assignment_count": 3, "in_progress_count": 1,
    "planned_slots": [{ "order": 0, "planned_start_at": "…", "planned_finish_at": "…", "slack_seconds": 1800, "in_progress": true }],
    "day_ends_at": "2026-08-26T11:00:00Z"
  }]
}
```

**Warning codes.** `MISSING_SKILL`, `TECHNICIAN_UNAVAILABLE` and `OUT_OF_SHIFT`
are §3 hard constraints: they set `blocked` and the bulk confirm rejects them.
`OVERLOADED` and `SCHEDULE_RISK` are **not** in §3's list and are advisory —
they colour a card the manager may still confirm.

The pool is deliberately broader than the automatic path's eligibility. §2 sends
everything automation refuses to Building Management, so a P3 emergency appears
here precisely because it must never appear there.

### 2.2 Bulk visual assignment confirmation

```
POST /api/v1/coordinator/visual-assignment/confirm
{ "placements": [ { "unit_id": "ticket:…", "technician_id": "…" } ] }
```

The request carries unit and technician ids and nothing else. No planned times,
no warnings, no acknowledgement flags: none of those is the client's to assert,
the server recomputes them under lock, and a field a client could lie about is a
field the server must not read.

**200**

```jsonc
{ "assigned_unit_count": 4, "assigned_ticket_count": 6, "assignment_ids": ["…"] }
```

**409 `VISUAL_PLACEMENT_INVALID`** — all or nothing. Every precondition for every
unit is checked, under lock, before a single row is written.

```jsonc
{
  "error": {
    "code": "VISUAL_PLACEMENT_INVALID",
    "message": "Một số phân công không hợp lệ. Không có thay đổi nào được lưu.",
    "details": { "failures": [
      { "unit_id": "ticket:…", "technician_id": "…", "codes": ["MISSING_SKILL"] }
    ] }
  }
}
```

Other rejections: `VISUAL_UNIT_NOT_PLACEABLE` (409) for a unit that left the
board. Failure codes beyond the warnings: `TICKET_NOT_APPROVED`,
`TICKET_IS_DUPLICATE`, `P3_REVIEW_PENDING`, `ACTIVE_ASSIGNMENT_EXISTS`.

### 2.3 Automatic Assignment toggle

```
GET /api/v1/coordinator/auto-assignment
PUT /api/v1/coordinator/auto-assignment
{ "enabled": true, "acknowledged": true, "expected_version": 3 }
```

```jsonc
{
  "enabled": true, "version": 4,
  "enabled_at": "…", "enabled_by_user_id": "…", "enabled_by_name": "Điều phối viên",
  "updated_at": "…",
  "open_event_count": 7          // waiting right now, so switching off is informed
}
```

`acknowledged` is required to enable and corresponds to the manager having read
§2's confirmation modal. The backend refuses `enabled: true` without it — with a
400 whose message **is** §2's wording — so skipping the modal is not a shortcut.
`expected_version` is optional optimistic concurrency (`409 CONFLICT_VERSION`).

Turning it **on** also enqueues the backlog: every ticket that became eligible
while it was off. Turning it **off** stops future dispatch, never unwinds an
existing assignment, and lets the next pass escalate anything still queued to
Building Management rather than deleting it.

### 2.4 Dispatch and at-risk decision visibility

```
GET /api/v1/coordinator/dispatch/events?status=ESCALATED&limit=50
GET /api/v1/coordinator/dispatch/at-risk-decisions?limit=50
POST /api/v1/coordinator/dispatch/run-once          # operations only
```

```jsonc
// at-risk-decisions
{
  "ticket_display_code": "PA-A1B2C3",
  "technician_name": "…",
  "decision_source": "AGENT",              // or SCHEDULER_FALLBACK
  "reason": "Lịch nhẹ nhất trong nhóm ứng viên.",
  "model_name": "gpt-…", "latency_ms": 1840,
  "candidate_technician_ids": ["…"],       // the set the backend authorised
  "slack_seconds": -5400,
  "error_code": null
}
```

`decision_source` is the field that matters in review. `AGENT` means a model
weighed the trade-off; `SCHEDULER_FALLBACK` means it did not answer in time and
the least-negative-slack candidate was taken instead. Both are legitimate, both
notify Building Management, and the payload never blurs them.

### 2.5 Technician and resident surfaces

```
GET /api/v1/technician/queue
```

```jsonc
{ "generated_at": "…", "within_working_shift": true, "items": [ /* ordered */ ] }
```

One ordered list, not `do_now`/`next` fields: the split is a rendering decision
and encoding it would force a second shape the first time a third bucket is
wanted. Each item carries `acceptance_due_at`, `planned_start_at`,
`planned_finish_at`, `planned_order`, `risk_state` and `slack_seconds`.

Resident payloads (`GET /api/v1/tickets`) changed shape:

| Removed | Added |
| --- | --- |
| `estimated_resolution_text` | `progress_text` |
| `expected_resolution_at` | `expected_start_at` |

`planned_finish_at` is **absent from every resident payload**. §4 forbids
presenting it as a completion promise, and the enforcement is that there is no
field capable of carrying it.

---

## 3. Scheduler design

`src/dispatch/scheduler.py` — pure functions over frozen dataclasses. Nothing
touches a `Session`, issues a query or reads a clock; `now` is always passed in.
That is what lets §8's "run scheduling in memory after the bulk query" hold, and
what makes the whole of §6 testable without a database.

### 3.1 The model

* A technician's queue is a **sequence**. Work is simulated from the next
  working instant, one unit after another, spilling across the overnight gap
  (`shift.advance`). The window is 08:00–18:00 **every day**, Vietnam time —
  no weekend rule, because §3 does not ask for one.
* Every assigned unit carries a **committed deadline**: the `planned_finish_at`
  written when it was placed. That commitment is what slack is measured
  against, and it is why `planned_finish_at` is persisted rather than
  recomputed on read.
* **Slack** is the gap between a unit's committed deadline and where the current
  simulation lands it, in **working** seconds. Wall-clock seconds would score
  the fourteen hours a technician is off shift as capacity.
* Placement commits `planned_finish_at = simulated finish + safety buffer`, so a
  freshly placed unit starts with exactly one buffer of headroom. Later
  insertions eat that headroom first and only then go negative — which is what
  makes the buffer a buffer rather than a constant offset.

The buffer is **30 minutes** (`DISPATCH_SAFETY_BUFFER_SECONDS`). §6 asks for "a
safety buffer" without naming one; thirty minutes is comfortably shorter than
the shortest §5 duration (three hours) so it never dominates the arithmetic, and
long enough to absorb travel and handover between two jobs in one building.

### 3.2 Ordering (§6 items 1–4)

`sort_slack_seconds = working_seconds_until(deadline) − remaining_work − buffer`,
which is the classic minimum-slack-time rule and is **position-independent** —
slack computed from a unit's place in the queue could not decide that place
without circularity.

A unit with no commitment yet is measured against `provisional_deadline`: the
commitment it would have received *on arrival*, computed from its own submission
time. This matters more than it looks. Returning a flat zero — the obvious
shortcut — makes every new unit more urgent than every on-time commitment, so
*every* insertion onto a non-empty technician breaks something and reports
AT_RISK. That would send the whole queue to the agent and empty §7 of meaning.
Measuring from submission instead makes a brand-new unit tie at zero with a
just-placed one (score then decides, per item 2) while a report that has waited
three working hours correctly outranks both.

Ties fall through score (desc), then submission time (asc), then the unit key —
item 4 permits any order, but an acceptable-but-unstable one would make every
schedule test flaky for no gain. An IN_PROGRESS unit is pinned to the front and
keeps its real start time.

### 3.3 The verdict

* **SAFE** — some eligible technician absorbs the unit while every unit already
  committed to them keeps slack ≥ 0.
* **AT_RISK** — every eligible technician has at least one committed unit pushed
  negative. Still assignable; a trade-off is simply being made, and this is the
  only case §7 lets an agent near.
* **Infeasible** — no technician passed §3 at all. Escalate to Building
  Management; never hand an empty candidate set to an agent.

Between two SAFE technicians the ranking is: earliest start, then most headroom
left (an empty queue is *unlimited* headroom, not zero), then least loaded, then
id. For an AT_RISK unit the same ranking is the fallback order — its head is the
least-negative-slack technician.

### 3.4 Tests

| File | Covers |
| --- | --- |
| `tests/test_dispatch/test_shift.py` | the working window, overnight spill, signed working seconds |
| `tests/test_dispatch/test_scheduler.py` | all four ordering rules, slack, SAFE/AT_RISK/infeasible, ranking |
| `tests/test_dispatch/test_eligibility.py` | §3's closed list, and that workload is *not* on it |

Two design bugs were found by writing these and fixed: the flat-zero sort key
described above, and `None` worst-slack (an empty queue) ranking below a busy
technician with slack left.

---

## 4. At-risk agent tool contract

```python
get_candidate_dispatch_history(db, candidate_technician_ids, category_id, current_time)
    -> dict[UUID, list[HistoryWindow]]
```

**One statement, one look-back.** Every window §7 asks for (30/60/90 days) is
computed in Python from a single pull covering the widest. Three windows × N
technicians would otherwise be 3N statements per micro-batch.

Per technician, per window: `completed_count`, `assigned_count`,
`accepted_count`, `accepted_on_time_count` (against the recorded
`acceptance_due_at`), `median_acceptance_seconds`, `rejected_count`,
`acceptance_timeout_count`, `unable_to_handle_count`, `reassigned_away_count`,
and `by_category` P50/P80 handling time. Current schedule, workload and
projected slack come from the in-memory simulation and travel in
`CandidateDispatchHistory`.

Times are **working** seconds: a job started at 16:00 and finished at 09:00 next
morning took three working hours, not seventeen, and the P80 it is compared
against is in working time too.

### 4.1 The privacy boundary

§7 restricts the tool to aggregated operational data. The enforcement is
structural, not procedural:

* the query names its columns explicitly — no `SELECT *`, no relationship walk —
  and none of them is `tickets.description`, a resident profile, a phone number,
  an email or an address;
* `CandidateDispatchHistory` has `extra="forbid"` and **no field capable of
  carrying free text**. A rule enforced by the absence of a field cannot be
  broken by a caller who forgets it;
* technician display names are absent too. The agent has no use for them, so it
  gets opaque ids and the backend maps them back for the manager UI;
* the ticket is identified to the model by its **dispatch-event id**, never its
  ticket id.

### 4.2 What the agent may return

`AtRiskBatchDecision` — one `technician_id` per `ticket_ref`, from that ticket's
`eligible_technician_ids` and no other. `_validate` checks every pick back
against that set and **discards** anything outside it. Discarded, not repaired:
repairing would mean the backend inventing a decision and recording it as the
agent's.

A ticket the agent skipped, or whose pick was discarded, falls back on its own —
partial answers are handled per ticket rather than by failing the batch.

### 4.3 Failure (the resolved question)

Timeout, transport error, disabled, unconfigured and "no answer survived
validation" all raise `AtRiskDecisionError`. The dispatcher then **assigns
anyway**, to the scheduler's least-negative-slack candidate, recording
`assignment_source = AUTO_FALLBACK` and `decision_source = SCHEDULER_FALLBACK`,
and notifies Building Management. Concurrency is bounded
(`AT_RISK_AGENT_MAX_CONCURRENCY`, default 2) and the call runs off-thread with a
hard timeout, because an abandoned provider thread cannot be killed.

### 4.4 Tests

`tests/test_dispatch/test_agent_tool.py` (10) — percentile shape, working-time
measurement, window narrowing, acceptance performance, the four end-reason
counters, the requested category always present, **the payload carries no
resident data** (asserted against the serialized JSON, not the column list), and
one statement for every window and candidate.

`tests/test_dispatch/test_at_risk_agent.py` (12) — one call per batch,
out-of-set picks discarded, unknown refs discarded, duplicates, total failure
raising, timeout inside the bound, disabled/unconfigured short-circuits, and the
prompt carrying the eligible ids.

---

## 5. Load and latency

`tests/test_dispatch/test_load_and_connections.py` (14).

| Property | How it is pinned |
| --- | --- |
| Statement count does not grow with batch size | `report.query_count == 6` for 1, 5 and 20 tickets |
| The whole pass has no per-ticket read | 20 tickets issue fewer than 6× the SELECTs of 1 |
| One agent call per micro-batch | 8 at-risk tickets → 1 request |
| A full batch fits its window | 20 tickets inside 10× the configured interval |
| Pools stay under the quota | `peak_db_session_budget ≤ supabase_max_sessions` (15) |
| An over-budget config refuses to boot | `validate_runtime_safety()` raises |
| The 20-ticket ceiling cannot be widened | `Settings(dispatch_micro_batch_size=50)` raises |
| No ticket is assigned twice | second pass claims nothing, one assignment exists |

The six statements: technicians + skills, their queues, exclusions, category
codes, the batch-wide emergency-gate check, and the batch-wide notification
recipients. Writing these tests found a real N+1 — the per-ticket eligibility
re-check and per-ticket notification recipient lookup — which is now batched.

**Connection budget.** The API and the worker size their own pools
(`API_DB_POOL_SIZE` + overflow, `DISPATCH_WORKER_DB_POOL_SIZE` + overflow) and
neither can see the other. `Settings.peak_db_session_budget` is the only place
the sum exists, and `validate_runtime_safety()` refuses to boot when it exceeds
`SUPABASE_MAX_SESSIONS` — checked at startup, because the first timeout happens
at peak load with residents waiting, which is the one moment nobody reads logs.

**Latency shape.** The claim is committed *before* the work starts. A single
transaction spanning the bulk load, the scheduling and a bounded agent call
would hold a Supabase session open for the whole agent timeout. So: claim,
commit, work, commit.

---

## 6. UI

| Surface | Change |
| --- | --- |
| Manager ticket list | One "Phân việc tự động" button became **two controls**: `Phân việc trực quan` opens the board, `AutoAssignmentControl` is the ON/OFF toggle. The deadline column is now `Hạn nhận việc` (`acceptance_due_at`). |
| Visual Assignment board | `VisualAssignmentBoard.tsx` — pool on the left, technician columns on the right, one confirm at the bottom. Drag and drop plus a `<select>` on every card, so the screen is reachable without a mouse. An invalid drop is refused at `dragover`; a rejected confirm marks the offending cards and leaves the arrangement alone. |
| Toggle confirmation modal | `AutoAssignmentControl.tsx` — carries §2's wording verbatim (pinned by a test on both sides). Switching off shows how many events are queued and says plainly that it does not recall work already assigned. |
| Manager at-risk notifications | `/manager/dispatch` — the at-risk decision list with its `decision_source` column, and the dispatch queue with escalation reasons. In-app notifications: `DISPATCH_AT_RISK_DECISION`, `DISPATCH_ESCALATED`. |
| Technician ordered queue | "Làm ngay" / "Tiếp theo" from `planned_order`, an estimated start, and a risk warning. The old progress bar towards a completion time is gone — there is no longer one to move towards. The countdown is to `acceptance_due_at` and stops once accepted. |
| Resident expected start | `progress_text` + `expected_start_at`. Before acceptance: "Chờ kỹ thuật viên xác nhận nhận việc". After: the expected start. Once started: "Kỹ thuật viên đang xử lý". Never a completion time. |

---

## 7. Operational decisions

* **Every manual assignment path enforces the shift.** Both single-ticket and
  case assignment reject placement outside 08:00–18:00 ICT, matching Visual
  Assignment. P3 gating remains first, so an emergency ticket always receives
  its specific P3-review response rather than a generic out-of-shift response.
* **The acceptance SLA durations** are P1 49h / P2 2h30m from cycle start, P3 5
  minutes. The former P1 3-day and P2 3-hour values were completion commitments
  under the old SLA and are no longer the official SLA. These acceptance values
  are configuration (`ACCEPTANCE_DUE_P*_SECONDS`), not code.
