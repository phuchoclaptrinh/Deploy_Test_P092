# Deploying the canonical analysis agent

Two migrations sit between the current production schema (`5b6c7d8e9f0a`) and
this build:

| Revision | What it adds |
| --- | --- |
| `6c7d8e9f0a1b` | The single-agent result columns, and the closing-out of in-flight sessions from the retired dual-runtime era. |
| `7d8e9f0a1b2c` | The mandatory human gate in front of the emergency priority (P3). |

**Do not deploy the application before both have run.** The result service
writes `final_category_id`, `p3_review_status` and the rest on every finalize;
against the old schema every analysis would fail with an undefined-column error
and every ticket would land in manual review.

Neither migration drops a column or deletes a row. The old
text/image reconciliation columns (`text_categories`, `image_categories`,
`category_match`, `red_flag_relation`) stay exactly as they are — nothing
writes them any more, and `text_categories` is only relaxed to nullable so new
rows can stop depending on it.

## 1. Audit first, while the old build is still serving

Both queries are read-only. Run them before anything else and keep the output:
they name the tickets the deploy will touch.

**Sessions parked on a resident question.** LangGraph checkpoints live in the
memory of the process that created them, so these cannot be resumed after any
deploy — by the new build or the old one. `6c7d8e9f0a1b` closes them, expires
their questions and moves their tickets to manual review.

```sql
SELECT s.id AS session_id, s.ticket_id, s.model_version, s.started_at,
       t.classification_status,
       (SELECT count(*) FROM ai_agent_questions q
         WHERE q.session_id = s.id AND q.status = 'PENDING') AS open_questions
FROM ai_analysis_sessions s
JOIN tickets t ON t.id = s.ticket_id
WHERE s.status = 'RUNNING'
ORDER BY s.started_at;
```

An empty result means nothing is in flight and the switch strands no one. A
non-empty one is the list of residents whose reports a coordinator will find in
the manual queue afterwards.

**Live P3 tickets.** These were published automatically under the old rule and
are being worked on right now. `7d8e9f0a1b2c` deliberately leaves them
published and marks them `NOT_REQUIRED`: pulling a live emergency back into a
review queue is the more dangerous of the two options. The gate applies to
everything classified from the deploy onwards.

```sql
SELECT r.ticket_id, r.run_number, r.priority_final, r.red_flag,
       t.classification_status, t.status
FROM ai_analysis_runs r
JOIN tickets t ON t.id = r.ticket_id
WHERE r.status = 'SUCCEEDED'
  AND r.priority_final = 'P3'
  AND t.classification_status = 'RESOLVED'
  AND t.status NOT IN ('COMPLETED', 'CANCELLED', 'INVALID',
                       'UNRESOLVABLE', 'LINKED_DUPLICATE')
ORDER BY r.completed_at DESC;
```

## 2. Back up

`ai_analysis_runs`, `ai_analysis_sessions`, `ai_agent_questions` and `tickets`
are the four tables either migration writes to. A full snapshot is simplest; if
operations wants a targeted export, those are the four.

## 3. Migrate

```bash
alembic current           # expect 5b6c7d8e9f0a
alembic upgrade head
alembic current           # expect 7d8e9f0a1b2c
```

## 4. Confirm the switch landed

```sql
-- No session should still be RUNNING from before the deploy.
SELECT count(*) FROM ai_analysis_sessions WHERE status = 'RUNNING';

-- Nor any question still waiting on one.
SELECT count(*) FROM ai_agent_questions q
JOIN ai_analysis_sessions s ON s.id = q.session_id
WHERE q.status = 'PENDING' AND s.status <> 'RUNNING';

-- Every historical run answers the new columns honestly.
SELECT p3_review_status, count(*) FROM ai_analysis_runs
WHERE status = 'SUCCEEDED' GROUP BY 1;
```

The first two should be `0`. The third should be entirely `NOT_REQUIRED` — no
row predating the deploy was ever held at a gate that did not exist.

## 5. Smoke test

Five paths, in this order, because each one leaves state the next reads:

1. **Independent ticket** — ordinary category and severity. Expect
   `ANALYSIS_COMPLETE`, a score, a priority, `classification_status=RESOLVED`,
   and the resident notified.
2. **Confirmed duplicate** — a second report on the same `location_id` and
   category while the first is still open. Expect `LINKED_DUPLICATE`, no
   priority, no score, no SLA on the duplicate.
3. **Uncertain duplicate** — expect `classification_status=MANUAL_REVIEW`,
   `grouping_status=WAITING_DUPLICATE_DECISION`, and the candidate snapshot
   visible in the coordinator's review panel.
4. **Management rejects the duplicate** — expect the ticket published and
   scored, `grouping_status` moving to `PENDING`, then to `GROUPED` or
   `NO_MATCH` once the background stage runs.
5. **P3 gate** — a report that scores P3, or one with a danger signal. Expect
   `p3_review_status=PENDING`, `grouping_status=WAITING_P3_MANAGEMENT_REVIEW`,
   no duplicate candidates on the run, and the ticket **not** published.
   Then confirm it (published, automation stays off) on one ticket and
   downgrade another (duplicate stage resumes, a second run appears with
   `effective_priority` set to the coordinator's choice).

## What "rollback" means now

There is no analysis-contract switch left to flip: `ANALYSIS_CONTRACT_VERSION`
and the dispatcher it fed were removed with the second runtime. Rolling back
means deploying the previous build, and the previous build cannot read the new
columns' *absence* — so a rollback needs `alembic downgrade 5b6c7d8e9f0a`,
which drops the new columns and every P3 review decision recorded in them.
Export `ai_analysis_runs` first if any of those decisions matter.
