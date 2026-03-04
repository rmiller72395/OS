# RMFramework Technical Manifesto

**Authority:** This document is the ground truth for system state transitions, lifecycle, and execution behavior. Governing laws (`.cursor/rules`) reference it.

---

## 1. Mission lifecycle

```
[User message] → STARTED → Director → CISO/CFO gates → CEO → dispatch
       ↓
   Outcome: SUCCESS | VETO | WORKERS_QUEUED | WORKERS_PENDING | WORKERS_HELD | WORKERS_DONE | SATURATED | ...
       ↓
   phase: STARTED → COMPLETED (with optional checkpoints)
```

- **STARTED:** Mission row created; trace initialized; Director invoked.
- **Checkpoints:** Outcome may be set to intermediate values (e.g. `WORKERS_QUEUED`) before phase becomes COMPLETED.
- **COMPLETED:** Mission is finished; `ts_end` set; no further state changes for that mission_id.

---

## 2. Work item (worker task) lifecycle

```
[WorkOrder from Director/CEO] → validated
       ↓
   status: QUEUED | APPROVAL_PENDING | HOLD_* | RUNNING | DONE
```

- **QUEUED:** Enqueued for worker loop; permit approved (or not required).
- **APPROVAL_PENDING:** Awaiting owner `/approve` or `/deny`; permit created.
- **HOLD_***:** Held (e.g. HOLD_WORKER, HOLD_BUDGET, HOLD_VALIDATION); not processed until remediated or mission closed.
- **RUNNING:** Worker loop has claimed the item; LLM (and optionally execution) in progress.
- **DONE:** Result written; optional execution step completed; mission report may be synthesized.

**Execution (v5+):** When `side_effects == EXECUTE` and work item reaches DONE, an optional **action step** may run: worker output is parsed for `ACTION_JSON`; if present and permit is APPROVED, tools run (see Execution Layer); results recorded in `action_log` and permit marked USED.

---

## 3. Permit lifecycle

```
create_permit() → status: PENDING
       ↓
   Owner: /approve → APPROVED  |  /deny → DENIED  |  expiry → EXPIRED
       ↓
   When action executed (if side_effects=EXECUTE): APPROVED → USED
```

- **PENDING:** Created; owner may approve or deny; reminder at T-3min; auto-expire sweep sets EXPIRED.
- **APPROVED:** Owner approved; work item may be QUEUED and run; for EXECUTE work, permit is consumed to USED when actions run.
- **DENIED:** Owner denied; work item remains APPROVAL_PENDING or is updated to HOLD.
- **EXPIRED:** Not approved in time; work item not queued (or held).
- **USED:** Permit was consumed for an execution step (audit trail).

---

## 4. Execution layer (v5+)

- **Scope:** Actions (tool invocations) run only when:
  1. Work order has `side_effects == EXECUTE`, and
  2. Work item has an APPROVED permit (or policy allows auto-run for that action type), and
  3. Tool is registered and allowed for the mission/worker.
- **Fail-closed:** On any ambiguity (unknown tool, missing permit, timeout, error), do **not** execute; record HOLD or failure in AuditDB.
- **Audit:** Every invocation is recorded in `action_log` (mission_id, work_item_id, tool, params_hash, outcome, ts).
- **Windows-native:** No Unix-only calls; use subprocess with timeout (e.g. `threading.Timer` or ctypes) for process-based tools.

---

## 5. Financial and audit consistency

- All spend and permit cash flow through AuditDB in WAL mode; single-writer via dedicated executor.
- No financial state change without an atomic transaction in AuditDB.
- Worker LLM costs are recorded in `worker_llm_costs` and attributed to mission cash envelope.

---

## 6. Version and references

- **RMFramework:** v4.11 Obsidian (bot.py); execution layer spec: `EXECUTION_LAYER_SPEC.md`.
- **Governing laws:** `.cursor/rules/rmframework-governing-laws.mdc`.
