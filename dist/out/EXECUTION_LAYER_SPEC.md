# RMFramework Execution Layer — Specification

**Goal:** Make the framework capable of **action**, not just planning. Every execution path remains fail-closed, auditable, and Windows-native.

---

## 1. Design principles

| Principle | Rule |
|-----------|------|
| **Fail-closed** | No execution on error, unknown tool, missing permit, or timeout. Record and hold. |
| **AuditDB** | Every action invocation is logged (mission_id, work_item_id, tool, outcome, ts). |
| **Permit-gated** | For `side_effects == EXECUTE`, execution runs only with an APPROVED permit; after execution, permit → USED. |
| **Windows-native** | No `fcntl`, `signal.SIGALRM`, or fork. Use subprocess + timeout via threading or ctypes. |
| **Extensible** | Tools are registered by name; you can add HTTP, DB, scripts, or custom integrations. |

---

## 2. Components

### 2.1 Tool registry

- **Location:** In-memory registry (e.g. `TOOL_REGISTRY: Dict[str, ToolDef]`) + optional persistence in config/DB.
- **ToolDef:** `name`, `description`, `handler: async (params: dict, context: ExecutionContext) -> ActionResult`, `risk_class` (optional), `requires_permit` (default True for EXECUTE).
- **Built-in tools (Phase 1):**
  - `http_request`: GET/POST to a URL (params: method, url, headers, body). Timeout 30s. For calling external APIs (travel, broker, tax, ERP).
  - `run_script`: Run a single script (e.g. PowerShell or Python) from an allowlisted dir with timeout and no network unless allowed. Optional; can be disabled by config.
- **Custom tools:** Registered via `/tool_register` or config so you can add `send_email`, `create_jira_ticket`, `run_sql`, etc.

### 2.2 Action request format (from workers)

Workers that are allowed to request execution (e.g. RUNNER with `side_effects=EXECUTE`) should output a **single** JSON block after the marker:

```
ACTION_JSON
{"actions": [{"tool": "http_request", "params": {"method": "GET", "url": "https://api.example.com/..."}}, ...]}
```

- **Parsing:** Extract first `ACTION_JSON` block after the marker; validate schema (tool name registered, params object). If invalid → fail-closed, no execution.
- **Order:** Execute actions in order; if one fails, stop (or record failure and continue, per policy); all outcomes logged.

### 2.3 Execution context and allowlists

- **ExecutionContext:** `mission_id`, `work_item_id`, `permit_id`, `worker`, `channel_id`, `timeout_seconds`, `allowed_tools` (effective allowlist for this run).
- **Effective allowlist:** Union of **global allowlist** (config: `global_allowed_tools`) and **mission allowlist** (header: `ALLOWED_TOOLS`). Tools on the global list need no per-job approval; everything else requires the owner to include it in `ALLOWED_TOOLS` for that mission.
- **Permission:** Execution runs only if:
  1. Work item has `side_effects == EXECUTE` and permit status is APPROVED, and
  2. Tool is in the effective allowlist (global ∪ mission).
- After successful execution: set permit status to USED (so it cannot be reused).

### 2.4 AuditDB: action_log table

```sql
CREATE TABLE IF NOT EXISTS action_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  TEXT NOT NULL,
    work_item_id INTEGER NOT NULL,
    permit_id   TEXT,
    tool        TEXT NOT NULL,
    params_hash  TEXT,
    outcome     TEXT NOT NULL,
    result_summary TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_action_log_mission ON action_log(mission_id);
```

- **outcome:** `SUCCESS` | `FAIL` | `TIMEOUT` | `SKIP_NO_PERMIT` | `SKIP_UNKNOWN_TOOL`.
- **params_hash:** Optional hash of params for audit (do not store full secrets).

### 2.5 Runner behavior (Windows)

- **Timeout:** Every tool run has a max duration (e.g. 60s). Use `asyncio.wait_for` or a thread with a timer; on timeout, abort and record TIMEOUT.
- **Subprocess:** For `run_script`, use `subprocess.run` with `timeout=`, `cwd=` allowlisted, no shell unless explicitly allowed and sanitized.
- **Network:** `http_request` uses aiohttp/requests with timeout; no file system access unless a dedicated tool for it.

### 2.6 Hardening (fail-closed)

- **ACTION_JSON:** Max `MAX_ACTIONS_PER_RUN` (20) actions; max `MAX_ACTION_JSON_BYTES` (100KB) payload. Over limit → parse returns None, no execution.
- **http_request:** Allowed methods only (GET, POST, PUT, PATCH, DELETE, HEAD). Only `http://` and `https://`; block `file://`, localhost, 127.0.0.1, ::1, private IPs (10.x, 172.16–31.x, 192.168.x). Max URL length 2048; max body 1 MiB; max 50 headers. Response/error summaries redacted before audit.
- **run_script:** Path must match safe charset (alphanumeric, `_.-/\\`); no `..`. Resolve with `os.path.realpath` so symlinks cannot escape allowlist. Max args 32; max arg length 1024. Stdout/stderr previews redacted before audit.
- **Permit:** If tool `requires_permit` and `context.permit_id` is missing/empty → SKIP_NO_PERMIT, no execution.
- **Audit:** `result_summary` passed to `log_action` is redacted (common secret keys and values truncated/redacted).
- **Idempotency:** Bot checks `action_log` for (mission_id, work_item_id) before running; if any row exists, execution is skipped (no double-run).

---

## 3. Integration points in bot.py

1. **After worker completes (DONE):** If work item has `side_effects == EXECUTE` and permit is APPROVED:
   - Parse worker `result_text` for `ACTION_JSON`.
   - If present and valid: for each action, call tool runner with context; log to `action_log`; on first failure optionally stop and set work item result to include execution summary.
   - Mark permit USED.
2. **Startup:** Load built-in tools and any custom tools from config/DB into registry.
3. **Owner commands:** `/tools` list registered tools; `/tool_register` (or config) add custom tool (name, handler type, config).

---

## 4. What you need to provide for “do everything”

| Domain | What you add |
|--------|----------------|
| **Travel** | API keys (env); custom tool `book_flight` or use `http_request` to your travel API. |
| **Investments** | Broker API (read/write); custom tool or `http_request`; permit required for trades. |
| **Tax software** | Code generation (existing WEB worker) + `run_script` or deploy pipeline tool; data via DB/API tools. |
| **ERP SaaS** | Custom tools: `deploy`, `run_migration`, `invite_tenant`; `http_request` for REST API; DB tool for tenant DB. |

The framework provides: **governance** (permits, budgets, risk routing), **audit** (action_log, work_items, permits), and a **tool runtime** (registry, timeout, AuditDB logging). You provide: **tool implementations** and **integrations** (APIs, scripts, DBs).

---

## 5. Implementation checklist

- [ ] Add `action_log` table and migration in AuditDB.
- [ ] Implement `execution.py`: tool registry, `run_actions(actions, context)`, timeout, AuditDB write.
- [ ] Built-in tool: `http_request` (method, url, headers, body; timeout 30s).
- [ ] Optional built-in: `run_script` (allowlisted dir, timeout, optional allow_network=false).
- [ ] In worker loop (or post-worker step): when status becomes DONE and side_effects=EXECUTE and permit APPROVED, parse ACTION_JSON → run_actions → log → set permit USED.
- [ ] Owner: `/tools` list; optional `/tool_register` for simple custom tools (e.g. webhook URL).
- [ ] TECHNICAL_MANIFESTO.md updated with execution lifecycle (done).
- [ ] Tests: execution fails closed on unknown tool, missing permit, timeout.

---

## 6. Version

- **Spec version:** 1.0
- **RMFramework:** v4.11+; execution layer is additive (v5.0 execution-ready).
