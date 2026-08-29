# The technician assignment lifecycle

Revision `9f0a1b2c3d4e` removed the acknowledgement step. This document is the
authoritative statement of what replaced it, and — more importantly — of the one
decision that has deliberately **not** been made.

## The states

```
before                                after

ASSIGNED                              ASSIGNED
   │                                     │
   ├──▶ ACCEPTED ──▶ IN_PROGRESS         ├──────────────▶ IN_PROGRESS
   │       │             │               │                    │
   │       │             ├──▶ COMPLETED  │                    ├──▶ COMPLETED
   │       │             └──▶ UNABLE     │                    └──▶ UNABLE_TO_HANDLE
   │       ├──▶ REJECTED                 ├──▶ REJECTED
   │       └──▶ UNABLE                   ├──▶ REASSIGNED
   ├──▶ REJECTED                         └──▶ UNABLE_TO_HANDLE
   ├──▶ REASSIGNED
   └──▶ UNABLE
```

`ACCEPTED` is gone from `AssignmentStatus`, from the PostgreSQL
`assignment_status_enum`, and from every transition in
`src/domain/assignment_transitions.py`. It is not merely unreachable: there is no
value to name.

The first positive action a technician can take on assigned work is **“Bắt đầu
xử lý”**. The alternative is still “Từ chối”.

## What `/technician/assignments/{id}/start` guarantees

One endpoint now carries everything the old `accept` + `start` pair carried
between them, under one lock, in one transaction:

1. the caller owns the assignment;
2. it is still `ASSIGNED`;
3. the ticket is `APPROVED`;
4. the technician holds no other `IN_PROGRESS` assignment — checked in the
   service for a readable error, and enforced by the partial unique index
   `uq_ticket_assignments_one_in_progress_per_technician`, which is what settles
   two concurrent calls that each pass the service check;
5. **it is the head of the technician’s queue** (`planned_order == 0`), else
   `409 ASSIGNMENT_NOT_AT_QUEUE_HEAD`.

Then it sets `started_at`, moves the ticket to `IN_PROGRESS`, appends the
timeline event, writes the `START_ASSIGNMENT` audit record, notifies the
resident, and **reindexes the technician’s remaining queue in the same
transaction** — raising a Building Management notification for any queued item
that crosses into `AT_RISK` as a result.

### Why the backend re-simulates before reading `planned_order`

The number on the row was true when it was written. A job completed thirty
seconds ago on another device already moved the queue, and a client holding the
old copy would otherwise be able to start the wrong job. `/start` calls
`reindex_technicians` first, then reads the head. The frontend’s order is a
rendering of the backend’s, never an input to it.

If Building Management needs a different ticket started first, they change the
queue through their own management action, which leaves an audit record. A
technician cannot reach past the schedule silently.

## The open decision: `start_due_at`

**No start deadline exists, and none may be invented.**

The old `acceptance_due_at` cannot be renamed into one. It was measured from the
moment work was handed over; a ticket legitimately sitting third in a valid
queue is *planned* to begin hours after that, so a clock started at `assigned_at`
would fire on work that is exactly on schedule.

`planned_start_at` is an estimate produced by the scheduler. Turning it into a
deadline requires answers to:

1. Is `planned_start_at` merely an estimate, or a hard start deadline?
2. If a grace period exists, what exact duration applies after it?
3. When the technician has not started by `start_due_at`, should the system
   (A) alert Building Management only; (B) automatically release and
   re-dispatch; (C) mark `AT_RISK` and ask the agent to choose; or (D) both B
   and C?

Until those are answered, the following are absent **on purpose**, not by
oversight:

| Absent | Where it would go |
| --- | --- |
| `start_due_at`, `start_warning_at` | `ticket_assignments`, one `add_column` each |
| `AssignmentEndReason.START_TIMEOUT` | `src/models/enums.py` |
| `HistoryWindow.start_timeout_count` | `src/dispatch/agent/schemas.py` and its tool query |
| a start-deadline sweep | `OperationalTimeoutService` |
| a synchronous deadline check | `AssignmentService.start` |

### Consequence to be aware of now

Nothing releases a silent `ASSIGNED` assignment any more. The acceptance sweep
that used to do it is gone, and no start sweep replaced it. Today, an assignment
a technician never starts stays with them until a human intervenes — Building
Management reassigns it, or the technician rejects it.

That is the honest state of the system, and it is the strongest argument for
settling the three questions above.

### When the rule is decided

Enforce it in **both** places:

* synchronously in `/start`, and
* asynchronously in the dispatch worker as recovery,

because the worker’s sweep runs on a 30-second cadence and cannot be the only
thing holding a deadline. This is the defect the old design had: `/accept` did
not check `acceptance_due_at` itself, so an acceptance landing between two
sweeps beat a deadline that had already passed.

## Agent history semantics

`get_candidate_dispatch_history` measures **start** performance, not acceptance:

| Removed | Added |
| --- | --- |
| `accepted_count` | `started_count` |
| `accepted_on_time_count` (vs `acceptance_due_at`) | `started_on_time_count` (vs `planned_start_at`) |
| `median_acceptance_seconds` (wall-clock) | `median_assignment_to_start_seconds` (**working** seconds) |
| `acceptance_timeout_count` | — (see the table above) |

Unchanged: `completed_count`, `rejected_count`, `unable_to_handle_count`,
`reassigned_away_count`, and per-category P50/P80 working durations.

Working seconds rather than wall-clock for the assignment-to-start wait, because
a queue spanning the overnight gap is now the normal case: assigned at 17:30 and
started at 08:10 is a forty-minute wait, not a fifteen-hour one.

The privacy boundary is unchanged — the tool’s explicit column list still carries
no ticket text, resident profile, phone number, email or address.

## What each role may see

| Fact | Resident | Technician | Building Management |
| --- | --- | --- | --- |
| `planned_start_at` | yes | yes | yes |
| `planned_finish_at` | **never** | no | yes |
| `planned_order` | no | as “Làm ngay / Tiếp theo / Thứ N” | yes |
| `risk_state`, `slack_seconds` | no | risk indicator only | yes |
| `reassignment_count` | no | no | yes |

The resident enforcement is structural: `ResidentTicketResponse` has no field
capable of carrying a completion estimate, so a serializer that forgot the rule
could not break it.

## Scheduler behaviour that did **not** change

Working window 08:00–18:00 every day, category P80 sizing, the 30-minute
configured safety buffer, and the queue order:

1. an `IN_PROGRESS` unit pinned first;
2. lowest slack;
3. higher score on a tie;
4. earlier submission on a further tie;
5. unit key, for determinism.

`SAFE` / `AT_RISK` still mean exactly what `src/dispatch/scheduler.py` documents:
measured against the committed `planned_finish_at`. There is **no**
`internal_target_finish_at`, and no claim of “zero lateness” is made anywhere.

### Initial P80 configuration (updated 2026-08-27)

These are internal working-time estimates used for capacity, queue ordering and
SAFE/AT_RISK simulation. They are not resident-facing completion promises.

| Category | P80 |
| --- | ---: |
| Nước | 4 giờ |
| Tường ẩm, thấm | 6 giờ |
| Thang máy | 4,5 giờ |
| Mất điện | 3 giờ |
| An ninh, an toàn | 1 giờ |
| Tiếng ồn | 3 giờ |
| Khóa, cửa | 2 giờ |
| Điều hòa | 5 giờ |
| Mùi hôi, vệ sinh | 3 giờ |
| Internet, TV | 3 giờ |
| Hư hỏng khu vực chung | 2 giờ |

The authoritative runtime table is `src.dispatch.durations.P80_BY_CATEGORY_CODE`.
`DEFAULT_P80` remains the largest configured duration (currently six hours) for
an unknown or newly added category, so it is scheduled conservatively.
