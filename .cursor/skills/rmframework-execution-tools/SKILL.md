---
name: rmframework-execution-tools
description: How to add or change tools in the RMFramework execution layer (v5.0). Use when editing execution.py, registering new tools, or documenting tool handlers. Covers ToolDef, ExecutionContext, ActionResult, Windows-native timeout, and audit logging.
---

# RMFramework Execution Tools

Execution runs in `execution.py`: tool registry, `run_actions()`, timeout, and audit callback. All behavior is fail-closed and Windows-native.

## Registering a tool

- **Type:** `ToolDef(name, description, handler, requires_permit=True)`.
- **Handler signature:** `async def handler(params: dict, context: ExecutionContext) -> ActionResult`.
- **Registration:** `register_tool(ToolDef(...))` (e.g. at module load in `execution.py`).

## ExecutionContext

Passed to every handler:

- `mission_id`, `work_item_id`, `permit_id`, `worker`, `channel_id` (optional)
- `timeout_seconds` (default 60.0) — each tool run is wrapped in `asyncio.wait_for(..., timeout=context.timeout_seconds)`
- `allowed_tools` (optional) — if set, only tools in this list may run; otherwise all registered tools allowed

## ActionResult

Return exactly one of:

- `ActionResult("SUCCESS", result_summary, details=None)`
- `ActionResult("FAIL", message, details=None)`
- `ActionResult("TIMEOUT", message, details=None)`

Runner also produces `SKIP_NO_PERMIT` and `SKIP_UNKNOWN_TOOL` when the action is not executed; handlers only return SUCCESS / FAIL / TIMEOUT.

## Windows-native and timeout

- Do **not** use `fcntl`, `signal.SIGALRM`, or fork. Use `asyncio.wait_for`, `asyncio.create_subprocess_exec` with `communicate(timeout=...)`, or threading/ctypes for process-based tools.
- Keep handler async and bounded; long work should be offloaded or use `context.timeout_seconds` so the runner can enforce timeout.

## Audit

Handlers do **not** write to `action_log`. The runner calls `log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary)` after each run. Ensure handler return value is a proper `ActionResult` and that `result_summary` does not contain secrets (params are hashed for audit; avoid logging full payloads in summary).

## Built-in reference

- **http_request:** `_tool_http_request` — params `method`, `url`, `headers?`, `body?`; uses aiohttp; timeout min(30, context.timeout_seconds).
- **run_script:** `_tool_run_script` — params `script_path`, `args?`, `timeout_seconds?`; path must be under `RMFRAMEWORK_SCRIPT_ALLOWLIST`; runs Python script via subprocess with timeout.

## Adding a new tool

1. Implement `async def _tool_my_tool(params, context: ExecutionContext) -> ActionResult`.
2. Validate `params`; return `ActionResult("FAIL", "message")` on invalid input.
3. Use `asyncio.wait_for` or stay within `context.timeout_seconds` so the runner’s timeout applies.
4. Return `ActionResult("SUCCESS", summary, details)` or `ActionResult("FAIL"|"TIMEOUT", message, None)`.
5. `register_tool(ToolDef("my_tool", "Description.", _tool_my_tool, requires_permit=True))`.

Optional: expose tool name and params in RUNNER prompts or in the rmframework-action-json skill so workers can emit valid ACTION_JSON.
