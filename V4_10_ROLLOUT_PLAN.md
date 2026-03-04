# v4.10 Rollout Plan — One-Shot Zip

## Plan of Attack

1. **Tracing** — Add `observability/tracing.py`: run_id/trace_id, spans (span_id, parent_span_id), events to `data/runs/<run_id>.jsonl`; run summary function; single-writer/thread-safe; compact summary to missions JSONL.
2. **Ticketing** — SQLite tickets table + model; state machine (NEW→READY→RUNNING→DONE/FAILED/BLOCKED/CANCELED); queue runner with pause; crash-safe reconciliation on startup; ticket-to-mission mapping.
3. **Init + self-test** — `python -m sovereign init` (folders, config template, env validation, DB init); `python -m sovereign self-test` (config, Discord, test run log, ticket sample, dashboard /health).
4. **Secrets + SAFE_MODE** — Load .env (python-dotenv); SAFE_MODE=1 disables paid calls and side-effect tools; announce at startup.
5. **Monitoring alerts** — MONITORING_CHANNEL_ID; on hard failure send alert (run_id, mission_id, ticket_id, WHAT HAPPENED, WHAT TO DO NEXT, last 10 events, dashboard link); 2000-char chunking; dedupe same error_signature 5 min.
6. **Strict validation** — WorkOrder/Mission validation before execution; on fail: trace event + Discord alert + stop; unit tests.
7. **Dashboard** — FastAPI localhost: GET /health, /runs, /runs/{run_id}, /tickets, /tickets/{id}; simple HTML.
8. **Slash commands** — /status, /runs, /run, /pause, /resume, /stop, /ack; full /ticket *; RBAC.
9. **Resume/idempotency** — stable step_id; RESUME_MODE; plan_hash for ticket reruns.
10. **Graceful shutdown** — /stop → shutdown_requested, no new work, finish in-flight, flush, exit.
11. **Watchdog** — heartbeat trace; /health checks; stall → alert + exit.
12. **Retention/backups** — LOG_RETENTION_RUNS/DAYS, backup on startup/daily; config schema version + migration.
13. **Packaging** — dist script producing zip (app, requirements, .ps1/.bat, template, docs).
14. **README** — Day 1 Bring-Up, ticketing, triage playbook.

## Files to Add

| File | Rationale |
|------|-----------|
| `observability/__init__.py` | Package init |
| `observability/tracing.py` | Run/span/event writer; run summary; data/runs/<run_id>.jsonl |
| `config_schema.py` | CONFIG_SCHEMA_VERSION, migration, validation |
| `tickets/__init__.py` | Package init |
| `tickets/db.py` | SQLite tickets table, CRUD, state machine |
| `tickets/queue_runner.py` | Watch READY, start next, pause flag, crash-safe reconciliation |
| `dashboard/main.py` | FastAPI app: /health, /runs, /runs/{id}, /tickets, /tickets/{id} |
| `sovereign/__main__.py` | Entry: init, self-test |
| `sovereign/init.py` | Create dirs, copy config template, init DBs |
| `sovereign/self_test.py` | Config schema, Discord, test run, ticket sample, dashboard health |
| `VERSION` | Version constant |
| `data/.gitkeep` | Ensure data/ exists in zip |
| `dist/build.ps1` | Build one-shot zip |
| `tests/test_validator.py` | WorkOrder/mission validation tests |
| `tests/test_tracing.py` | Tracing writer concurrency |
| `tests/test_alert_throttle.py` | Dedup/throttle alerts |
| `tests/test_ticket_state.py` | Ticket state transitions |
| `tests/test_config_migration.py` | Config migration example |
| `tests/test_init_self_test.py` | Init/self-test smoke |

## Files to Change

| File | Changes |
|------|---------|
| `bot.py` | Integrate tracing (start_run/start_span/end_span/record_event), MONITORING_CHANNEL_ID alerts, load .env, SAFE_MODE, pause/resume/stop globals, slash commands + ticket commands, heartbeat loop, graceful shutdown, run summary to missions JSONL, ticket-to-mission start |
| `execution_models.py` | Add run_id to ExecutionContext (optional) |
| `requirements.txt` | Pin versions; add uvicorn, python-dotenv, fastapi |
| `.env.example` | Add MONITORING_CHANNEL_ID, SAFE_MODE, MODEL_PROVIDER placeholders |
| `README.md` | Day 1 Bring-Up, Internal Ticketing, monitoring channel, triage playbook, dashboard, budgets |

## Order of Operations (implementation)

1. observability/tracing.py
2. tickets/db.py + queue_runner.py
3. config_schema.py + DEFAULT_CONFIG extensions (pause, resume_mode, monitoring_channel_id, etc.)
4. sovereign/init.py + __main__.py + self_test.py
5. Monitoring alerts in bot.py (helper + call sites)
6. WorkOrder validator (complete mid-integration) + tests
7. dashboard/main.py
8. Slash commands + ticket commands in bot.py
9. Idempotency/resume, graceful shutdown, heartbeat in bot.py
10. Retention, backups, config migration, VERSION
11. dist/build.ps1, README, .env.example
12. Remaining tests + acceptance checklist
