# Intelligence Engine — Execution Layer v5.1

**Goal:** Highly autonomous, self-healing execution with contextual awareness, resilience, and full telemetry. All logic is asynchronous and non-blocking; Windows-native.

---

## 1. Module Layout

| Module | Purpose |
|--------|--------|
| **skills/preflight.py** | Context check: state validation, dependency mapping, risk scoring. Escalate to Restricted when score > threshold. |
| **skills/resilience.py** | Self-healing: exponential backoff, circuit breaker (trip after 3 failures), alternative skill routing. |
| **skills/telemetry.py** | Post-flight report storage (SQLite WAL), slow-skill flag (>2s), chain_id for full traceability. |
| **skills/execution_manager.py** | Orchestrator: preflight → **consult memory** → risk escalation → circuit check → resilience → gatekeeper → execute → post-flight → **commit to Knowledge Store**. |
| **skills/knowledge_store.py** | Global Memory: task success patterns, failure correlation, consult_memory (timeout/alternative suggestions). SQLite WAL. |
| **execution.py** | Public API: `run_actions(..., use_intelligence_engine=True)` delegates to `ExecutionManager`. |

---

## 2. Contextual Awareness & Pre-Flight

- **State validation:** Before run, engine checks skill `requirements` (e.g. `[("api_available", "https://..."), ("file_unlocked", "path")]`). Built-in: `api_available`, `file_unlocked`, `resource_available`. Register custom checks via `register_state_checker(key, checker)`.
- **Dependency mapping:** Params may include `_depends_on: [{"skill": "B", "max_age_seconds": 60}]`. Engine verifies B’s output for this mission/work_item is recorded and fresh (see `record_skill_output` / `get_dependency_freshness`).
- **Risk scoring:** Per-skill scorers (e.g. `_risk_http_request`, `_risk_run_script`) return 0–100. Register via `register_risk_scorer(skill_name, scorer)`. If score > `risk_escalation_threshold` (default 70), the run is treated as **Restricted** for that invocation (approval required even for Global skills).

---

## 3. Self-Healing Wrapper

- **Exponential backoff:** On `RetryableError`, retry up to `BACKOFF_MAX_RETRIES` (4) with delays `min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2^attempt)`.
- **Circuit breaker:** Per-skill failure count; after `CIRCUIT_FAILURE_THRESHOLD` (3) consecutive failures, circuit **trips**. Skill is disabled until `CIRCUIT_RESET_SECONDS` (60). Optional `set_circuit_alert_callback(cb)` to alert on trip.
- **Alternative routing:** If a skill fails, engine tries `alternative_skill_names` (on `BaseSkill`) or skills registered via `register_alternative(primary, alternative)`.

---

## 4. Execution Telemetry & Learning

- **Post-flight report:** Each run records `chain_id`, `trace_id`, `skill_name`, `mission_id`, `work_item_id`, `input_hash`, `output_summary`, `duration_ms`, `outcome`, `refactoring_flag`.
- **Storage:** SQLite DB (`RMFRAMEWORK_TELEMETRY_DB` or `execution_telemetry.db`), WAL mode. `TelemetryStore.record_report()`, `get_slow_skills()`, `get_chain(chain_id)`.
- **Refactoring flag:** If `duration_ms >= SLOW_SKILL_THRESHOLD_MS` (2000), report is flagged and logged as “Refactoring Needed”.
- **Chain ID:** One UUID per `run_actions` call; all skills in that run share the same `chain_id` for end-to-end tracing.

---

## 4.1. Global Memory (Knowledge Store)

- **Purpose:** One-shot learning: record what worked and what failed so the agent can adjust strategy on the next run.
- **Storage:** SQLite (`RMFRAMEWORK_KNOWLEDGE_DB` or `knowledge_store.db`), WAL mode. Tables: `task_success_patterns`, `failure_correlation`.
- **Task success patterns:** On SUCCESS, record skill name, skill chain (list of tools used), input params (redacted), chain_id, mission_id, work_item_id.
- **Failure correlation:** On non-SUCCESS, record skill name, error message, error type (timeout, boomi, network, etc.), input hash, params snapshot (redacted), env context (timeout_seconds, mission_id, work_item_id).
- **Consult Memory (before execute):** ExecutionManager calls `knowledge_store.consult_memory(skill_name, params, timeout)`. If past failures for this (skill, input_hash) exist:
  - **Timeout:** If last failure was timeout, suggest increased `timeout_seconds` (e.g. 1.5× or min 90s) and apply to context.
  - **Alternative:** If failures and the skill has a registered alternative, try the alternative skill first; on success return and record success for that alternative.
- **Post-flight commit:** After every execution (and after telemetry), commit to Knowledge Store: `record_success(...)` or `record_failure(...)`.

---

## 5. BaseSkill Extensions

- **pre_flight_check(params, context)** — Optional async. Return a result with `passed=False` to block; return `None` to rely only on global preflight.
- **post_flight_report(params, context, result, duration_ms)** — Optional async. Return a dict to merge into the stored report (e.g. custom metrics).
- **requirements** — `List[Tuple[str, Any]]` for state validation (e.g. `[("api_available", "https://api.example.com")]`).
- **alternative_skill_names** — `List[str]` of fallback skill names on failure.

---

## 6. ExecutionManager and run_actions

- **ExecutionManager(telemetry_store=None, knowledge_store=None, risk_escalation_threshold=70, circuit_alert_callback=None)**  
  Orchestrates: skill pre_flight_check → global preflight (state, dependency, risk) → **consult Knowledge Store** (adjust timeout / try alternative first) → circuit check → `run_with_resilience` (backoff, alternatives) → gatekeeper → execute → post-flight report and telemetry → **commit to Knowledge Store**.
- **run_actions(..., use_intelligence_engine=True)**  
  Default: uses `get_execution_manager().run_actions(...)`. Set `use_intelligence_engine=False` for legacy path (gatekeeper only).

---

## 7. Fail-Closed and Windows-Native

- Preflight failure → no execution; dependency missing/stale → no execution; circuit open → no execution (with optional half-open after reset).
- No Linux-only primitives; file lock check uses `msvcrt`; API check uses `urllib` in executor; telemetry uses SQLite.
