# RMFramework (Sovereign Orchestrator) — v5.0 Rollout

This release is **rollout-ready**: Discord as control plane, local dashboard for observability, run graph, ticketing, Tool Registry + Tool Grants, SAFE_MODE, watchdog, and owner-friendly operations.

- **Execution Run Graph:** Every run has `run_id`/`trace_id`; spans and events in `data/runs/<run_id>.jsonl`; compact summaries in `data/missions.jsonl`.
- **Internal Ticketing:** SQLite tickets; create/list/start/retry/cancel via Discord; queue runner (optional); crash-safe reconciliation.
- **Monitoring:** Failures alert to `MONITORING_CHANNEL_ID` with "What happened" / "What to do next" and dashboard deep-links.
- **Safe Mode:** `SAFE_MODE=1` disables paid calls and side-effect tools; diagnostics only.
- **Secrets:** Use `.env` (python-dotenv) or environment variables; do not store tokens in JSON config.

## Day 1: Safe Launch

- **Start in SAFE_MODE:** Set `SAFE_MODE=1` in `.env` for the first run. Only diagnostics and read-only tools are available; no paid model calls, no side-effect tools.
- **Use only read-only tools at first:** The starter toolset includes `time_now`, `uuid_new`, `json_validate`, `http_get_json_readonly`, `public_api_catalog_search`. Auto run-scoped grants allow only these and only explicit scopes (`read:time`, `read:uuid`, `read:json`, `read:http`, `read:catalog`). No wildcards.
- **Add APIs incrementally:** See "How to add a new API safely" below.

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
- **Model routing:** `MODEL_ROUTING_PATH` — if set, load model routing from this path; otherwise `./model_routing.json` is used.

4) **Run**
```bash
python bot.py
```
Or: `./run_windows.ps1` (loads `.env` then runs bot). For Task Scheduler, point to `run_windows.ps1` or `run_windows.bat`.

## Model routing (v5.0)

Model selection is centralized in **`model_routing.json`** (repo root). Each layer (CEO, CFO, CISO, DIRECTOR, MANAGER, WORKER_EXECUTION) has a primary `provider`/`model` and optional `fallback_models`. The runtime uses `resolve_model(layer, attempt_index)` so the first attempt uses the primary model and retries use fallbacks.

- **Edit:** Change `model_routing.json` (or your custom file) and restart the bot. No code change required.
- **Override path:** Set env `MODEL_ROUTING_PATH` to the full path to your JSON (or YAML) file. Validated on startup and in `python -m sovereign self-test`.
- **CFO-gated worker paid fallback:** Workers default to local models (e.g. `kimi-k-local`). If the config includes a paid fallback (e.g. `openai/gpt-5-mini`) for `WORKER_EXECUTION`, that fallback is **CFO-gated**: the mission must have explicit approval or the system restricts workers to local-only. Set `CFO_APPROVED_WORKER_PAID=1` in the mission funding header to allow worker paid fallback for that run.
- **SAFE_MODE:** When `SAFE_MODE=1`, paid model calls (anthropic, openai, google) are blocked for all layers; workers use local-only when configured.

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

The framework can **run tools** when a work order has `side_effects: EXECUTE` and the owner approves the permit. Every tool execution goes through the Tool Registry + Tool Grants (default-deny). Workers output `ACTION_JSON`; each run is logged in AuditDB (`action_log`).

### Starter toolset (read-only, safe)

After `python -m sovereign init` or first bot startup, the **Tool Registry** is bootstrapped with:

| Tool | Purpose | Scopes | Enabled by default |
|------|---------|--------|--------------------|
| `time_now` | UTC + local time (America/New_York) | read:time | yes |
| `uuid_new` | Generate UUID | read:uuid | yes |
| `json_validate` | Validate JSON string (optional schema) | read:json | yes |
| `http_get_json_readonly` | HTTP GET JSON from **allowlisted domains only** | read:http | yes |
| `public_api_catalog_search` | Search local API catalog (no network) | read:catalog | yes |
| `http_request` / `run_script` | General HTTP, script execution | write | **no** (owner must enable) |

Auto run-scoped grants allow **only** the starter read-only tools and **only** explicit scopes (`read:time`, `read:uuid`, `read:json`, `read:http`, `read:catalog`). No wildcards. Tickets that need any other tool require an explicit Tool Grant from the owner.

### Catalog vs Allowlist

- **Catalog** (`data/catalog/public_api_catalog.json`): For **discovery** only. CEO/Manager can use `public_api_catalog_search` to suggest APIs. The catalog is not used for execution. Update it optionally with `python scripts/update_public_api_catalog.py` (pulls from public-api-lists on GitHub). You must still **allowlist** domains and **grant** tools for execution.
- **Allowlist** (`public_api_allowlist_domains` in config or env `PUBLIC_API_ALLOWLIST_DOMAINS`): **Execution** allowlist for `http_get_json_readonly`. Only domains in this list can be fetched. Start small; add domains incrementally.

### How to add a new API safely

1. **Add domain to allowlist:** In `sovereign_config.json` set `public_api_allowlist_domains` to include the API host (e.g. `api.example.com`), or set env `PUBLIC_API_ALLOWLIST_DOMAINS=api.example.com,api.open-meteo.com`.
2. **Keep tool read-only:** Use `http_get_json_readonly` (GET, JSON only). Do not enable `http_request` or other write tools unless you need them and have approved the risk.
3. **Create explicit Tool Grant:** For ticket-backed runs, use `/tools approve_tool` for the ticket with the tool name and scopes (e.g. `read:http`). For ad-hoc runs, the auto run-scoped grant already allows `http_get_json_readonly` if the domain is in the allowlist.
4. **Run a test ticket:** Create a ticket, approve the tool, start the mission with `TICKET_ID: TKT-xxx` and a task that uses the tool. Check `/run <run_id>` and dashboard.

### Legacy / advanced tools

- **Global allowlist:** `/set_global_tools` — tools on this list never need per-mission approval. Prefer explicit grants for safety.
- **Built-in:** `http_request` (general GET/POST), `run_script` (allowlisted dir; `RMFRAMEWORK_SCRIPT_ALLOWLIST`). These are **disabled** by default in the registry; owner must enable and grant explicitly.

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

