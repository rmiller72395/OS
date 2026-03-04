---
name: rmframework-action-json
description: Defines the ACTION_JSON format for RMFramework execution layer (v5.0). Use when editing RUNNER prompts, worker output format, or docs that describe how workers request tool execution. Ensures valid schema and fail-closed behavior.
---

# RMFramework ACTION_JSON

Workers with `side_effects == EXECUTE` request execution by emitting a single **ACTION_JSON** block. The runtime parses it after the work item is DONE and permit is APPROVED; invalid or missing block → no execution (fail-closed).

## Format

1. **Marker:** Literal line or phrase `ACTION_JSON`.
2. **Single JSON object** immediately after: first `{` after the marker to matching `}` (brace-balanced). Only the first such object is used.
3. **Schema:** Root must be `{"actions": [ ... ]}` where each element is `{"tool": "<name>", "params": { ... }}`.

**Valid example:**

```
ACTION_JSON
{"actions": [{"tool": "http_request", "params": {"method": "GET", "url": "https://api.example.com/status"}}]}
```

**Invalid (ignored / no execution):** missing marker, invalid JSON, root not an object, no `actions` key, `actions` not a list, or action missing `tool` or `params`.

## Tool contracts (built-in)

- **http_request:** `method` (default GET), `url` (required), optional `headers` (dict), optional `body`. Timeout 30s.
- **run_script:** `script_path` (required; relative to allowlist), optional `args` (list), optional `timeout_seconds`. Requires env `RMFRAMEWORK_SCRIPT_ALLOWLIST` (comma-separated dirs). Script must be under one of those dirs.

## Fail-closed behavior

- Execution runs **only** when: work has `side_effects == EXECUTE`, permit status is APPROVED, and parsed ACTION_JSON is valid with registered tools.
- Unknown tool, invalid params, missing permit, or timeout → skip that action (or stop chain), log outcome, **do not execute**.
- Workers must treat execution as a **request**; never assume the action ran. No guarantees if parsing fails or permit is not APPROVED.

## No secrets in params

`action_log` stores `params_hash`, not full params. Do **not** put API keys, passwords, or tokens in `params`; use env, server-side config, or references so the agent output stays safe to log.

## Checklist for prompts/docs

- [ ] Emit exactly one block: marker `ACTION_JSON` then one `{"actions": [...]}` object.
- [ ] Each action has `tool` (string) and `params` (object).
- [ ] Tool names match registered tools (e.g. `http_request`, `run_script`).
- [ ] No secrets in `params`; document that execution is permit-gated and fail-closed.
