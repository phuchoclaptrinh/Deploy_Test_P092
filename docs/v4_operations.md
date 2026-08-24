# Running v4: worker, migrations and rollout

Companion to `Self_Dev_Docs/agent_backend_contract_v4.md`. This is the operational
half — what has to be running, in what order, and what to do when it is not.

## 1. What must be running

| Process | Command | Why |
| --- | --- | --- |
| API | `uvicorn src.main:app` | Serves the resident, coordinator and technician routes. |
| Assignment worker | `python -m src.workers.assignment_worker` | Everything time-based: resident question timeouts, acceptance warnings and reassignment, DIRECT jobs, proposal expiry. |

**The worker is not optional.** Contract §5 rules out `FastAPI BackgroundTasks`
and in-process timers for the 5–10 minute assignment windows, and the reason is
operational rather than stylistic: a web process restarting mid-window drops the
ticket with no record that anything was pending. Without the worker running:

- rejected and timed-out assignments never get a replacement technician,
- resident questions never expire, so those tickets sit `RUNNING` forever,
- proposal batches never expire, so a coordinator can confirm one against a
  ten-minute-old candidate snapshot.

`POST /api/v1/coordinator/assignment-worker/run` and
`POST /api/v1/coordinator/operational-timeouts/run` force a single pass. They are
diagnostics — useful to reproduce a problem or to unstick an environment — and
are documented as such in the OpenAPI description. They are not a scheduler.

One pass, in order: timeouts → triggers → DIRECT → PROPOSAL. Each stage opens
its own database session and swallows its own exceptions, so a model outage on
the DIRECT stage cannot stop proposals from expiring.

Several workers may run at once. Jobs are claimed with `FOR UPDATE SKIP LOCKED`,
and a claim older than `ASSIGNMENT_JOB_CLAIM_TIMEOUT_SECONDS` is taken back, so a
worker that dies mid-job releases its work rather than parking a ticket forever.

```
# systemd-style, or any process supervisor
ExecStart=/srv/fixit/.venv/bin/python -m src.workers.assignment_worker

# or from cron, one pass per minute
* * * * * cd /srv/fixit && .venv/bin/python -m src.workers.assignment_worker --once
```

## 2. Configuration

Beyond the existing settings, v4 adds:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ANALYSIS_CONTRACT_VERSION` | `v4` | Which contract a **new** ticket starts on. Sessions already in flight ignore it. |
| `ASSIGNMENT_DECISION_ENGINE` | `RULE` | Which engine picks the technician. `RULE` = `RULE_ENGINE_V1`, no model call. `AI` = the contract §4–§5 model path. See §2.0. |
| `ASSIGNMENT_RULES_FILE` | `config/assignment_rules.yaml` | Where the rule set is read from. See §2.0. |
| `ASSIGNMENT_RULE_*` | — | Per-key override of that file, e.g. `ASSIGNMENT_RULE_MAX_ACTIVE_ASSIGNMENTS=5`. |
| `ASSIGNMENT_PRIMARY_MODEL` / `ASSIGNMENT_FALLBACK_MODEL` | — | §5.2, **`ASSIGNMENT_DECISION_ENGINE=AI` only**. Both required and they must differ. In production the API then refuses to start and the worker exits `2`; elsewhere both start and warn. See §2.1. |
| `ASSIGNMENT_MODEL_TIMEOUT_SECONDS` | `300` | AI engine only. One hard deadline per request, no retry, no extension. |
| `ASSIGNMENT_GRACE_SECONDS` | `300` | §6.2 window after a P1/P2 rejection. P3 skips it. |
| `ASSIGNMENT_REASSIGNMENT_CAP` | `3` | Three changes allowed; the fourth pauses the ticket. |
| `DIRECT_REQUEST_MAX_TICKET_COUNT` | `20` | Distinct tickets per DIRECT model request. |
| `PROPOSAL_TTL_SECONDS` | `600` | §4.6 item 3. |
| `INCIDENT_CASE_MAX_TICKET_COUNT` | `5` | §7.9. |
| `INCIDENT_CASE_SLA_EXTENSION_PER_EXTRA_TICKET` | `0.25` | Completion SLA factor, capped at 2.00. P3 never stretches. |
| `ACCEPTANCE_*_SECONDS` | §5.2 values | Acceptance clock, §6.4. |
| `ASSIGNMENT_WORKER_POLL_SECONDS` | `15` | Worker loop interval. |
| `ASSIGNMENT_WORKER_BATCH_SIZE` | `20` | Jobs claimed per pass. |
| `ASSIGNMENT_JOB_CLAIM_TIMEOUT_SECONDS` | `900` | When another worker may reclaim a stalled job. |

The acceptance windows and the reassignment cap are technical configuration, not
coordinator-editable options: a UI that let them drift per building would make
the SLA promises unverifiable.

### 2.0 Which engine picks the technician

`RULE` is the default and calls no model. `RULE_ENGINE_V1`
(`src/assignment_rules`) filters the candidate snapshot by the configured load
caps, orders the work items P3 → P2 → P1 and then oldest first, and picks the
minimum of a lexicographic key — fewest projected P3 first for a P3 item,
fewest projected total first for P1/P2, then longest since last assigned, then
`technician_id`. Projected load grows after every decision, so one batch still
spreads across technicians. Contract §4.1a has the full rule.

What that changes operationally:

- an assignment costs microseconds instead of up to two 300-second windows;
- the same inputs always produce the same technician, so a decision can be
  re-derived from `ai_assignment_jobs.candidate_snapshot` after the fact;
- `MANUAL_REQUIRED` no longer has a "the model timed out" cause. The remaining
  causes are business ones: no candidates at all, or every candidate over a cap
  on a P1/P2 item.

The rule set lives in `config/assignment_rules.yaml`:

| Key | Meaning | Shipped default |
| --- | --- | --- |
| `rule_version` | Written to `primary_model`/`completed_model` and to every decision | `RULE_ENGINE_V1` |
| `max_active_assignments` | Total live-work cap per technician | `null` (no limit) |
| `max_active_p1_assignments` / `..._p2_...` / `..._p3_...` | Per-priority caps; the P3 one is the urgent-workload limit | `null` |
| `allow_p3_overload_when_all_capped` | Place a P3 over the cap rather than miss its five minutes | `true` |
| `tie_break_on_last_assigned_at` | Break a load tie on idle time | `true` |

The shipped defaults set **no caps at all**, deliberately: turning the LLM off
must not also change who gets work on day one. Fill the caps in once you know
what a real technician workload looks like, and bump `rule_version` whenever a
change would make yesterday's decision come out differently.

A caps change needs a worker restart, not a deploy. `ASSIGNMENT_RULE_<KEY>`
overrides any single key for a container that ships the YAML read-only.

The file is parsed at startup in both processes, and a bad file is fatal rather
than a warning: an unknown key (`max_active_assignment`, singular) or a cap of
`0` raises `AssignmentRuleConfigError`, because the alternative failure mode —
every cap silently unset while the process reports itself healthy — is the one
nobody would notice.

To roll back to the model path, set `ASSIGNMENT_DECISION_ENGINE=AI` and restart.
`src/assignment_agent` is unchanged and still carries its own failover rule,
described next.

### 2.1 The failover pair is checked at startup

This applies while `ASSIGNMENT_DECISION_ENGINE=AI`. On `RULE` there is no model
pair to fail over, so the check is skipped and the rule file is parsed instead
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
model outage into MANUAL_REQUIRED rows or EMPTY proposal rows, and that is the
wrong answer here: "no fallback is configured" is not "the model considered this
ticket and could not place it", and a coordinator looking at the manual queue
has no way to tell the two apart. The exception propagates to the worker, which
stops. Its message carries model names only, never a key.

## 3. Rolling v4 out and back

`ANALYSIS_CONTRACT_VERSION` decides only what a **new** ticket starts on. A
paused session always resumes on the contract recorded in
`ai_analysis_sessions.model_version`, so:

- flipping to `v4` does not disturb any v3 session already in flight;
- flipping back to `v3` stops new v4 sessions starting and leaves existing v4
  sessions to finish on v4.

There is no third state and no in-place conversion: the checkpoint, the state
shape and the result schema all differ, so a session is bound for life to the
contract it started under.

A v4 technical failure is never retried on v3. The session is marked `FAILED`
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
spellings — and deletes the placeholder rows the pre-v4 deterministic proposal
builder wrote, because they cannot satisfy the new per-mode check constraints.

## 5. What the database now guarantees

These are enforced by the database, not only by the service layer, so a future
code path cannot quietly violate them:

| Guarantee | Mechanism |
| --- | --- |
| A ticket is never its own duplicate master | `ck_tickets_duplicate_not_self` |
| `LINKED_DUPLICATE` always has a master | `ck_tickets_linked_duplicate_needs_master` |
| One successful finalization per analysis session | `uq_ai_analysis_runs_one_success_per_session` (partial unique) |
| One active assignment per ticket | `uq_ticket_assignments_one_active_per_ticket` (partial unique) |
| A ticket is in at most one unfinished DIRECT job | `uq_ai_assignment_job_members_active_ticket` (partial unique) |
| A DIRECT job cannot carry batch identity, and vice versa | `ck_ai_assignment_jobs_direct_shape` / `..._proposal_shape` |
| A ticket appears once per proposal batch | `uq_assignment_proposal_item_members_batch_ticket` plus the composite FK on `(item_id, batch_id)` |
| Only `AI_AUTO` may have no human author | `ck_ticket_assignments_human_source_has_actor` |
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
| `PATCH /api/v1/coordinator/assignment-proposals/{batch_id}/items/{item_id}` | New: deselect a row or change its technician (§4.6 item 4). |
| `POST /api/v1/coordinator/assignment-proposals/{batch_id}/confirm` | Accepts optional `expected_version` for optimistic concurrency. |
| `GET /api/v1/coordinator/assignment-jobs` | New: job list for the coordinator screen. |
| `POST /api/v1/coordinator/assignment-jobs/{job_id}/cancel` | New (§6.2): take a work item back inside the grace window. |
| `POST /api/v1/coordinator/assignment-worker/run` | New, diagnostics only. |

Response compatibility: proposal items now expose `proposed_technician_id` and
`final_technician_id` separately, and keep `selected_technician_id` /
`selected_technician_name` as aliases of the final choice. Technician assignment
responses still use `reject_reason`, mapped from the renamed
`ticket_assignments.rejection_reason` column. `activation_delay` accepts both the
§7.6 spellings and the older `2H`/`5H`/`1D`/`3D` forms on input, and always
returns the §7.6 spelling.

`assignment_proposal_batches.ready_at`, `expires_at`, `continue_auto_assignment`
and `activation_delay` are now nullable in the response, because a `BUILDING`
batch genuinely has none of them yet — "not decided" and "no" are different
answers, and only a confirm answers the question.

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

`tests/test_v4/` runs the real services on SQLite, which is enough for the
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
| Analysis to `ANALYSIS_COMPLETE`, finalized | §1.7, §3 | The ticket row lock `finalize_v4()` takes is a real one. |
| Resident question, answer, same session resumes | §1.7.5 | The session survives four separate connections. |
| `DUPLICATE_EXISTING` links, no assignment | §3.1 | `ck_tickets_linked_duplicate_needs_master`. |
| Replay returns the stored run; a changed payload gives 409 | §1.7.9 | `uq_ai_analysis_runs_one_success_per_session`, the partial unique index rather than the service that wrote it. |
| Worker pass creates an `AI_AUTO` assignment | §4-§5 | `SKIP LOCKED` claiming, `ck_ticket_assignments_human_source_has_actor`, and the active-member partial unique index being released. |
| A second pass changes nothing | §5.1 | The job store, not in-process state, is what makes the pass idempotent. |
