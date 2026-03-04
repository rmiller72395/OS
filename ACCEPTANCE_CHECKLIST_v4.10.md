# v4.10 Rollout — Acceptance Checklist

## A) Discord control plane

- [ ] **Monitoring channel:** `MONITORING_CHANNEL_ID` set; on hard failure an alert is sent with run_id, mission_id, ticket_id (if any), "What happened", "What to do next", last events, dashboard link; 2000-char chunking; same error_signature throttled within 5 min.
- [ ] **Commands:** `/status`, `/runs`, `/run <run_id>`, `/pause`, `/resume`, `/stop` work; RBAC (owner-only where required).
- [ ] **Ticket commands:** `/ticket create|list|view|ready|start|block|done|cancel|retry` work and persist to SQLite.

## B) Observability & dashboard

- [ ] **Tracing:** Each run has run_id/trace_id; events in `data/runs/<run_id>.jsonl`; run summary aggregates status/duration/cost/errors.
- [ ] **Missions JSONL:** Compact run summary appended to `data/missions.jsonl` on run end (no huge payload duplication).
- [ ] **Dashboard:** GET /health, /runs, /runs/{run_id}, /tickets, /tickets/{id}; localhost only by default; shows status, duration, cost, span/event data.

## C) Production hardening

- [ ] **WorkOrder validation:** Invalid orders (unknown worker, empty objective) → trace event + monitoring alert + held in DB; validation unit test exists.
- [ ] **Graceful shutdown:** `/stop` triggers drain, flush, exit.
- [ ] **Pause:** `/pause` stops new work; config `pause_new_work` persisted; `/resume` restores.

## D) Packaging & docs

- [ ] **Init:** `python -m sovereign init` creates dirs, config template, init DBs; prints next steps.
- [ ] **Self-test:** `python -m sovereign self-test` validates config, Discord env, test run log, ticket sample, dashboard /health.
- [ ] **Secrets:** .env or env vars; .env.example includes DISCORD_TOKEN, MONITORING_CHANNEL_ID, SAFE_MODE.
- [ ] **README:** Day 1 Bring-Up, Internal Ticketing, monitoring channel, triage playbook, dashboard, common failures.
- [ ] **Dist:** `dist/build.ps1` produces zip with app, requirements, run_windows.ps1/.bat, template, docs.

## E) Ticketing

- [ ] **Model:** ticket_id, title, description, status (NEW|READY|RUNNING|BLOCKED|DONE|FAILED|CANCELED), priority, created_at, updated_at, created_by, last_run_id, etc. in SQLite.
- [ ] **State machine:** Only READY→RUNNING; RUNNING→DONE/FAILED/BLOCKED; transitions validated.
- [ ] **Reconciliation:** On startup, RUNNING tickets without active run → FAILED (or READY if resume_mode and evidence).

## F) Safe mode & versioning

- [ ] **SAFE_MODE=1:** No paid calls, no side-effect tools; only diagnostics; announced at startup.
- [ ] **VERSION** in /status and dashboard; self-test prints version.
