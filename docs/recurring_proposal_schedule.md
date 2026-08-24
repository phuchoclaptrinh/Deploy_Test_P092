# Product override — the recurring proposal schedule

**Status:** implemented, 24/08/2026.
**Overrides:** the assumption in `Self_Dev_Docs/agent_backend_contract_v4.md` §4.6
and `Self_Dev_Docs/dac_ta_tinh_nang_luong_nghiep_vu_v4.md` §2.12 that a proposal
batch is only ever opened by a coordinator pressing a button.

## What changed, and what did not

The product now has **two** automation features. They are easy to confuse and
must never be conflated, so they are named apart everywhere — in the tables, in
the services, in the API and in the UI copy.

| | V4 DIRECT auto-assignment | Recurring proposal schedule |
|---|---|---|
| Question it answers | May Backend assign an approved ticket by itself, and how long does it wait first? | How often does Backend build a new draft table for a coordinator to review? |
| Effect of a run | Creates a `TicketAssignment`, notifies the technician | Creates a `BUILDING`/`READY` `AssignmentProposalBatch`, notifies nobody |
| Storage | `auto_assignment_settings` (`enabled`, `activation_delay`) | `assignment_proposal_schedules` (`enabled`, `interval_code`, `next_run_at`) |
| API | `GET`/`PATCH /coordinator/auto-assignment-settings` | `GET`/`PATCH /coordinator/assignment-schedule` |
| Service | `AutoAssignmentSettingsService` | `AssignmentScheduleService` |
| Worker stage | 3 (DIRECT) | 5 (SCHEDULE) |
| How it turns on | Only as a consequence of confirming a proposal that assigns real work (§4.6 item 6) — no request can ask for it | A coordinator picks an interval in the result modal after confirming a round |
| How it turns off | `PATCH /coordinator/auto-assignment-settings` with `enabled=false`, immediately | Same endpoint as above with `enabled=false` |
| UI wording | `Sau 2 giờ` — a wait before one ticket goes out | `Lặp lại mỗi 2 giờ` — a repeat |

Everything in the left column is unchanged. This override adds the right column;
it does not relabel the left one.

## Why it exists

The previous workspace offered "Tự động chạy lại sau khi phân việc" and sent the
chosen interval to `activation_delay`. That is a per-ticket delay from approval
to **direct** assignment. A coordinator who ticked it was told the system would
show them another table in two hours, and what actually happened was that the
system started assigning every newly approved ticket two hours after approval,
with no human in the loop. The label and the behaviour disagreed on the one
question that matters — *does a person still decide?* — so the schedule was made
real rather than the label made vaguer.

## Rules a due run follows

1. It is claimed under a row lock and **advances `next_run_at` before building
   anything**. A worker that dies mid-build skips one round instead of retrying
   the same due time forever, and two workers cannot both fire it.
2. It refuses to create a batch while a `BUILDING` or `READY` one exists.
   Two tables drawing on the same queue is not a state a coordinator can act on.
3. With nothing eligible it creates **no visible batch**, records the run and
   waits for the next interval. An empty table appearing every two hours is
   noise, not information.
4. A missed window is caught up in a single step. Coming back after two days
   down must not fire once per pass until it catches up.
5. The batch it creates is `created_by_type='SYSTEM'` with
   `requested_by_user_id = NULL`. §8.1 keeps SYSTEM and a named actor apart, and
   borrowing whoever last configured the schedule would put their name on a
   decision they were not present for. The **confirmation** of that batch is
   always a named coordinator's.
6. It uses the same batch limit, ordering, case-member limit, candidate
   snapshot, TTL, model validation and failure handling as a coordinator-created
   proposal. It is the same `create_batch` call with a different actor.
7. It never enables DIRECT auto-assignment, and configuring it never writes to
   `auto_assignment_settings`.

## Confirmation snapshots

Related change in the same revision (`1e2f3a4b5c6d`): confirming a batch now
freezes `assignment_proposal_batches.confirmation_snapshot` inside the same
transaction as the assignments it describes, and `GET
/coordinator/assignment-history` renders from that alone.

History used to read the ticket's current category, the location's current label
and the profile's current name. That rewrites the past: rename a category in
September and the August record claims a coordinator approved something they
never saw. `src/services/assignment_history_service.py` therefore contains no
`joinedload` at all, deliberately.

The repeat chosen in the result modal lands on `followup_schedule`, not in the
snapshot — it is the next thing the coordinator asked for, not part of what they
approved. It is write-once: the same answer twice is a double-click and is
tolerated, a different answer is refused.

Rounds confirmed before this revision have `confirmation_snapshot IS NULL`. They
are reported with `has_snapshot=false` and no rows rather than reconstructed
from live data, because reconstructing them is the bug being fixed.

## DIRECT activation (added 24/08/2026)

DIRECT is deliberately asymmetric, and this supersedes the earlier reading of
§4.6 item 6 in one place: an **empty** proposal is no longer confirmable.

**Off is always available.** `PATCH /coordinator/auto-assignment-settings` with
`enabled=false` takes effect at once, clears the activation provenance, audits
as `DISABLE_DIRECT_AUTO_ASSIGNMENT`, and touches nothing else — not the
recurring schedule, not existing proposals, not tickets already assigned.

**On is reachable from exactly one code path.** `confirm_batch` turns DIRECT on
when, and only when, all of the following hold in one transaction:

* the batch is `READY` and not expired,
* an authenticated, named coordinator is confirming it,
* the confirmation actually created assignments (`assigned > 0`), and
* DIRECT was off beforehand.

`continue_auto_assignment` is **gone from the confirm request**. While it
existed, the client decided whether DIRECT turned on — and a client's word is
what this rule refuses to take, since a body can be forged or replayed with no
proposal behind it. The batch column of that name survives as a *record* of
whether confirming that batch is what flipped the switch.

Consequences worth stating plainly:

* An empty batch now returns `PROPOSAL_NOTHING_TO_ASSIGN` on confirm, always.
  It used to be confirmable as the way to enable DIRECT for future tickets,
  which is backwards: an empty table is no evidence anyone reviewed any work.
* A confirmation whose rows were all taken by manual assignment first confirms
  normally but does **not** activate DIRECT. Nothing was handed out, so nothing
  was authorised.
* A batch the recurring scheduler opened does not activate DIRECT by existing.
  It activates DIRECT only if a named coordinator later confirms it.
* A second confirmation while DIRECT is already running leaves the delay and the
  original provenance alone. It authorised nothing new.

`auto_assignment_settings.activated_by_batch_id` / `activated_by_user_id` /
`activated_at` (revision `2f3a4b5c6d7e`) carry the provenance, alongside an
`ACTIVATE_DIRECT_AUTO_ASSIGNMENT` audit entry whose `entity_id` is the batch —
so the trail leads from the switch back to the table of work a human looked at.

On the frontend there is no function that can *form* an enabling request:
`updateAutoAssignmentSettings` was replaced by `disableDirectAutoAssignment`,
which hard-codes `enabled: false`. `directControl()` returns either a stop
control or a sentence, with no third shape — so a component cannot render an
enable button, greyed out or otherwise. A disabled control still advertises an
action that does not exist.
