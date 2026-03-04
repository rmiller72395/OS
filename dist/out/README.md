# RMFramework (Sovereign Orchestrator) — v5.0 Rollout

This release is **rollout-ready**: Discord as control plane, local dashboard for observability, run graph, ticketing, Tool Registry + Tool Grants, SAFE_MODE, watchdog, and owner-friendly operations.

- **Execution Run Graph:** Every run has `run_id`/`trace_id`; spans and events in `data/runs/<run_id>.jsonl`; compact summaries in `data/missions.jsonl`.
- **Internal Ticketing:** SQLite tickets; create/list/start/retry/cancel via Discord; queue runner (optional); crash-safe reconciliation.
- **Monitoring:** Failures alert to `MONITORING_CHANNEL_ID` with "What happened" / "What to do next" and dashboard deep-links.
- **Safe Mode:** `SAFE_MODE=1` disables paid calls and side-effect tools; diagnostics only.
- **Secrets:** Use `.env` (python-dotenv) or environment variables; do not store tokens in JSON config.

## Day 1 Bring-Up (exact command sequence)

1. **Unzip** the release to a folder (e.g. `C:\Sovereign\v5.0`). Set `SOVEREIGN_DATA_DIR` to a persistent path (e.g. `C:\SovereignData`) so data survives code updates.

2. **Bootstrap**
   ```powershell
   cd C:\Sovereign\v5.0
   python -m sovereign init
   ```
   Creates `data/`, `data/runs/`, `backups/`, `logs/`, config from template if missing, and initializes the tickets DB.

3. **Secrets**
   - Copy `.env.example` to `.env`.
   - Set `DISCORD_TOKEN`, `OWNER_DISCORD_IDS`, `RMFRAMEWORK_PERMIT_SECRET`, `MONITORING_CHANNEL_ID`.
   - For first-day safety set `SAFE_MODE=1`.

4. **Self-test**
   ```powershell
   python -m sovereign self-test
   ```
   Must exit 0. Then optionally: `python verify_execution_layer.py`.

5. **SAFE_MODE first run**
   - Ensure `SAFE_MODE=1` in `.env`. Start: `python bot.py` or `.\run_windows.ps1` (or Task Scheduler: `run_windows.bat`).
   - In Discord: `/status` (shows version, SAFE_MODE, pause, queue depth).
   - Try `/runs`, `/ticket list`, `/ticket create title:Test description:First ticket`.

6. **First real ticket run**
   - Set `SAFE_MODE=0` in `.env` and restart.
   - `/ticket ready TKT-000001`, then `/ticket start TKT-000001`, then start a mission with `TICKET_ID: TKT-000001` in the message.

## Quick start (Windows 10/11)

1) **Install Python 3.11+**

2) **Install dependencies**
```bash
pip install -r requirements.txt
```

3) **Environment (use `.env` or env vars)**
- **Required:** `DISCORD_TOKEN`, `OWNER_DISCORD_IDS`, `RMFRAMEWORK_PERMIT_SECRET`
- **Rollout:** `MONITORING_CHANNEL_ID` (channel for failure alerts)
- **Optional:** `SAFE_MODE=1`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPS_CHANNEL_ID`

4) **Run**
```bash
python bot.py
```
Or: `./run_windows.ps1` (loads `.env` then runs bot). For Task Scheduler, point to `run_windows.ps1` or `run_windows.bat`.

## Monitoring channel & triage

- Set **`MONITORING_CHANNEL_ID`** to the Discord channel where failure alerts are sent.
- On hard failure (crash, timeout, veto, validation failure), the bot sends:
  - **What happened** (1–2 lines)
  - **What to do next** (1–3 steps)
  - Last events excerpt and a **dashboard link** (`http://localhost:8765/runs/<run_id>`)
  - If the run is tied to a ticket: suggest `/ticket view <id>` and `/ticket retry <id>`
- Alerts are chunked to Discord’s 2000-character limit; same error signature is throttled (repeat count) within 5 minutes.

**When an alert fires:**  
1. Open the dashboard link to see the run and span/event details.  
2. Follow the "What to do next" steps.  
3. For ticket-backed runs, use `/ticket retry <id>` after fixing the issue if appropriate.

## Local dashboard

- Default: **http://localhost:8765** (bind: `SOVEREIGN_DASHBOARD_HOST=127.0.0.1`, `SOVEREIGN_DASHBOARD_PORT=8765`).
- **Endpoints:** `/health`, `/runs`, `/runs/<run_id>`, `/tickets`, `/tickets/<id>`.
- Use for observability and drill-down; **Discord remains the operational interface** (no replacement of Discord).

## Internal Ticketing

- **Create:** `/ticket create title:My task description:Details priority:1`
- **List:** `/ticket list` or `/ticket list READY`
- **View:** `/ticket view TKT-000001`
- **Lifecycle:** NEW → `/ticket ready` → READY; then queue or `/ticket start` → RUNNING; on completion → DONE/FAILED; `/ticket block`, `/ticket done`, `/ticket cancel`, `/ticket retry` as needed.
- Only **READY** tickets can be auto-started by the queue runner (when enabled). **RUNNING** tickets are tied to a `run_id`; on run completion they move to DONE (success) or FAILED (hard fail) or BLOCKED (governance).
- Tickets are stored in SQLite (`data/tickets.db`). Dashboard shows tickets and links to runs.

## Owner commands (Discord)

Core:
- `/help` — command list
- `/dashboard` — system status
- `/settings` — current config
- `/setup_check` — env/key check (no secrets)
- `/status` — run, queue, budget, ticket summary (v4.10)
- `/runs` — last 5 runs; `/run <run_id>` — run summary + dashboard link
- `/pause` — stop starting new work; `/resume` — allow new work; `/stop` — graceful shutdown

Budget:
- `/set_limit 100.00`
- `/set_austerity 45.00`

Workers / Managers:
- `/workers`
- `/work_queue`
- `/tools` — list execution-layer tools (v5.0)
- `/set_threshold 200` — owner approval threshold for cash spend
- `/set_workers_auto_run on|off`
- `/set_workers_max_auto 2`
- `/set_managers on|off`
- `/set_manager_fanout 2`

Permits:
- `/approve <permit_id>`
- `/deny <permit_id>`

Templates:
- `/template` — funding header template

## Mission funding headers (optional, recommended)
Paste above your task message:

```
PROJECT: ExampleProject
CASH_BUDGET_USD: 5000
OWNER_APPROVAL_THRESHOLD_USD: 200
ALLOWED_TOOLS: http_request, run_script
TICKET_ID: TKT-000001
TASK: Analyze vendor options and produce a recommendation
```

Notes:
- **ALLOWED_TOOLS** (optional): Comma-separated tools this mission may run. The Director outputs TOOLS_REQUESTED_JSON; you decide what to grant per job.
- If `CASH_BUDGET_USD` is missing/0, any worker order with `estimated_cash_usd > 0` will be **held**.
- Permit approvals are enforced against the mission cash envelope.

## WorkOrder safety & validation (v4.10)

Any proposed work order is **validated before dispatch**:
- Worker must be **known & enabled** (builtin worker enabled OR custom worker enabled in registry)
- Objective must be non-empty
- Enums are normalized (unknown risk/side_effects → safe defaults)

Invalid work orders are written to the DB as `HOLD_*` items and the channel receives a short summary.

## Custom workers

You can register custom workers (and the framework will now execute them):

```
/worker_register {"name":"LEGAL","description":"Contract review","sop":"You are the LEGAL worker...","enabled":true}
```

Enable/disable:
- `/worker_enable LEGAL`
- `/worker_disable LEGAL`

## Permit reminders & auto-expire (v4.10)

- Pending permits receive a reminder **~3 minutes before expiry**.
- Expired pending permits are **auto-cancelled** by a background sweep.

## Execution layer (v5.0) — from planning to action

The framework can **run tools** (e.g. HTTP requests, scripts) when a work order has `side_effects: EXECUTE` and the owner approves the permit. Workers (e.g. RUNNER) output `ACTION_JSON` with a list of tool calls; each run is logged in AuditDB (`action_log`) and is fail-closed (unknown tool or missing permit = no execution).

- **Built-in tools:** `http_request` (GET/POST with timeout), `run_script` (allowlisted dir; set `RMFRAMEWORK_SCRIPT_ALLOWLIST`).
- **Global allowlist:** `/set_global_tools http_request, run_script` — tools on this list never need per-mission approval. **Everything not on the global list** requires you to set `ALLOWED_TOOLS` in the mission header (approval per job).
- **Add your own:** Register tools in `execution.py` or extend the registry for APIs (travel, broker, ERP). See `EXECUTION_LAYER_SPEC.md` and `TECHNICAL_MANIFESTO.md`.

## Governance & vision

- **GOVERNANCE_AND_VISION.md** — Company purpose, self-serving / best-results mandate, and owner–CEO partnership. The CEO soul is written to operate in this relationship; the owner provides whatever the team needs.

## Budgets & escrow (high-level)

- Every LLM call reserves escrow before running; actual cost is applied after. Unknown cost triggers a lock until the operator runs `/reset_cost_lock`.
- Mission-level `CASH_BUDGET_USD` and `OWNER_APPROVAL_THRESHOLD_USD` control permits and worker spend. Permits expire; reminders at T–3 minutes.

## Common failure signatures & fixes

| Signature | Likely cause | Fix |
|-----------|--------------|-----|
| WORK_ORDER_VALIDATION_FAILED | Unknown/disabled worker or empty objective | Enable worker with `/worker_enable`, fix CEO/Director output |
| TIMEOUT | Mission exceeded time limit | Simplify task or increase timeout; check dashboard for where it stuck |
| CRASH | Uncaught exception | Check logs and run JSONL; fix code/config and restart |
| VETO | CISO/CFO blocked | Address veto reason in mission or headers |
| cost_unknown | LLM returned no cost | Investigate ledger; `/reset_cost_lock` when satisfied |

## Rollback

If you need to roll back after deploying the zip:

1. Stop the bot (Discord `/stop` or kill the process).
2. Restore the previous release folder from backup (or re-unzip the prior zip).
3. If using `SOVEREIGN_DATA_DIR`, point it to the same path; config/DBs remain compatible within v4.x/v5.0.
4. Run `python -m sovereign self-test` from the restored folder.
5. Start with `python bot.py` or `.\start.ps1`.

## Files created

In the same directory as `bot.py` (or under `SOVEREIGN_DATA_DIR` when set):
- `sovereign_config.json`
- `sovereign_audit.db`
- `sovereign_audit.log`
- `sovereign_heartbeat.txt`
- `data/runs/<run_id>.jsonl` (per-run trace)
- `data/missions.jsonl` (compact run summaries)
- `data/tickets.db` (tickets)
- `data/sovereign_ops.db` (tool registry, grants, capability plans)

