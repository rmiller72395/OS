# Execution Layer Refactor — Architecture Plan

**Goal:** Zero-failure execution engine with strict Skill interface, two-tier access (Global vs Restricted), Gatekeeper for human-in-the-loop approval, and observable, idempotent behavior.

---

## 1. Current Coupling & Pain Points

| Area | Current State | Issue |
|------|----------------|--------|
| **Tool contract** | `ToolDef(name, description, handler, requires_permit)` | No schema validation, no access tier, no telemetry contract. |
| **Execution path** | Single `run_actions()` in `execution.py` | Permit check and tool dispatch are coupled; no separation for “safe” vs “sensitive” tools. |
| **Error handling** | One broad `except Exception` in `run_actions` | No Retry vs Alert strategy; all failures treated alike. |
| **Observability** | `log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, summary)` | Sufficient for audit but no execution trace ID or timing in one place. |
| **Approval** | Permit (APPROVED) checked in bot before calling `run_actions` | No per-action Gatekeeper; no serialized intent or approver identity for sensitive ops. |

**Files to change:**

- **execution.py** — Keep as main entry; delegate to skills + gatekeeper; replace generic catch with severity-based handling.
- **bot.py** — Uses `run_actions`, `ExecutionContext`, `parse_action_json`. After refactor: same public API; optional approval provider for RESTRICTED skills.

---

## 2. Proposed File Structure

```
sovereign_v4_10_rollout_ready/
├── execution_models.py             # ExecutionContext, ActionResult (shared; avoids circular imports)
├── execution.py                    # Public API: run_actions, parse_action_json, ExecutionContext,
│                                    # ActionResult, list_tools, register_tool; delegates to skills + gatekeeper
├── skills/
│   ├── __init__.py                 # Re-export BaseSkill, AccessLevel, register_skill, get_skill, gatekeeper
│   ├── base.py                     # BaseSkill ABC, AccessLevel enum, execute() with telemetry
│   ├── exceptions.py               # ExecutionError, RetryableError, AlertableError (severity-based)
│   ├── registry.py                 # Two-tier registry; register_skill, get_skill, list_skills; ToolDef adapter in execution.py
│   └── gatekeeper.py               # Route by access_level; GLOBAL→execute; RESTRICTED→approval then execute
├── bot.py                          # Unchanged imports; optional approval_provider, log_approval for RESTRICTED
└── EXECUTION_LAYER_SPEC.md
```

Built-in tools (`http_request`, `run_script`) remain implemented inside `execution.py` as `BaseSkill` subclasses and are registered there to avoid circular imports and keep a single place for hardening constants.

---

## 3. Skill Architecture (Strict Interface)

### 3.1 BaseSkill (skills/base.py)

- **Metadata:** `name`, `description`, `version`, `access_level: AccessLevel` (GLOBAL | RESTRICTED).
- **Validation:** `validate(self, params: dict) -> None` — raises `ValidationError` if params invalid; called before execute.
- **Execution:** `async execute(self, params: dict, context: ExecutionContext) -> ActionResult` — implemented by subclasses; base wrapper adds telemetry (log start/end, duration, trace_id).
- **Idempotency:** Documented contract; skills that support it should declare it; runner can skip duplicate (mission_id, work_item_id) per existing spec.

### 3.2 AccessLevel (skills/base.py)

- **GLOBAL:** Foundational utility; no external validation; execute immediately; optimized for speed.
- **RESTRICTED:** State-changing or high-risk; MUST go through Gatekeeper: pause, serialize intent, wait for Approval, log approver, then execute.

---

## 4. Two-Tier Registry (skills/registry.py)

- Registry stores skills by name (case-insensitive).
- **register_skill(skill: BaseSkill)** — register a skill; name must be unique.
- **get_skill(name: str) -> Optional[BaseSkill]** — lookup by name.
- **list_skills()** — return list of skill metadata (name, description, version, access_level).
- **Backward compatibility:** `register_tool(ToolDef(...))` creates an adapter (thin BaseSkill) with `access_level=GLOBAL` and `execute=handler`, and registers it so existing callers need not change.

---

## 5. Gatekeeper Logic (skills/gatekeeper.py)

- **Inputs:** skill (BaseSkill), params, ExecutionContext, ApprovalProvider (protocol), log_action.
- **Behavior:**
  - If `skill.access_level == GLOBAL`: validate → execute → log (no approval).
  - If `skill.access_level == RESTRICTED`: validate → build ApprovalRequest (intent: tool name, params_hash, mission_id, work_item_id, side_effects description) → call `await approval_provider.request_approval(request)` → if approved, execute and call `log_approval(..., approved_by, approved_at)` then normal log_action; if denied, return ActionResult("SKIP_NO_PERMIT", "approval denied", ...).
- **ApprovalProvider protocol:** `async def request_approval(request: ApprovalRequest) -> ApprovalResult` where ApprovalResult has `approved: bool`, `approved_by: str`, `approved_at: str` (or None if denied).

---

## 6. Quality & Performance Standards

- **Error handling:** Replace generic `except Exception` with catches for `RetryableError` (e.g. transient network) → retry up to N times; `AlertableError` (e.g. config/schema) → log alert, return FAIL; other → log and FAIL. Severity drives Retry vs Alert.
- **Observability:** Every execution: trace_id (or execution_id) in context; log_action receives it; timing recorded in skill base wrapper.
- **Idempotency:** Existing spec: bot can skip execution if action_log already has (mission_id, work_item_id). Skills that are idempotent document it; runner does not double-run when already logged.

---

## 7. Integration with bot.py

- **Minimal change:** `run_actions(actions, context, log_action=..., approval_provider=...)`. If `approval_provider` is None and a RESTRICTED skill is invoked, behavior: either fail-closed (SKIP_NO_PERMIT, “approval required”) or use a default no-op provider that denies. Bot can pass an approval provider that resolves approvals via Discord (e.g. pending queue + `/approve_execution <id>`).
- **Permit:** Existing permit (APPROVED → USED) remains; Gatekeeper adds a second layer for RESTRICTED (explicit approval request + approver identity).

---

## 8. Implementation Order

1. **skills/exceptions.py** — ExecutionError, RetryableError, AlertableError.
2. **skills/base.py** — AccessLevel, BaseSkill with validate/execute and telemetry wrapper.
3. **skills/registry.py** — Registry, register_skill, get_skill, list_skills, register_tool adapter.
4. **skills/gatekeeper.py** — ApprovalRequest, ApprovalResult, ApprovalProvider, run_via_gatekeeper.
5. **execution.py** — Refactor run_actions to use registry + gatekeeper; add HttpRequestSkill, RunScriptSkill; specific exception handling (Retry/Alert); preserve parse_action_json, ExecutionContext, ActionResult, list_tools, register_tool.
6. **bot.py** — Add optional approval_provider to run_actions call; or leave None for now (RESTRICTED skills then require approval_provider or fail-closed).
