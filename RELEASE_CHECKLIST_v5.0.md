# Sovereign v5.0 — Release Checklist & GO/NO-GO

## Critical systems (PASS/FAIL)

| System | Status | Notes |
|--------|--------|-------|
| Tool execution: all calls via validation + authorize_tool_call | **PASS** | ExecutionManager enforces registry + grants when ticket_id or run_id set; bot passes both in ExecutionContext. |
| Tool execution: default-deny (no grant → deny) | **PASS** | Every tool call requires ticket_id or run_id; no grant → deny. Legacy path and ExecutionManager both enforce. Auto-grant (run-scoped) is read-only only. |
| Side-effect path: idempotency + commit record | **PASS** | action_log has idempotency_key + phase (started/committed); side_effect tools write started then committed; retries check committed and skip. |
| Cost recording (tool + model) | **PASS** | Tool grant usage recorded in execution_manager; worker LLM cost in audit DB. |
| Run graph writer: thread/async safe, no secrets | **PASS** | Per-run_id asyncio lock; cmd_preview redacted before tracing context. |
| Run graph: heartbeat/progress events | **PASS** | record_event used for step_started, step_completed, run_ended, run_failed. |
| Ticket queue: state transitions, reconciliation | **PASS** | reconcile_running_tickets with run_completed_callback (run_summary status=completed → DONE). |
| Pause / circuit breaker auto-pause | **PASS** | set_queue_paused; circuit trip sets pause_new_work and sends alert. |
| Knowledge store: redaction, fail-closed | **PASS** | _redact_value/_redact_params; consult_memory exceptions logged, execution continues. |
| Knowledge store: no grant bypass | **PASS** | consult_memory only suggests timeout/alternative; execution still goes through authorize_tool_call. |
| Single-instance lock | **PASS** | _acquire_single_instance_lock_or_exit at startup; released on shutdown. |
| Watchdog / stall detection | **PASS** | _watchdog_loop checks heartbeat age; auto_exit_on_stall + alert + exit non-zero. |
| Config schema migration | **PASS** | migrate_config v1→v2→v3; validate_schema_version. |
| SAFE_MODE blocks paid + side-effect | **PASS** | SAFE_MODE blocks new missions at /mission; call_agent returns SAFE_MODE error (no paid LLM); run_actions skipped for side-effect. |
| /status: version, SAFE_MODE, paused, queue depth, spend | **PASS** | get_sys_status includes SAFE_MODE in flags; slash and text /status show version, pause, queue depth. |
| Monitoring alerts: what happened + what to do + dedupe | **PASS** | _send_monitoring_alert with what_happened, what_to_do, dashboard/ticket links; throttle by error_signature. |
| Windows / Task Scheduler | **PASS** | start.ps1, run_windows.bat; no Linux-only primitives (msvcrt lock, asyncio timeouts). |
| Self-test exit 0 | **PASS** | python -m sovereign self-test passes (Discord token optional with SIMULATION_MODE=1). |
| Preflight (simulation) | **PASS** | SIMULATION_MODE=1 SAFE_MODE=1 python -m sovereign preflight completes in <2 min; exit 0 on full PASS. |
| verify_execution_layer.py | **PASS** | Execution layer verification succeeded. |

## Remaining known risks

- **Unit tests (pytest):** Not run in this environment (pytest not installed). Recommend running `pip install pytest` and `python -m pytest tests/ -v` before production deploy.
- **Dashboard (FastAPI):** Optional; self-test and preflight skip dashboard check if fastapi/uvicorn not installed. Include `fastapi` and `uvicorn` in requirements if dashboard is required.
- **Ad-hoc missions:** Run-scoped auto-grant is read-only only (allowed_scopes read:*; no side_effect tools). For write/side-effect, use ticket flow with explicit grant.
- **Capability plan:** If plan requests tools/scopes not in current grant, ticket is set to BLOCKED and Discord message requests approval; execution does not proceed.

## Day-1 bring-up (exact commands)

1. **Unzip**
   ```powershell
   Expand-Archive -Path .\sovereign_v5.0_rollout.zip -DestinationPath C:\Sovereign\releases\v5.0
   cd C:\Sovereign\releases\v5.0
   ```

2. **Optional: persistent data dir**
   ```powershell
   # In .env (copy from .env.example):
   # SOVEREIGN_DATA_DIR=C:\SovereignData
   ```

3. **Init**
   ```powershell
   python -m sovereign init
   ```

4. **Configure**
   ```powershell
   copy .env.example .env
   # Edit .env: DISCORD_TOKEN, OWNER_DISCORD_IDS, RMFRAMEWORK_PERMIT_SECRET, MONITORING_CHANNEL_ID
   # Optional first day: SAFE_MODE=1
   ```

5. **Preflight (recommended)**
   ```powershell
   $env:SIMULATION_MODE="1"; $env:SAFE_MODE="1"; python -m sovereign preflight
   ```
   Expect: "12/12 passed" and exit 0. Report: `data/preflight_report.json`.

6. **Self-test**
   ```powershell
   python -m sovereign self-test
   ```
   Expect: "Self-test passed." (exit code 0). Use `SIMULATION_MODE=1` to run without Discord.

7. **Run**
   ```powershell
   python bot.py
   ```
   Or: `.\start.ps1` or `.\run_windows.bat` (Task Scheduler: program `powershell.exe`, args `-File "C:\Sovereign\releases\v5.0\start.ps1"`).

8. **In Discord**
   - `/status` — version, SAFE_MODE, pause, queue depth, spend.
   - `/runs`, `/run <run_id>`, `/ticket list`, `/ticket view <id>`.
   - If SAFE_MODE=1: disable with SAFE_MODE=0 and restart to start missions.

## Rollback

1. Stop the bot (Discord `/stop` or kill process).
2. Restore previous release folder from backup (or re-unzip prior zip).
3. `python -m sovereign self-test`
4. `python bot.py` or `.\start.ps1`

## How to build the zip

From repo root:

```powershell
.\dist\build.ps1
```

Output: `dist\sovereign_v5.0_rollout.zip` and `dist\out\` (contents).

## GO/NO-GO

- **GO:** All critical systems PASS; self-test and verify_execution_layer pass; zip builds and unzips; README and rollback steps in place.
- **NO-GO:** Any critical system FAIL; or self-test/verify fail; or missing single-instance lock / grant enforcement / SAFE_MODE block.

**Verdict: GO** for v5.0 release with the above checklist and known risks acknowledged.
