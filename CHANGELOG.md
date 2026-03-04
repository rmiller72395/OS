# Changelog

## v4.11.0 "Obsidian Edition" (2026-03-02)
- **Global Memory (Knowledge Store):** One-shot learning layer: SQLite Knowledge Store records task success patterns (skill chain + input params) and failure correlation (error + env context). ExecutionManager consults memory before each run and adjusts strategy (increase timeout after timeouts, try alternative skill first when past failures exist). Post-flight report commits insights to the Knowledge Store after every execution. Config: `RMFRAMEWORK_KNOWLEDGE_DB` (default: `knowledge_store.db`).
- **Profit Tier:** Fix `model_hint` persistence in work_items INSERT (16 columns); Tier 1 defense-in-depth in worker loop when `risk_class != NONE` or `side_effects == EXECUTE`
- **Config:** Reduce config lock hold time (write to temp outside lock); in-memory-first lazy-flush docstring on ConfigCache
- **Shutdown:** Explicit "Drain timeout; proceeding with shutdown." log when deadline exceeded
- **Security:** Permit `issued_at` in HMAC and DB (replay hardening); backward-compat verification for legacy permits; `/approve` checks `RMFRAMEWORK_PERMIT_SECRET` at runtime; never log secret
- **Board/Director hardening:** Director output rejected if empty or over 100k chars; Director signature tail limited to 256 chars (injection/garbage reject); board manager uses mission deadline and logs per-manager exceptions; manager output capped (50k chars, 2 orders per manager); `parse_work_orders` caps input size and max 50 orders per parse
- **CEO/CFO hardening:** Gate (CFO/CISO) result capped at 256 KB and tail lines at 2 KB; veto reason capped; CEO schema input capped at 512 KB; CEO/Director JSON blobs (WORK_ORDERS_JSON, ROUTING_MAP_JSON, ROUTING_OVERRIDES_JSON) capped at 64 KB; WORK_ORDERS limited to 50; CEO STATUS must be one of APPROVED|DENIED|HOLD|NEEDS_CLARIFICATION (else warning); CEO ROUTING_OVERRIDES_JSON merged only for allowlisted tier models; CFO/CISO prompts tightened (output only required format)
- SQLite migration: `permits.issued_at`

## v4.10.0 Rollout (2026-03)
- **Execution run graph:** run_id/trace_id, spans, events in `data/runs/<run_id>.jsonl`; compact summaries in `data/missions.jsonl`; run_summary() for status/duration/cost.
- **Internal ticketing:** SQLite tickets (NEW→READY→RUNNING→DONE/FAILED/BLOCKED/CANCELED); Discord /ticket create|list|view|ready|start|block|done|cancel|retry; queue runner; crash-safe reconciliation on startup.
- **Monitoring:** MONITORING_CHANNEL_ID alerts with "What happened" / "What to do next", dashboard links, 2000-char chunking, 5-min throttle. See docs/ALERT_BUTTONS_WORKAROUND.md for button workaround.
- **Discord slash commands:** /status, /runs, /run, /pause, /resume, /stop, /ticket * (same RBAC as prefix).
- **Watchdog:** Heartbeat stall detection; if no heartbeat for health_stall_s and auto_exit_on_stall, alert and exit (Task Scheduler can restart).
- **Retention & backup:** Run log retention (log_retention_runs/days, optional gzip); backup on startup and daily (config + tickets DB); backup_keep_days prune.
- **Idempotency:** RESUME_MODE=safe_skip_completed skips execution when action_log already has (mission_id, work_item_id); step_started/step_completed events in tracing.
- **Bootstrap:** `python -m sovereign init` and `python -m sovereign self-test`; .env for secrets; SAFE_MODE; run_windows.bat in repo.

## v4.10.0 (2026-03-02)
- Permit expiry reminders (T–3 minutes) + auto-expire sweep
- Strict WorkOrder validation (fail-closed) + worker registry honored at runtime
- Owner UX: /settings, /setup_check, and manager/worker toggle commands
- SQLite migration: `permits.last_reminded_at`
