# Operations: worker, migrations and rollout

Companion to `Self_Dev_Docs/agent_backend_contract_v4.md`. This is the operational
half — what has to be running, in what order, and what to do when it is not.

## 1. What must be running

| Process | Command | Why |
| --- | --- | --- |
| API | `uvicorn src.main:app` | Serves the resident, coordinator and technician routes. |
| Dispatch worker | `python -m src.workers.dispatch_worker` | Everything time-based: resident question timeouts and the automatic-assignment micro-batches. It no longer sweeps any assignment deadline — see `docs/assignment_lifecycle.md`. |

**The worker is not optional.** Every unit of pending work is a database row
(`dispatch_events`), and the reason is operational rather than stylistic: a web
process restarting mid-batch would drop the ticket with no record that anything
was pending. Without the worker running:

- nothing is assigned automatically, however the toggle is set;
- rejected assignments never get a replacement technician;
- resident questions never expire, so those tickets sit `RUNNING` forever.

One pass, in order:

1. **Timeouts** — resident question expiry. No assignment deadline is swept:
   the acceptance clock is gone with the acceptance step, and no start deadline
   has been approved to replace it (`docs/assignment_lifecycle.md`).
2. **Backlog** — enqueue anything eligible with no open dispatch event. The
   recovery path for tickets that became eligible while the toggle was off.
3. **Dispatch** — one micro-batch: claim, bulk-load, schedule in memory, one
   agent call for the AT_RISK subset, write in one transaction.

The poll interval **is** the micro-batch interval (§8 asks for roughly 0.5–1s).
The two slower stages do not run every tick — sweeping timeouts and scanning the
backlog at 1 Hz would spend the session budget on queries that almost always
find nothing — so each has its own cadence (30s and 120s) and the dispatch stage
runs alone in between.

Each stage opens its own session and swallows its own exceptions, so a model
outage on the dispatch stage cannot stop the timeout sweep from running.

Several workers may run at once. Events are claimed with `FOR UPDATE SKIP
LOCKED`, and a claim older than `DISPATCH_CLAIM_TIMEOUT_SECONDS` is taken back,
so a worker that dies mid-batch releases its work rather than parking a ticket
forever. Past `DISPATCH_MAX_ATTEMPTS` the event stops being retried and becomes
Building Management's — an event that has killed three workers will kill a
fourth.

`POST /api/v1/coordinator/dispatch/run-once` and
`POST /api/v1/coordinator/operational-timeouts/run` force a single pass. They
are diagnostics — useful to reproduce a problem or to unstick an environment —
and are documented as such in the OpenAPI description. They are not a scheduler.

```
# systemd-style, or any process supervisor
ExecStart=/srv/fixit/.venv/bin/python -m src.workers.dispatch_worker

# or from cron, one pass per minute (coarse: the loop is the real thing)
* * * * * cd /srv/fixit && .venv/bin/python -m src.workers.dispatch_worker --once
```

### 1.1 Outside the working shift

§3 makes the 08:00–18:00 Vietnam window a hard constraint, so between 18:00 and
08:00 no technician is eligible and the dispatch stage does nothing. Events are
**deferred**, not escalated: `available_at` is pushed to the next opening, so an
operator reading the queue at 02:00 sees "waiting for 08:00" rather than a pile
of overdue work — and Building Management is not handed a queue every morning
that the system was about to handle by itself.

## 2. Configuration

Beyond the existing settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISPATCH_SAFETY_BUFFER_SECONDS` | `1800` | §6's safety buffer. Shorter than the shortest §5 duration so it never dominates; long enough to absorb travel between two jobs in one building. |
| `DISPATCH_MICRO_BATCH_SIZE` | `20` | §8's ceiling. Capped at 20 by the settings model — a contract, not a tuning knob. |
| `DISPATCH_MICRO_BATCH_INTERVAL_MS` | `750` | §8: batches roughly every 0.5–1s. Bounded to that range. |
| `DISPATCH_CLAIM_TIMEOUT_SECONDS` | `120` | How long a claimed event may sit before another worker takes it back. Longer than a whole batch including a full agent timeout, so a slow pass is never mistaken for a dead one. |
| `DISPATCH_MAX_ATTEMPTS` | `3` | Past this an event escalates instead of being retried. |
| `AT_RISK_AGENT_ENABLED` | `true` | §7. With it off, every AT_RISK ticket takes the scheduler fallback. |
| `AT_RISK_AGENT_MODEL` | — | Falls back to `MODEL_NAME`. |
| `AT_RISK_AGENT_TIMEOUT_SECONDS` | `20` | One hard deadline per micro-batch call. |
| `AT_RISK_AGENT_MAX_CONCURRENCY` | `2` | §8: bounds how many agent calls exist at once. |
| `AT_RISK_AGENT_HISTORY_WINDOWS_DAYS` | `30,60,90` | §7's look-back windows. |
| `SUPABASE_MAX_SESSIONS` | `15` | §8's known constraint. See §2.0. |
| `API_DB_POOL_SIZE` / `API_DB_MAX_OVERFLOW` | `5` / `2` | The API's share of the quota. |
| `DISPATCH_WORKER_DB_POOL_SIZE` / `_MAX_OVERFLOW` | `2` / `1` | The worker's share. |
| `ASSIGNMENT_REASSIGNMENT_CAP` | `3` | Three changes allowed; the fourth pauses the ticket for Building Management. |
| `INCIDENT_CASE_MAX_TICKET_COUNT` | `5` | §7.9. |
| `INCIDENT_CASE_SLA_EXTENSION_PER_EXTRA_TICKET` | `0.25` | Completion SLA factor, capped at 2.00. P5 never stretches. |
| `ASSIGNMENT_WORKER_POLL_SECONDS` | `15` | Worker loop interval. |
| `ASSIGNMENT_WORKER_BATCH_SIZE` | `20` | Jobs claimed per pass. |
| `ASSIGNMENT_JOB_CLAIM_TIMEOUT_SECONDS` | `900` | When another worker may reclaim a stalled job. |

The `ACCEPTANCE_*_SECONDS` settings are **gone**, along with the acceptance step
they configured. Nothing replaced them: `start_due_at` and its grace period are
an open business decision, and a default invented here would quietly become the
policy. See `docs/assignment_lifecycle.md`.

The safety buffer and the reassignment cap are technical configuration, not
coordinator-editable options: a UI that let them drift per building would make
the schedule unverifiable.

### 2.0 The database session budget

§8's known operational constraint: the Supabase session pooler has a hard
15-session quota. The API and the dispatch worker are two processes sharing it,
and neither can see the other's usage — so each sizes its own pool from
configuration and `Settings.validate_runtime_safety()` refuses to boot when the
worst case adds up to more than `SUPABASE_MAX_SESSIONS`.

```
API_DB_POOL_SIZE + API_DB_MAX_OVERFLOW
  + DISPATCH_WORKER_DB_POOL_SIZE + DISPATCH_WORKER_DB_MAX_OVERFLOW
  <= SUPABASE_MAX_SESSIONS
```

Checked at startup rather than at the first timeout, because the first timeout
happens at peak load with residents waiting — the one moment nobody is reading
logs. Raising `SUPABASE_MAX_SESSIONS` without raising the real plan does not buy
capacity; it only moves where the failure appears.

`pool_timeout` is deliberately short (5s). When the quota really is exhausted,
failing a request quickly surfaces the problem; blocking on the pool turns it
into a cascade of slow requests that looks like an application performance issue
instead of a connection one.

(`require_assignment_failover` is false).

`ASSIGNMENT_PRIMARY_MODEL` and `ASSIGNMENT_FALLBACK_MODEL` are the one pair that
is validated before anything runs, in both processes:

- the API checks it in `validate_runtime_safety()` and refuses to start;
- the worker checks it in `verify_configuration()` and exits with status `2`
  before claiming a single job.

Both go through `src/services/assignment_decision_engine.py`, which is the only
place that reads the engine switch, and then
`src/assignment_agent/config.py::enforce_failover`, so the two cannot drift — a worker that started on a configuration the API rejects would
drain the queue into the manual pile while the deployment looked half-healthy.
Only `app_env=production` is fatal; development and test log a warning and run
without failover, so local work needs neither two model names nor two providers.

A misconfiguration raises `AssignmentConfigurationError`, which is deliberately
**not** caught by the DIRECT and PROPOSAL error handling. Those handlers turn a
model outage into escalated dispatch events, and that is the
wrong answer here: "no fallback is configured" is not "the model considered this
ticket and could not place it", and a coordinator looking at the manual queue
has no way to tell the two apart. The exception propagates to the worker, which
stops. Its message carries model names only, never a key.

## 3. Rolling the analysis agent out and back

There is one analysis pipeline and no switch in front of it. Rolling back means
deploying the previous build, not flipping a setting: `ANALYSIS_CONTRACT_VERSION`
and the dual-runtime dispatcher it fed are both gone.

One consequence is worth planning for. LangGraph checkpoints live in the memory
of the process that created them, so a session parked on a resident question
cannot be resumed after a deploy — by the new build or the old one. Migration
`6c7d8e9f0a1b` closes any that are in flight, expires their questions and moves
their tickets to manual review, and its docstring carries the read-only audit
query that lists them beforehand. Deploying while that query returns rows strands
those residents in a coordinator queue rather than in an unanswerable question.

A technical failure is never retried as something else.
and the ticket goes to `MANUAL_REVIEW`, because "we do not know what happened"
must not become a confident v3 answer on a ticket a resident is waiting for.

## 4. Migrations

One head: `5e6f7a8b9c0d` (`add_v4_agent_backend_contract`), chained onto the v4
shell `4d5e6f7a8b9c`.

```
python -m alembic heads      # -> 5e6f7a8b9c0d (head)
python -m alembic upgrade head
```

Two things to know before running it anywhere new:

**Migrations need a Supabase-compatible database.** The chain references
`auth.users`, `auth.uid()` and the `anon` / `authenticated` / `service_role`
roles. A plain PostgreSQL instance needs `scripts/postgres_test_shim.sql`
applied first — it creates the `auth` schema, an `auth.users` table, the three
`auth.*` functions and the four roles. Against a real Supabase project none of
that is needed, and the shim must not be applied there.

**`alembic/env.py` now runs one transaction per revision.** PostgreSQL refuses
to *use* an enum label added by a still-uncommitted transaction, and this chain
adds labels in one revision and references them in a later one, so a fresh
database could not be migrated at all under a single run-wide transaction. The
side effect is worth having: a failure leaves the earlier revisions applied
instead of rolling the whole chain back.

Migration `5e6f7a8b9c0d` also normalizes vocabulary in place — `MANUAL` →
`COORDINATOR_MANUAL`, `REJECTED_BY_TECHNICIAN` → `TECHNICIAN_REJECTED`,
`KEPT_LINKED` → `KEEP_LINKED`, and the activation delays to their §7.6
spellings. Revision `8e9f0a1b2c3d` then removes the proposal architecture
outright and rewrites `assignment_source` onto the new vocabulary; see
`docs/assignment_replacement.md` §1.

## 5. What the database now guarantees

These are enforced by the database, not only by the service layer, so a future
code path cannot quietly violate them:

| Guarantee | Mechanism |
| --- | --- |
| A ticket is never its own duplicate master | `ck_tickets_duplicate_not_self` |
| `LINKED_DUPLICATE` always has a master | `ck_tickets_linked_duplicate_needs_master` |
| One successful finalization per analysis session | `uq_ai_analysis_runs_one_success_per_session` (partial unique) |
| One active assignment per ticket | `uq_ticket_assignments_one_active_per_ticket` (partial unique) |
| A ticket has at most one open dispatch event | `uq_dispatch_events_open_ticket` (partial unique) — this is what makes enqueue idempotent |
| A technician holds at most one IN_PROGRESS ticket | `uq_ticket_assignments_one_in_progress_per_technician` (partial unique) — §3, in the database because two concurrent `start` calls cannot see each other |
| P5 never enters the automatic workflow | `ck_dispatch_events_no_emergency` — enforced by the table that would carry it |
| A risk assessment scores each criterion 0–4 | `ck_ticket_risk_assessments_*_range` — the rubric's scale, in the database |
| A ticket's assessment revisions are numbered once each | `uq_ticket_risk_assessments_ticket_revision` — two concurrent re-scores both read revision 3 |
| A confirmed apartment count is 1–5 | `ck_ticket_risk_assessments_unit_count_range` — a case holds five, so anything else counted something other than case members |
| A claimed dispatch event can always be reclaimed | `ck_dispatch_events_claim_has_expiry` |
| An assigned event names its assignment and technician | `ck_dispatch_events_assigned_shape` |
| An escalated event says why | `ck_dispatch_events_escalated_shape` |
| An at-risk decision is either the agent's or the fallback's | `ck_at_risk_decisions_source` |
| Only the three automatic sources may have no human author | `ck_ticket_assignments_human_source_has_actor` |
| An enabled auto-assignment switch names who enabled it | `ck_auto_assignment_settings_enabled_has_actor` |
| A case series has a stable order | `uq_incident_cases_series_sequence` |

Duplicate *detection and linking* are unchanged. What is gone is the resident
appeal on top of them: `7a8b9c0d1e2f` drops the `duplicate_disputes` table and
`tickets.duplicate_disputed_at`, so the "one open duplicate review per ticket"
index no longer exists because there is no review to open.

## 5b. Ticket visibility: the private AI phase

A report is private to the person who sent it while classification is still
running, and is published the moment classification finishes:

    private  <=>  classification_status IN (PENDING, PROCESSING)

That window covers both the analysis itself and the time the Agent spends
waiting for an answer to a follow-up question. Everything else is published:
`RESOLVED`, `MANUAL_REVIEW`, `FAILED`, and any invalid terminal outcome
regardless of which of those two the v3/v4 path records. There is no separate
persisted "published" flag — publication is derived, so the existing finalize
paths stay the single source of truth.

| Actor | Private AI phase | Published |
| --- | --- | --- |
| The reporter | Lists and reads it, reads its photos and pending question, answers the question, cancels while the status allows | Reads it; reporter-only actions stay available when otherwise valid |
| Another resident of the same apartment | No list row, no count, no detail, no attachment, no AI question | Reads it and its attachments; may never cancel it or answer its questions |
| A resident of another apartment | No access | No access |
| Coordinator / Building Management | Not in the list or counts; detail, attachments and human-facing mutations all read as not-found | Normal coordinator workflow |
| Internal Agent and workers | Full access, unchanged | Unchanged |

Where it lives:

- `src/services/ticket_visibility.py` holds the rule once, as a SQL predicate
  for list queries and a Python check for a single loaded row.
- `TicketRepository` exposes actor-scoped reads
  (`list_resident_tickets`, `get_resident_visible_ticket`,
  `list_coordinator_tickets`, `get_coordinator_visible_ticket`) next to the
  unscoped internal reads (`get_resident_ticket`, `get_coordinator_ticket`)
  that background analysis and the workers keep using.
- The predicate is applied **before** `count`, `offset` and `limit`, so an
  invisible row never lands in a total and never consumes a page slot.
- Cancel and both AI-question endpoints re-check the reporter identity in the
  backend. `available_actions` is actor-aware, but it is a UI hint, never the
  authorization.
- Unauthorized reads return not-found rather than forbidden, so a ticket ID
  cannot be probed for existence.
- `8b9c0d1e2f3a` aligns the ticket, attachment and status-history RLS policies
  with the same rule, and keeps `ai_agent_questions` closed to direct client
  access.

## 6. API changes

| Endpoint | Change |
| --- | --- |
| `POST /api/v1/tickets/{id}/duplicate-review` | **Removed.** The resident duplicate appeal no longer exists. |
| `POST /api/v1/tickets/{id}/duplicate-dispute` | **Removed**, not deprecated — the alias is gone with the endpoint. |
| `GET /api/v1/coordinator/duplicate-disputes` | **Removed** along with the Building Management dispute queue. |
| `POST /api/v1/coordinator/duplicate-disputes/{id}/resolve` | **Removed.** |
| `POST /api/v1/coordinator/tickets/{id}/duplicate-link` | Unchanged: coordinator duplicate *linking* is not an appeal and stays. |
| `GET /api/v1/coordinator/visual-assignment/board` | The Visual Assignment board (§1). Replaces the proposal endpoints. |
| `POST /api/v1/coordinator/visual-assignment/confirm` | All placements in one transaction; 409 with `details.failures` on any §3 violation. |
| `PUT /api/v1/coordinator/auto-assignment` | The ON/OFF toggle (§2). Requires `acknowledged` to enable. |
| `GET /api/v1/coordinator/dispatch/at-risk-decisions` | What the automatic path decided, and whether an agent or the fallback decided it (§7). |
| `GET /api/v1/coordinator/dispatch/events` | The automatic queue and its outcomes. |
| `GET /api/v1/technician/queue` | §4: the ordered work queue behind "Làm ngay" / "Tiếp theo". |
| `POST /api/v1/coordinator/dispatch/run-once` | Diagnostics only: one micro-batch. |
| `GET/POST /api/v1/coordinator/assignment-proposals*` | **Removed** with the proposal architecture (§9). |
| `GET/PATCH /api/v1/coordinator/assignment-schedule` | **Removed**: there is no recurring draft schedule any more. |
| `GET /api/v1/coordinator/assignment-history*` | **Removed**: history was a view over proposal snapshots. |
| `GET/POST /api/v1/coordinator/assignment-jobs*` | **Removed**; `dispatch/events` answers the same question. |
| `POST /api/v1/coordinator/assignment-worker/run` | **Removed**; `dispatch/run-once` replaces it. |

Response compatibility: technician assignment responses still use
`reject_reason`, mapped from the `ticket_assignments.rejection_reason` column.

Two resident fields changed, and the change is the point rather than a rename
(§4): `estimated_resolution_text` and `expected_resolution_at` are **gone**,
replaced by `progress_text` (what is happening now) and `expected_start_at`
(when a technician is expected to begin). `planned_finish_at` exists on the
assignment but is absent from every resident payload — it is internal capacity
arithmetic and must never be presented as a completion promise. A client still
reading the old keys would be showing a promise nobody is making, which is why
they were removed rather than deprecated.

Full contracts: `docs/assignment_replacement.md` §2.

## 7. Sanitization boundaries

Two places hand model-facing data across a tenancy boundary, and both are
deliberately conservative:

- `search_related_tickets` returns another unit's ticket. The summary is built
  from Category, asset label, status and Priority; the resident description is
  not used at all, and status history carries transitions and timestamps but
  never the actor or the free-text reason. See `AgentServiceBase._safe_summary`
  — that is the one place to add a real text summary later, and it needs a
  redaction step rather than a truncation.
- `issue_summary` on an assignment work item is built the same way. It is both
  the PII surface and the prompt-injection surface on that path, and the prompt
  marks it explicitly as data rather than instructions.

Client errors never contain a prompt, a stack trace or a raw model response
(§9). Raw output is kept on `ai_assignment_jobs.raw_model_output` for audit.

## 8. Verifying v4 against a disposable PostgreSQL

`tests/test_workflow/` runs the real services on SQLite, which is enough for the
business rules but proves nothing about the parts of the contract the database
enforces: `SELECT ... FOR UPDATE`, `FOR UPDATE SKIP LOCKED`, the partial unique
indexes, and the per-mode check constraints. `tests/e2e_postgres/` runs the
same flows against real PostgreSQL.

Every model call is scripted. The suite makes no network request and needs no
API key.

### 8.1 It truncates, so it has to be sure

The suite truncates every table the v4 flows write — that is what makes it
repeatable — so the question of *which* database it is pointed at is the most
important thing in the package. **A URL cannot answer that question.** A
migrated Railway, Neon or RDS database has an ordinary PostgreSQL URL on an
ordinary host; so does a colleague's staging copy on localhost.

Two things are therefore required, and both have to be true:

1. **A disposable database name.** Exactly `fixit_v4_e2e`, or any name ending
   in `_e2e`.
2. **The sentinel row**, planted by `scripts/postgres_test_shim.sql`:
   `public.v4_e2e_disposable_guard` must contain the marker for the key
   `suite`. Applying that file is the act of consent — it is a deliberate
   command a person runs against a database they chose — and the sentinel is
   what carries that consent forward to every later `pytest` run.

The sentinel is authoritative. The name is an extra layer, and so are the
checks that came before it: the dedicated `V4_E2E_DATABASE_URL` variable, a
PostgreSQL scheme, no managed-provider host (Supabase, Neon, RDS, Railway,
Render, Azure), not the `DATABASE_URL` from `.env`, and not `APP_ENV=production`.

Two properties are worth stating explicitly, because they are what make the
sentinel meaningful:

- **The fixture never creates or repairs it.** It only reads it. A fixture that
  could plant its own permission slip would not be a gate.
- **It is re-read immediately before every `TRUNCATE`**, not cached from
  fixture setup, and the guard table is never in the truncated set and has no
  foreign keys, so no `CASCADE` can reach it.

Applying the shim is mandatory *before* Alembic as well: the migration chain
references `auth.users` and the Supabase roles, and without them `alembic
upgrade head` fails on the first foreign key onto `auth.users` (§4).

An unset `V4_E2E_DATABASE_URL` skips the suite. Everything else — an unsafe
URL, a missing sentinel, an unmigrated schema — *fails*, because a silent skip
is how an unverified claim gets made.

The gate itself is tested in `tests/e2e_postgres/test_safety_gate.py`, which
runs in the normal suite against fake connections. It has no PostgreSQL and no
marker of its own, so it can prove the rejection paths without needing a
database it is allowed to destroy.

### 8.2 The exact procedure

```bash
# 1. A throwaway cluster. Anywhere outside the repo; a container works too.
export PGBIN="/c/Program Files/PostgreSQL/16/bin"     # or your platform's path
export PGDATA="$TMPDIR/fixit-e2e/pgdata"
"$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust -E UTF8
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-p 55433 -c listen_addresses=127.0.0.1" -l "$PGDATA/../pg.log" -w start

# The name is checked by the suite. Anything not ending in _e2e is refused.
"$PGBIN/createdb" -h 127.0.0.1 -p 55433 -U postgres fixit_v4_e2e

export E2E_URL="postgresql://postgres@127.0.0.1:55433/fixit_v4_e2e"

# 2. Mandatory, and only ever against a database you are willing to lose.
#    Adds the Supabase surface the migrations need (§4) and plants the
#    disposable sentinel. Idempotent.
"$PGBIN/psql" -v ON_ERROR_STOP=1 -f scripts/postgres_test_shim.sql -d "$E2E_URL"

# 3. Migrate. Alembic reads DATABASE_URL, so it is set for this command only.
DATABASE_URL="$E2E_URL" APP_ENV=test python -m alembic upgrade head

# 4. Run the suite.
V4_E2E_DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:55433/fixit_v4_e2e"   python -m pytest tests/e2e_postgres -v -m postgres_e2e

# 5. Throw the cluster away. Check the path first - this is a delete.
"$PGBIN/pg_ctl" -D "$PGDATA" -m immediate stop && rm -rf "$PGDATA"
```

The suite is repeatable: it truncates before seeding, so step 4 can be run any
number of times without step 1. Skipping step 2 leaves the database without a
sentinel, and step 4 then fails without executing a single `TRUNCATE`.

### 8.3 What it covers

| Flow | Contract | What only PostgreSQL proves |
| --- | --- | --- |
| Analysis to `ANALYSIS_COMPLETE`, finalized | §1.7, §3 | The ticket row lock `finalize()` takes is a real one. |
| Resident question, answer, same session resumes | §1.7.5 | The session survives four separate connections. |
| `DUPLICATE_EXISTING` links, no assignment | §3.1 | `ck_tickets_linked_duplicate_needs_master`. |
| Replay returns the stored run; a changed payload gives 409 | §1.7.9 | `uq_ai_analysis_runs_one_success_per_session`, the partial unique index rather than the service that wrote it. |
| Worker pass creates an `AI_AUTO` assignment | §4-§5 | `SKIP LOCKED` claiming, `ck_ticket_assignments_human_source_has_actor`, and the active-member partial unique index being released. |
| A second pass changes nothing | §5.1 | The job store, not in-process state, is what makes the pass idempotent. |

---

## 11. Risk scoring v2

`docs/risk_scoring_v2.md` is the contract; this is what running it needs.

### The migrations

Three revisions, in order, and the first is a hard cutover:

| Revision | What it does | Reversible |
| --- | --- | --- |
| `a1b2c3d4e5f7` | Deletes the operational ticket graph, rebuilds `priority_level_enum` with five labels, drops every v1 scoring column and `scoring_rule_versions`, renames the `p3_review_*` gate columns, creates `ticket_risk_assessments`. | **No.** Forward-only. |
| `b2c3d4e5f6a8` | Adds `closed_at` / `closed_reason` to `incident_cases`. | Yes. |
| `c3d4e5f6a8b9` | Repoints the dispatch check constraint from `P3` to `P5`. | Yes. |

**`a1b2c3d4e5f7` deletes data, deliberately.** Every ticket, assignment,
analysis run, dispatch event and incident case goes. Nothing maps a v1 ticket
onto the v2 model: a severity does not imply five criteria, and a fabricated
`human_safety` score would make every priority derived from it a guess presented
as a record. What survives is the building and the people in it — accounts,
apartments, floors, locations, technician profiles and skills, and the category
catalog.

Run it against a database you are willing to lose the ticket history of. On a
shared or production database, take a dump first; the revision's `downgrade`
raises rather than pretending it can put anything back.

### What changed operationally

* **The priority scale inverted.** P5 is the emergency (five wall-clock
  minutes, handled by hand); P1 is the routine band. Every dashboard, alert and
  saved query written before this means the opposite of what it says.
* **The dispatch queue orders P4 → P3 → P2 → P1.** P5 has no rank because it is
  never enqueued.
* **SLA is measured at `started_at`, not `completed_at`,** and P1–P4 consume
  service minutes that pause outside 08:00–18:00. Compliance covers P1–P4; P5 is
  reported beside the rate, never inside it.
* **`scoring_rule_versions` is gone.** There is no longer a runtime-editable
  definition of a priority. Changing the rubric means changing
  `src/domain/risk_scoring.py` and `docs/risk_scoring_v2.md` together, and
  bumping `RUBRIC_VERSION` so existing rows stay readable as what they were.

### Running the chain against real PostgreSQL

Not yet done in this repository. The chain resolves to a single head across 39
revisions and `tests/test_migrations/` asserts the shape of `a1b2c3d4e5f7`
against ORM metadata, but SQLite is what the test suite runs on, and SQLite
never exercises the enum rebuild — which is the one step with no equivalent
there and the one most likely to fail on a real server.

Do it on a database you are willing to lose the ticket history of. A throwaway
local one is enough and is what the safety gate in `tests/e2e_postgres/`
assumes:

```bash
createdb -h localhost -U postgres fixit_rubric_v2_check
export CHECK_URL="postgresql+psycopg://postgres@localhost:5432/fixit_rubric_v2_check"

# Supabase surface the chain expects: auth.users, auth.uid(), the four roles.
psql -v ON_ERROR_STOP=1 -f scripts/postgres_test_shim.sql -d "$CHECK_URL"

DATABASE_URL="$CHECK_URL" ALLOW_LIVE_MIGRATION=true python -m alembic upgrade head
```

Then check the five things the cutover is actually for. Each should return the
row shown and nothing else:

```sql
-- 1. Five bands, and P5 among them.
SELECT enumlabel FROM pg_enum e
  JOIN pg_type t ON t.oid = e.enumtypid
 WHERE t.typname = 'priority_level_enum'
 ORDER BY e.enumsortorder;
--> P1, P2, P3, P4, P5

-- 2. The revision table exists with its range constraints.
SELECT conname FROM pg_constraint
 WHERE conrelid = 'ticket_risk_assessments'::regclass AND contype = 'c'
 ORDER BY 1;
--> one ck_..._range per criterion, plus risk_score and unit_count

-- 3. The v1 vocabulary is gone from the ticket table.
SELECT column_name FROM information_schema.columns
 WHERE table_name = 'tickets'
   AND column_name IN ('severity', 'severity_source', 'base_score',
                       'location_bonus', 'density_bonus', 'red_flag_detected');
--> no rows

-- 4. Runtime-editable scoring is gone entirely.
SELECT to_regclass('public.scoring_rule_versions');
--> NULL

-- 5. The dispatch queue bars the emergency band, not P3.
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid = 'dispatch_events'::regclass AND conname LIKE 'ck_%emergency%';
--> ck_dispatch_events_no_emergency ... (priority <> 'P5')
```

Two things to know before pointing this at anything shared.

`a1b2c3d4e5f7` has no `downgrade`. It raises rather than pretending it can put
back what it deleted, so a dump taken beforehand is the only way back.

`ALLOW_LIVE_MIGRATION=true` is the repo's own opt-in, checked in
`src/database/migration_safety.py`. It is required only for commands that may
change the database (`upgrade`, `downgrade`, `stamp`, and unknown/programmatic
commands). Read-only inspection such as `current`, `history`, `heads` and
`check` remains available with the flag off. On its own the flag only asks
whether you meant to migrate online at all, and it used to be the last word:
`APP_ENV` describes the checkout, not the server, so a development `.env`
pointing at hosted Supabase satisfied both gates and would have run
`a1b2c3d4e5f7` against the shared database without a question.

So a non-local `DATABASE_URL` now also has to be named:

```bash
MIGRATION_TARGET="db.<project>.supabase.co:5432/postgres" DATABASE_URL="postgresql+psycopg://…" ALLOW_LIVE_MIGRATION=true python -m alembic upgrade head
```

The fingerprint is `host[:port]/database` — no credentials, so it is safe in a
shell history and in the migration log. It must match what `DATABASE_URL`
resolves to, which is the point: type the staging clone's fingerprint while
`.env` still points at production and the run is refused, on the near side of
the delete. Localhost is exempt; the development loop runs many times a day and
ceremony there would only be kept permanently satisfied.

Set all three on the migrating command. None of them belongs in `.env` — that
is where two copies of `ALLOW_LIVE_MIGRATION=true` were found sitting.

`alembic stamp` is not part of any procedure here. It moves the version pointer
without running the DDL, so it turns a schema mismatch into a silent one and
disarms the boot guard in `src/database/schema_version.py`.

### The invariant to alert on

Three queries that must always return zero rows. They are asserted in
`tests/test_workflow/test_emergency_manual_only.py`; running them against
production is the cheap version of the same check.

```sql
SELECT a.id FROM ticket_assignments a JOIN tickets t ON t.id = a.ticket_id
 WHERE a.is_active AND t.priority = 'P5';

SELECT m.ticket_id FROM incident_case_members m JOIN tickets t ON t.id = m.ticket_id
 WHERE t.priority = 'P5';

SELECT e.id FROM dispatch_events e JOIN tickets t ON t.id = e.ticket_id
 WHERE e.is_open AND t.priority = 'P5';
```

A row in any of them means a P5 reached an assignment path, which is the one
thing `src/domain/assignment_guard.py` exists to prevent.

## 12. The risk v2 cutover runbook

§11 describes what the three revisions do. This is the order to do them in on a
database that holds real data, and the checks that decide whether to continue at
each step.

The shape of the risk is unusual and worth stating once: `a1b2c3d4e5f7` deletes
the entire operational ticket graph and raises rather than offering a
`downgrade`. There is no rollback inside the database. The only way back is a
dump taken beforehand and proven to restore. Everything below follows from that.

### 12.1 The release candidate

One commit, and every command below runs from it. Before starting:

| Check | Command | Expected |
|---|---|---|
| Backend | `python -m pytest -q` | all pass |
| Frontend | `cd frontend && npm test` | all pass |
| Types | `cd frontend && npx tsc --noEmit` | silent |
| Build | `cd frontend && npm run build` | succeeds |
| Lint | `python -m ruff check .` | `All checks passed!` |
| One head | `python -m alembic heads` | `c3d4e5f6a8b9 (head)` |

Also confirm by eye, because none of these are things a test can decide for you:
the rubric weights are the ones you signed off — 25 / 5 / 50 / 15 / 5 for
human safety, property spread, essential function, affected scope and
deterioration speed, in `src/domain/risk_scoring.py` — the simulator is
present and its sample scenario runs on `SERVICE_HOURS_RISK_V2`, and
`src/database/schema_version.py` is wired into the app's lifespan so a
half-applied chain fails at boot instead of at the first request.

### 12.2 Inventory, before anything is touched

```bash
python scripts/premigration_inventory.py \
    --database-url "$MIGRATION_PG_URL" \
    --out outputs/inventory-before.md
```

Read-only — it runs `SELECT` and `count(*)` and nothing else, so it is safe to
point at production. It reports the server version, the current revision, a row
count for each of the eighteen tables the cutover empties, a row count for each
table that must survive, and the audit-log split.

**Stop conditions.** The revision must read `9f0a1b2c3d4e`; anywhere else in the
chain and the rest of this document does not apply. And the last section of the
report is a checklist a person has to answer, not the script:

If any ticket history is needed for lookup afterwards, **the plan stops here.**
The answer is then an archive migration, and `a1b2c3d4e5f7` as written is the
wrong tool. Do not proceed and plan to "export it later" — there is no later.

### 12.3 Back up, and prove the backup

Prefer the Supabase **direct** host for migrations and `pg_dump`. If the machine
cannot reach the direct IPv6 endpoint, Supabase also documents the Supavisor
**session-mode** pooler on port `5432` as the backup/restore fallback. Do not use
the transaction-mode pooler on port `6543` for this procedure. Whichever
endpoint is used must be named exactly in the inventory and migration record;
direct and session-mode hosts have different `MIGRATION_TARGET` fingerprints.

Strip the SQLAlchemy driver from the dump URL: `pg_dump` wants
`postgresql://`, not `postgresql+psycopg://`.

`pg_dump` must be at least the server's major version. The client here is 16.

```powershell
pg_dump --dbname="$env:MIGRATION_PG_URL" --format=custom --no-owner --no-acl `
        --file="backup-before-risk-v2.dump"

Get-FileHash "backup-before-risk-v2.dump" -Algorithm SHA256
pg_restore --list "backup-before-risk-v2.dump"
```

A dump that has not been restored is not a backup, it is a file. Restore it into
the staging database and keep that database — §12.5 runs the rehearsal on it.

```powershell
pg_restore --clean --if-exists --no-owner --no-acl `
           --dbname="$env:STAGING_PG_URL" "backup-before-risk-v2.dump"
```

### 12.4 Rehearsal one: an empty PostgreSQL

This is the cheap one and it catches the expensive failure. SQLite is what the
test suite runs on, and SQLite never exercises the enum rebuild — the one step
with no equivalent there and the one most likely to fail on a real server. See
§11 for the `createdb` / `postgres_test_shim.sql` / `upgrade head` sequence.

### 12.5 Rehearsal two: the restored copy

The same chain, over real data shapes, from the release candidate:

```powershell
$env:DATABASE_URL = $env:STAGING_APP_DATABASE_URL
$env:APP_ENV = "development"
$env:MIGRATION_TARGET = "<staging host:port/db>"

python -m alembic current      # 9f0a1b2c3d4e
$env:ALLOW_LIVE_MIGRATION = "true"
python -m alembic upgrade head
Remove-Item Env:ALLOW_LIVE_MIGRATION
python -m alembic current      # c3d4e5f6a8b9
Remove-Item Env:MIGRATION_TARGET
```

Time this run. The maintenance window in §12.7 comes from it plus at least 100%.

A note on `APP_ENV`. The gate in `src/database/migration_safety.py` still
requires `development` or `test`, so a production cutover means setting it for
the duration of that one command. That is a real weakness of using `APP_ENV` to
describe a *database*, and it is why `MIGRATION_TARGET` was added: the
fingerprint is what actually identifies the server, and it is checked against
what `DATABASE_URL` resolves to. If a stronger interlock is wanted before
touching production, the place to add it is that module — a required project
ID, checked the same way.

### 12.6 Acceptance on staging

Run the five schema queries and the three zero-row invariants in §11, and also
confirm:

- The `priority_level_enum` has exactly `P1 P2 P3 P4 P5`.
- `severity`, `severity_source`, `base_score`, `location_bonus`,
  `density_bonus`, `red_flag_detected` are gone from `tickets`.
- `ticket_risk_assessments` exists with a range constraint per criterion.
- `to_regclass('public.scoring_rule_versions')` is `NULL`.
- `dispatch_events` bars `P5`, not `P3`.
- The backend starts — meaning `assert_schema_is_current` passed.

Then run the inventory again and diff it:

```bash
python scripts/premigration_inventory.py --database-url "$STAGING_PG_URL" \
    --out outputs/inventory-after.md
diff outputs/inventory-before.md outputs/inventory-after.md
```

Every doomed table must read 0. Every surviving table must be **byte-identical**
to the before file. A change there means the cutover reached further than it was
meant to, and that is a stop.

Smoke test, in this order, because each step depends on the one above it:

1. Sign in as a resident and as Building Management.
2. Create a ticket.
3. The Agent scores all five criteria.
4. A clarifying question is asked and can be answered.
5. A duplicate of a P5 is recorded but does **not** join a case.
6. A P5 is not assigned to anyone.
7. A P4 is assigned normally.
8. The simulator page loads and a run completes.
9. The UI shows P1–P5 and the risk breakdown, and the reports page counts P4 and
   P5 tickets in its distribution chart.

### 12.7 Production

Only after §12.6 passes in full.

1. Maintenance mode on.
2. Stop the backend, the dispatch worker, and anything else that creates tickets.
3. Take the final dump and prove the restore (§12.3). This is the one that
   matters; the earlier one was a rehearsal artifact.
4. Record the revision and the row counts (§12.2).
5. Run the chain from the release-candidate commit, naming the target.
6. Post-checks (§12.6).
7. Start the backend. The schema guard must pass.
8. Smoke test.
9. Maintenance mode off.

Do not open the system to users before the smoke test finishes. Anything created
after the cutover is data a rollback cannot merge back.

### 12.8 Rollback

There is no `downgrade`. The procedure is:

1. Stop everything.
2. Create a new database.
3. Restore the pre-migration dump into it.
4. Point `DATABASE_URL` at the restored database.
5. Deploy the pre-v2 build.
6. Confirm `alembic current` reads `9f0a1b2c3d4e`.

### 12.9 Go conditions

All of these, and the last one is a person's, not a check's:

- [ ] Rubric weights confirmed; simulator present; schema guard wired in.
- [ ] Release candidate green on all six checks in §12.1.
- [ ] Inventory taken and the revision reads `9f0a1b2c3d4e`.
- [ ] Dump taken through the direct host, or documented Supavisor session mode
      on port 5432 when direct IPv6 was unavailable, and **restored** successfully.
- [ ] Both rehearsals passed; the second one over real data.
- [ ] Nobody needs the old ticket history for lookup.
- [ ] Everyone else on the database has been told.
- [ ] Losing the entire ticket history is accepted, explicitly.

Production is a separate approval from staging. It cannot be undone.
