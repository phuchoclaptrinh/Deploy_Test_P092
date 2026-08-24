# V4 Agent ↔ Backend integration map

Branch `codex/v4-agent-backend-integration`, based on `origin/main` (`23f276b`).

## Branch comparison

| | Commit | Notes |
| --- | --- | --- |
| Merge base | `60fbe43` | `frontend_v2` |
| Backend base | `origin/main` `23f276b` | Newer backend: V4 shell models, migrations, services, routes |
| Agent source | `origin/TienDai` `61ffe98` | Agent V4 + Assignment Agent V4 on an older backend snapshot |

Both branches re-derive most of the backend from the same ancestor, so 161 files
are touched on both sides. `origin/main` is the authoritative backend, so this
branch is **not** a merge: only the modules that exist *solely* on `origin/TienDai`
are transferred, and every shared file stays at its `origin/main` version unless
the contract requires a change.

Deliberately **not** transferred: `scripts/log_antigravity.py`,
`scripts/log_manual.py`, `scripts/submit_log.py`, `tests/agents/test_graph.py`
(a V3 graph test duplicating `tests/test_agents/`), the `eval/` harness and the
`Self_Dev_Docs/pairwise_v4/` evaluation artifacts.

## Files taken from the Agent branch

| File | Transfer |
| --- | --- |
| `src/models/agent_schemas_v4.py` | verbatim |
| `src/agents/v4/{__init__,state,prompts,llm_client,nodes,graph,service}.py` | verbatim |
| `src/agents/v4/tools.py` | taken, then the adapter is rewritten onto the new purpose-aware backend search |
| `src/agents/analysis_dispatch.py` | taken, then rewritten to finalize through `finalize_v4()` and default to V4 |
| `src/assignment_agent/*` | verbatim (`schemas`, `prompts`, `config`, `model_client`, `validator`, `service`); later demoted to the `ASSIGNMENT_DECISION_ENGINE=AI` rollback path, with three optional fields added to `schemas.CandidateSnapshotV4`/`WorkItemV4` for the rule engine |
| `src/services/llm.py` | taken (Anthropic fallback behind OpenAI) |

`origin/TienDai` contains no Agent V4 or Assignment Agent tests — the agent work
was done under an explicit "do not write tests" constraint — so all V4 coverage
on this branch is new.

## Files retained from the Backend

Everything else, in particular the V4 shell that `origin/main` already carries and
that this branch builds on rather than replaces:

- models `assignment_proposal.py`, `auto_assignment_setting.py`,
  `ticket_relation.py`, `incident_case*.py`
  (`duplicate_dispute.py` is gone: the resident duplicate appeal was removed,
  while duplicate detection and linking stayed)
- services `v4_workflow_service.py`, `assignment_proposal_service.py`,
  `operational_timeout_service.py`, the whole Agent V3 stack
- migrations `1a2b3c4d5e6f`, `3c4d5e6f7a8b`, `4d5e6f7a8b9c`
- every existing route and response model

## Files requiring semantic manual integration

| File | Why |
| --- | --- |
| `src/config.py` | Anthropic keys; the §5.2 assignment/worker configuration block |
| `src/models/enums.py` | `AgentSeveritySource` alignment, job/proposal/relation enums |
| `src/models/agent_schemas.py` | untouched — V3 sessions keep finalizing on V3 |
| `src/services/agent_tool_service.py` | `purpose=DUPLICATE\|GROUPING` split (§2.2) |
| `src/services/agent_result_service.py` | untouched V3 path; V4 finalize lands in a new module |
| `src/database/models/{ticket,ticket_assignment,ai_analysis,assignment_proposal,incident_case}.py` | §7.1–§7.9 columns/constraints |
| `src/api/routes/tickets.py` | version-aware dispatch; actor-scoped list/detail/attachment reads and reporter-only cancel and AI-question endpoints (see `docs/v4_operations.md` §5b) |
| `src/api/routes/coordinator_tickets.py` | proposal lifecycle, job cancel, worker trigger |
| `src/services/assignment_proposal_service.py` | replace the deterministic placeholder with the real Agent |
| `src/agents/v4/tools.py` | real adapter, no dependency gaps |
| `.env.example` | Phase 6 credential scrub |

## New migrations

One new Alembic head chained onto `4d5e6f7a8b9c`:

- `5e6f7a8b9c0d_add_v4_agent_backend_contract` — §7.1–§7.9 columns, constraints and
  indexes: duplicate self-link check, `ai_analysis_runs` V4 payload columns,
  `ticket_assignments` cycle/SLA/rejection columns, the durable
  `ai_assignment_jobs` fields, `ai_assignment_job_members` partial unique index,
  proposal decision/member composite constraints, `incident_cases.series_id`.

## What was actually built

Delivered against the map above, in the order the commits land:

| Area | Module |
| --- | --- |
| Purpose-aware search (§2.2) | `src/services/agent_tool_service.py` |
| `finalize_v4()` (§1.7, §3) | `src/services/agent_result_v4_service.py` |
| Version-aware dispatch (§18.2) | `src/agents/analysis_dispatch.py` |
| Candidate snapshots (§4.1, §4.3) | `src/services/assignment_candidates.py` |
| Durable job store (§5.1, §7.4) | `src/services/assignment_job_service.py` |
| Job triggers (§4.2, §6.2) | `src/services/assignment_trigger_service.py` |
| DIRECT orchestration (§4.5, §5.2) | `src/services/assignment_direct_service.py` |
| PROPOSAL lifecycle (§4.6, §7.5) | `src/services/assignment_proposal_service.py` |
| Worker process (§5) | `src/workers/assignment_worker.py` |

Operational detail — what has to be running, how the rollout switch behaves, and
what the database now guarantees — is in `docs/v4_operations.md`.
