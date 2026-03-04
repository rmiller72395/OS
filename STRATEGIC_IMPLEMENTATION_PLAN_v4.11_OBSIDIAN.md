# RMFramework v4.11 "Obsidian Edition" — Strategic Implementation Plan

**Lead Systems Architect — Critical v4.10 Engineering Audit**  
**Target node:** i9-14900HX / RTX 5070 | **Objective:** Harden Profit Tier logic and Windows robustness for high-velocity deployment.

**Note:** `TECHNICAL_MANIFESTO.md` was referenced but is not present in the repo. This audit is based on `bot.py` and the RMFramework governing laws. Creating that manifest is recommended for v4.11.

---

## 1. Architectural Review

### 1.1 Model Tiering — Does it prevent high-risk tasks from Tier 3 (Commodity)?

**Finding: Logic is correct; one storage bug can undermine it.**

- **Dispatch path (`_dispatch_work_orders`):**
  - `_force_tier1_for_order(o)` is called and correctly forces Tier 1 when `risk_class != "NONE"` or `side_effects == "EXECUTE"` (lines 2806–2809).
  - Forced Tier 1 model is written into `model_hint` and passed to `enqueue_work` (including for HOLD paths).
- **Execution path (`_worker_loop`):**
  - Worker uses `item.get("model_hint")` then `_fail_up_from_model(model_hint)` (2522–2523).
  - It does **not** re-evaluate `risk_class` at execution time; it trusts the stored `model_hint`.

**Critical bug:** In `_enqueue_work_sync` (1031–1056), the `INSERT` lists **15 columns** but the tuple supplies **16 values** (including `work.get("model_hint")`). The column `model_hint` is **not** in the INSERT list. This causes a **parameter count mismatch** (15 placeholders vs 16 values). Either:
- The process would crash on first `enqueue_work`, or
- In some environments the 16th value is ignored and `model_hint` is never persisted, so workers fall back to `MODEL_DIRECTOR` (Tier 2), **violating the rule that risk_class != NONE must use Tier 1.**

**Recommendation:**
- Fix `_enqueue_work_sync`: add `model_hint` to the INSERT column list and the 16th `?` placeholder so the Tier chosen at dispatch is persisted and used at execution.
- **Defense in depth:** In `_worker_loop`, when loading a work item, re-apply Tier 1 forcing using `risk_class` from the item (from DB). If the stored `model_hint` is Tier 2/3 but `risk_class != "NONE"`, override to Tier 1. This guarantees correct routing even if any other code path ever wrote a wrong hint.

---

### 1.2 `_dispatch_work_orders` and AuditDB — Race conditions under high concurrency (10+ missions)?

**Finding: No race conditions in AuditDB writes; design is sound.**

- **Serialization:** All AuditDB access goes through `_run(fn, *args)`, which holds `async with self._lock` and runs `fn` in a **single-thread** executor (max_workers=1). So all `_*_sync` methods run sequentially on one thread; no concurrent SQLite writes from this process.
- **WAL mode:** SQLite is in WAL mode; single writer from the dedicated thread is safe. Multiple missions can call `create_permit`, `enqueue_work`, etc. concurrently from the event loop, but they are serialized at the AuditDB layer.
- **Dispatch loop:** `_dispatch_work_orders` does many `await _audit_db.*` calls in sequence (enqueue_work for invalid, HOLD_BUDGET, then create_permit + enqueue_work for valid). No shared in-memory state between missions that could race; each mission gets its own permits and work rows. No race conditions identified in the dispatch loop itself.

**Recommendation:** No change required for race conditions. Optional: add a brief comment above `_run` stating that the executor + lock guarantees single-threaded AuditDB writes for WAL consistency.

---

## 2. Performance & Windows Robustness

### 2.1 msvcrt.locking and ConfigCache — Lock contention and “In-Memory-First, Lazy-Flush”

**Findings:**

- **Lock scope:** Config is read/written under `sovereign_config.json.lock` with `msvcrt.locking` on the **first byte** of the lock file. Lock is held for the duration of read (in `_read_config_from_disk`) or for the full write + replace (in `_write_config_to_disk`). So contention is per config operation, not per key.
- **ConfigCache:** Already “in-memory first”: all reads/writes go to `_data`; `flush_lazy()` and `flush_durable()` write to disk. So in-memory-first is already there.
- **Lazy vs durable:** `_config_sync_loop` already does lazy flush every 5s and durable every ~60s. So the “lazy-flush” behavior is in place; the main cost is that every 5s, if dirty, we take the config lock and call `_write_config_to_disk(..., durable=False)`, which still does `json.dump` + `tf.flush()` (no `fsync`). So disk I/O is already minimized for the 5s interval; the 60s durable flush does `os.fsync`.

**Recommendations:**

- **Minimize lock hold time:** In `_write_config_to_disk`, build the JSON and write to the temp file **outside** the lock, then under the lock only: `_lock_file` → replace temp to CONFIG_FILE → optional fsync → `_unlock_file`. Today the whole write (including json.dump and replace) is under the lock. Moving “prepare content + write to temp file” outside the lock reduces contention time.
- **ConfigCache:** Add an explicit “in-memory-first, lazy-flush” comment in the class docstring and ensure all config reads in hot paths use `_cfg.get()` / `_cfg.snapshot()` without forcing a flush. No structural change needed beyond the lock-scope reduction above.

---

### 2.2 SetConsoleCtrlHandler and 30-second mission drain — Thread safety and saturation hang

**Findings:**

- **Handler:** The CTRL handler runs in a Windows callback thread. It uses `loop.call_soon_threadsafe(_schedule)` to schedule `bot._shutdown(reason, hard=...)` on the event loop. So shutdown runs on the main asyncio thread; that’s correct.
- **Drain loop:** `_shutdown` sets `_draining = True`, then:
  ```python
  while _inflight_missions and time.monotonic() < deadline:
      await asyncio.sleep(0.5)
  ```
  So the event loop is still running and can process I/O and task completion. In-flight missions are awaited via `_run_mission`; when they finish they release the semaphore and pop from `_inflight_missions`. So as long as the event loop is servicing those tasks, they can complete and the drain can exit.
- **Saturation risk:** If the **worker pool** is saturated (e.g. many QUEUED work items and a single `_worker_loop` that processes one at a time), missions that are “in flight” but waiting for workers might not complete within the 30s drain. The drain only waits for `_inflight_missions` to empty; it does not wait for the work queue. So:
  - If “mission” means “user message that started a run”: when `_run_mission` returns (Director/CEO/workers scheduled), the mission is still in `_inflight_missions` until the `finally` that pops it. So we do wait for the full mission (including dispatch) to complete.
  - But the **worker loop** keeps running until we cancel it (`self._worker_loop_task.cancel()`). So we don’t wait for queued workers to finish; we wait only for the “mission” (the Discord message handler) to finish. So if the mission has already dispatched 10 work orders and the handler returns, that mission is popped from `_inflight_missions`; the 30s drain might then exit while the worker loop is still processing. That’s by design (we don’t want to wait indefinitely for workers). The only “hang” risk would be if the event loop were blocked so that neither missions nor the drain loop could advance.
- **Actual hang risk:** The drain loop does `await asyncio.sleep(0.5)` in a tight loop. If the **main thread** is blocked (e.g. a synchronous call that holds a lock or does heavy CPU), the drain could appear to hang. There’s no explicit “drain timeout” that forces exit after 30s regardless; we exit when either `_inflight_missions` is empty or the deadline is passed. So we don’t deadlock on the drain logic itself, but we could hit the Windows ~5s kill on CTRL_CLOSE if we’re blocked elsewhere.

**Recommendations:**

- **Make drain timeout strict:** After the while loop, if `time.monotonic() >= deadline` and `_inflight_missions` is still non-empty, log a clear warning and proceed to flush/cancel/close. Already the code proceeds to cancel tasks and close; just make the “we timed out” explicit in logs.
- **Avoid blocking the event loop:** Ensure no synchronous call in the mission or worker path holds the asyncio thread for a long time. The AuditDB already offloads to a thread; config flush uses `asyncio.to_thread`. So no change required except to document that the drain assumes the event loop remains responsive.
- **Worker loop and drain:** Optionally, when `_draining` is True, have `_worker_loop` stop claiming new work (e.g. skip `_fetch_next_queued_work` or exit the loop quickly). Today the worker loop checks `_draining` in its sleep loop (`while not _draining`), but it might be in the middle of `call_agent` when drain starts. Cancelling `_worker_loop_task` will raise CancelledError in that call_agent and exit the loop; that’s already the case. So no code change strictly required; only the explicit “drain timed out” log.

---

## 3. Security Hardening

### 3.1 HMAC permit and permit replay (recycled mission_id)

**Findings:**

- **Signing string:** `_permit_signing_string` includes: `permit_id`, `mission_id`, `work_id`, `worker`, `max_cash_usd`, `risk_class`, `expires_at`. So same mission_id with different permit_id yields a different signature. So **reusing mission_id alone** doesn’t replay a permit; each permit has a unique `permit_id` (uuid.uuid4().hex[:10]).
- **Replay of the same permit:** If an attacker captured a valid (permit_id, mission_id, work_id, …) and the HMAC, they could in theory try to “re-approve” the same permit. On approval, the code updates permit status to APPROVED and work_items to QUEUED. If the permit was already APPROVED and already executed, re-sending `/approve` would just set status again (idempotent). The real risk is using the same signed permit token to approve a *different* context. The signing string binds permit_id, mission_id, work_id, worker, etc., so the token is not reusable for another permit or another work_id. So **replay of one permit** doesn’t grant a different work item.
- **Recycled mission_id:** If mission_id is reused (e.g. same ID for a new mission), the new mission would get new permit_ids, so new signing strings. Old permits would have expired (expires_at) and would fail expiry check on approval. So no obvious replay from recycling mission_id.
- **Gap:** The signing string does **not** include a nonce or issuance timestamp. So theoretically, if the same (permit_id, mission_id, work_id, worker, max_cash_usd, risk_class, expires_at) were ever recreated (e.g. bug or deliberate reuse of permit_id), the same HMAC would be valid. permit_id is 10 hex chars (40 bits), so collision is unlikely but not cryptographically bound to “once ever.”

**Recommendations:**

- **Add issuance timestamp to signing string (and DB):** Add an `issued_at` UTC timestamp when creating the permit. Include `issued_at` in `_permit_signing_string` and store it in the `permits` table. On verify, require `issued_at` to be present and (optionally) reject if too old (e.g. > 24h). This binds the permit to a single issuance time and reduces any replay window.
- **Optional nonce:** Alternatively or in addition, add a random nonce (e.g. 16 bytes hex) to the permit and to the signing string so that even identical logical content produces a different signature. Prefer timestamp for auditability and nonce for extra uniqueness.

---

### 3.2 require_env() and RMFRAMEWORK_PERMIT_SECRET — Instant terminate and no leak in logs

**Findings:**

- **Startup:** `require_env()` is called at module load: `OWNER_IDS, ALLOWED_CHANNEL_IDS_SET = require_env()`. If `RMFRAMEWORK_PERMIT_SECRET` is missing, we raise `RuntimeError("Missing env: ...")` and the process exits. So we do terminate if the secret is missing.
- **Logs:** Grep shows the secret is never logged as a value; only `bool(RMFRAMEWORK_PERMIT_SECRET)` is used in `/setup_check`. So no leak in current code.
- **Runtime clearing:** If something cleared the env or the global after startup (e.g. for testing), `_hmac_permit` would return `""` and `_verify_permit_hmac` would return False (no sig or no secret). So we fail closed. But we don’t **terminate** the process if the secret is later found empty at runtime (e.g. permit approval path). Failing verification is correct; instant terminate is a policy choice.

**Recommendations:**

- **Explicit “no log” rule:** Add a one-line comment above `RMFRAMEWORK_PERMIT_SECRET = os.getenv(...)`: “Never log or echo; fail closed if missing.”
- **Runtime check (optional):** In `/approve`, before verifying HMAC, if `not RMFRAMEWORK_PERMIT_SECRET`, respond with “Permit system unavailable (configuration error).” and optionally log “CRITICAL: RMFRAMEWORK_PERMIT_SECRET empty at approval time.” Do not terminate the process from the approval handler to avoid DoS from a single request; failing the approval is sufficient. If you want “instant terminate” when secret is detected empty at runtime, do it in a single dedicated safety check (e.g. a periodic task or one-time check after startup) and document it.

---

## 4. Implementation Plan (Summary)

| # | Area | Action |
|---|------|--------|
| 1 | **Model tiering** | Fix `_enqueue_work_sync`: add `model_hint` to INSERT columns and 16th `?`. Add defense-in-depth in `_worker_loop`: if item has risk_class != NONE and current model_hint is not Tier 1, override to Tier 1. |
| 2 | **AuditDB** | No change for races. Optional: add a one-line comment on `_run` that single-thread executor + lock ensures serialized WAL writes. |
| 3 | **ConfigCache / disk I/O** | In `_write_config_to_disk`, build JSON and write to temp file outside the lock; hold lock only for rename + optional fsync. Add short “in-memory-first, lazy-flush” docstring to ConfigCache. |
| 4 | **Shutdown drain** | After the drain while-loop, if deadline exceeded and `_inflight_missions` non-empty, log “Drain timeout; proceeding with shutdown.” Proceed unchanged to flush/cancel/close. |
| 5 | **HMAC permit** | Add `issued_at` to permit creation (UTC), add column to `permits` if missing, include `issued_at` in `_permit_signing_string` and in HMAC verification. Optionally reject permits with issued_at older than e.g. 24h. |
| 6 | **require_env / secret** | Add comment above `RMFRAMEWORK_PERMIT_SECRET`: never log or echo; fail closed if missing. In `/approve`, if secret is empty at runtime, respond “Permit system unavailable” and do not approve. |

---

## 5. Version and Changelog

- **Version:** Bump to **v4.11.0 "Obsidian Edition"** in `bot.py` header and any version string.
- **CHANGELOG:** Add a v4.11 section:
  - **Profit Tier:** Fix model_hint persistence in work_items; defense-in-depth Tier 1 enforcement in worker loop.
  - **Config:** Reduce config lock hold time (write to temp outside lock); document in-memory-first lazy-flush.
  - **Shutdown:** Explicit drain timeout log when deadline exceeded.
  - **Security:** Permit issuance timestamp in HMAC and DB; no secret in logs; approval path checks secret at runtime.

---

**Awaiting [APPROVE] to proceed with code refactor to v4.11 Obsidian Edition.**
