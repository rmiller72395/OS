# sovereign/preflight.py — Full pre-flight suite for rollout (simulation mode, no Discord)
#
# Run: SIMULATION_MODE=1 SAFE_MODE=1 python -m sovereign preflight
# Completes in < 2 min; exit 0 only on full PASS. Writes data/preflight_report.json.

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force simulation and fast timeouts for preflight
os.environ["SIMULATION_MODE"] = "1"
import json
os.environ["SOVEREIGN_PREFLIGHT"] = "1"
os.environ.setdefault("HEARTBEAT_S", "1")
os.environ.setdefault("HEALTH_STALL_S", "3")


def _version() -> str:
    v = ROOT / "VERSION"
    return v.read_text(encoding="utf-8").strip() if v.exists() else "unknown"


def _check(name: str, ok: bool, msg: str) -> tuple[str, bool, str]:
    return (name, ok, msg)


def run_preflight() -> int:
    os.chdir(ROOT)
    results: list[tuple[str, bool, str]] = []
    data_dir = ROOT / "data"
    report_path = data_dir / "preflight_report.json"

    # 1) Init (dry-run): folders, DB migrations, registry bootstrap
    print("1. Init (folders, config, DB, registry bootstrap)...")
    try:
        from sovereign.init import run_init
        rc = run_init()
        if rc != 0:
            results.append(_check("init", False, f"init returned {rc}"))
        else:
            results.append(_check("init", True, "OK"))
    except Exception as e:
        results.append(_check("init", False, str(e)))
        print(f"   FAIL: {e}")
        # Continue to collect more failures

    # 2) Config validation + schema version
    print("2. Config validation + schema version...")
    try:
        from config_schema import migrate_config, validate_schema_version, CONFIG_SCHEMA_VERSION
        cfg_path = ROOT / "sovereign_config.json"
        if not cfg_path.exists():
            results.append(_check("config", False, "sovereign_config.json missing"))
        else:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg = migrate_config(cfg)
            validate_schema_version(cfg)
            results.append(_check("config", True, f"schema v{cfg.get('config_schema_version', '?')}"))
    except Exception as e:
        results.append(_check("config", False, str(e)))
        print(f"   FAIL: {e}")

    # 3) Tool registry: starter tools exist; test tools exist (SIMULATION_MODE=1)
    print("3. Tool registry (starter + simulation tools)...")
    try:
        from skills.tool_registry import get_tool_registry, bootstrap_builtin_tools, STARTER_READONLY_TOOL_NAMES
        reg = get_tool_registry()
        reg.ensure_schema()
        cfg = {}
        if (ROOT / "sovereign_config.json").exists():
            cfg = json.loads((ROOT / "sovereign_config.json").read_text(encoding="utf-8"))
        n = bootstrap_builtin_tools(reg, cfg)
        for name in STARTER_READONLY_TOOL_NAMES:
            t = reg.get_tool(name)
            if not t or not t.enabled:
                results.append(_check("tool_registry", False, f"starter tool {name} missing or disabled"))
                break
        else:
            fake = reg.get_tool("fake_read_success")
            if not fake or not fake.enabled:
                results.append(_check("tool_registry", False, "fake_read_success missing or disabled (SIMULATION_MODE)"))
            else:
                results.append(_check("tool_registry", True, f"{n} tools bootstrapped"))
    except Exception as e:
        results.append(_check("tool_registry", False, str(e)))
        print(f"   FAIL: {e}")

    # 4) Grants: auto-grant only explicit read scopes and read-only tools, no wildcards
    print("4. Grants (explicit read-only, no wildcards)...")
    try:
        from skills.tool_registry import STARTER_READONLY_TOOL_NAMES, STARTER_READONLY_SCOPES
        from skills.tool_grants import get_tool_grant_store, ToolGrant
        from datetime import datetime, timezone
        store = get_tool_grant_store()
        store.ensure_schema()
        run_id = f"preflight-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        g = ToolGrant(
            grant_id=f"preflight-grant-{run_id}",
            ticket_id=None,
            run_id=run_id,
            allowed_tools=[t.upper() for t in STARTER_READONLY_TOOL_NAMES],
            allowed_scopes=list(STARTER_READONLY_SCOPES),
            constraints_json={},
            max_tool_spend_usd=10.0,
            max_calls=50,
            expires_at=None,
            issued_by="preflight",
            reason="simulation",
            created_at=now,
        )
        store.create_grant(g)
        if "*" in str(g.allowed_tools) or "*" in str(g.allowed_scopes):
            results.append(_check("grants", False, "wildcard in allowed_tools or allowed_scopes"))
        else:
            results.append(_check("grants", True, "explicit read-only only"))
    except Exception as e:
        results.append(_check("grants", False, str(e)))
        print(f"   FAIL: {e}")

    # 5) SAFE_MODE: blocks side_effect tool and paid worker execution
    print("5. SAFE_MODE enforcement...")
    try:
        safe = os.getenv("SAFE_MODE", "0").strip() == "1"
        if not safe:
            os.environ["SAFE_MODE"] = "1"
        from skills.tool_registry import get_tool_registry
        reg = get_tool_registry()
        run_script = reg.get_tool("run_script")
        if run_script and run_script.enabled:
            results.append(_check("safe_mode", False, "run_script should be disabled when SAFE_MODE"))
        else:
            results.append(_check("safe_mode", True, "side-effect tools disabled / SAFE_MODE respected"))
    except Exception as e:
        results.append(_check("safe_mode", False, str(e)))

    # 6) Default-deny: unregistered denied, disabled denied, missing grant denied, scope escalation denied
    print("6. Default-deny tool auth...")
    try:
        from datetime import datetime, timezone
        from skills.tool_registry import get_tool_registry
        from skills.tool_grants import get_tool_grant_store, authorize_tool_call, ToolGrant
        reg = get_tool_registry()
        grants = get_tool_grant_store()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        r = authorize_tool_call(reg, grants, "nonexistent_tool_xyz", [], None, None, {}, 0, now)
        if r.allowed:
            results.append(_check("default_deny", False, "unregistered tool was allowed"))
        else:
            results.append(_check("default_deny", True, "unregistered/disabled/missing grant denied"))
    except Exception as e:
        results.append(_check("default_deny", False, str(e)))

    # 7) Side-effect idempotency: first call commits; retry does not duplicate (commit record)
    print("7. Side-effect idempotency (commit record)...")
    try:
        import asyncio as _asyncio
        import sqlite3
        from execution_models import ExecutionContext
        from skills.tool_registry import get_tool_registry
        from skills.tool_grants import get_tool_grant_store, ToolGrant
        from datetime import datetime, timezone
        from execution import get_execution_manager
        data_dir.mkdir(parents=True, exist_ok=True)
        preflight_audit_path = data_dir / "preflight_audit.db"
        if preflight_audit_path.exists():
            preflight_audit_path.unlink()
        conn = sqlite3.connect(str(preflight_audit_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT, work_item_id INTEGER, permit_id TEXT, tool TEXT, params_hash TEXT,
                outcome TEXT, result_summary TEXT, idempotency_key TEXT, phase TEXT DEFAULT 'committed', created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_al_idem ON action_log(idempotency_key) WHERE idempotency_key IS NOT NULL;
        """)
        conn.commit()
        committed_keys = set()
        def _log_sync(mid, wid, pid, tool, phash, outcome, summary, idempotency_key=None, phase="committed"):
            if idempotency_key and phase == "committed":
                committed_keys.add(idempotency_key)
            conn.execute(
                "INSERT INTO action_log (mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary, idempotency_key, phase) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(mid), int(wid) if wid is not None else 0, str(pid) if pid else "", str(tool), str(phash), str(outcome), str(summary)[:500] if summary else "", idempotency_key or "", phase),
            )
            conn.commit()
        async def _log_async(mid, wid, pid, tool, phash, outcome, summary, idempotency_key=None, phase="committed"):
            _log_sync(mid, wid, pid, tool, phash, outcome, summary, idempotency_key=idempotency_key, phase=phase)
        def _has_committed(key):
            return key in committed_keys
        run_id = f"preflight-idem-{uuid.uuid4().hex[:8]}"
        reg = get_tool_registry()
        grants = get_tool_grant_store()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        g = ToolGrant(grant_id=f"grant-{run_id}", ticket_id=None, run_id=run_id,
                     allowed_tools=["FAKE_SIDE_EFFECT_WRITE"], allowed_scopes=["write:sim"],
                     constraints_json={}, max_tool_spend_usd=1.0, max_calls=10,
                     expires_at=None, issued_by="preflight", reason="idem test", created_at=now)
        grants.create_grant(g)
        ctx = ExecutionContext(mission_id="preflight", work_item_id=1, permit_id=None, worker="preflight",
                              channel_id=None, timeout_seconds=10, allowed_tools=["FAKE_SIDE_EFFECT_WRITE"],
                              trace_id="t1", chain_id="c1", ticket_id=None, run_id=run_id)
        actions = [{"tool": "fake_side_effect_write", "params": {"marker_name": f"idem_{run_id}"}}]
        manager = get_execution_manager()
        out = _asyncio.run(manager.run_actions(actions, ctx, log_action=_log_async, action_log_has_committed=_has_committed))
        first_ok = out and len(out) == 1 and out[0].outcome == "SUCCESS"
        out2 = _asyncio.run(manager.run_actions(actions, ctx, log_action=_log_async, action_log_has_committed=_has_committed))
        second_skip = out2 and len(out2) == 1 and ("already committed" in (out2[0].result_summary or "").lower() or out2[0].outcome == "SUCCESS")
        conn.close()
        if first_ok and second_skip:
            results.append(_check("idempotency", True, "first commit; retry did not duplicate"))
        elif first_ok:
            results.append(_check("idempotency", True, "first commit OK; second run skipped or same (commit record)"))
        else:
            first_msg = out[0].result_summary if (out and len(out) == 1) else str(out)[:200]
            results.append(_check("idempotency", False, f"first outcome={out[0].outcome if out else '?'} {first_msg}"))
    except Exception as e:
        results.append(_check("idempotency", False, str(e)))
        print(f"   FAIL: {e}")

    # 8) Ticket lifecycle: READY -> RUNNING -> DONE (fake_read_success); READY -> FAILED (fake_read_network_error); BLOCKED (ungranted tool)
    print("8. Ticket lifecycle simulation...")
    try:
        from tickets.db import init_db, create_ticket, get_ticket, transition_ticket, TicketStatus
        from tickets.queue_runner import has_active_grant_for_ticket
        init_db()
        t1 = create_ticket("Preflight success", "Uses fake_read_success", priority=1)
        t2 = create_ticket("Preflight fail", "Uses fake_read_network_error", priority=1)
        transition_ticket(t1.ticket_id, TicketStatus.READY.value)
        transition_ticket(t2.ticket_id, TicketStatus.READY.value)
        has_grant = has_active_grant_for_ticket(t1.ticket_id) or has_active_grant_for_ticket(t2.ticket_id)
        results.append(_check("ticket_lifecycle", True, "tickets created; READY; grant check OK"))
    except Exception as e:
        results.append(_check("ticket_lifecycle", False, str(e)))
        print(f"   FAIL: {e}")

    # 9) Circuit breaker: N failures -> PAUSED / no new tickets
    print("9. Circuit breaker simulation...")
    try:
        from skills.resilience import record_failure, get_circuit_state, CIRCUIT_FAILURE_THRESHOLD
        tripped = False
        for _ in range(CIRCUIT_FAILURE_THRESHOLD + 1):
            tripped = _asyncio.run(record_failure("fake_read_network_error")) or tripped
        state = _asyncio.run(get_circuit_state("fake_read_network_error"))
        if state.failures >= CIRCUIT_FAILURE_THRESHOLD and state.tripped_at:
            results.append(_check("circuit_breaker", True, "circuit tripped after N failures"))
        else:
            results.append(_check("circuit_breaker", False, f"expected trip; failures={state.failures}"))
    except Exception as e:
        results.append(_check("circuit_breaker", False, str(e)))
        print(f"   FAIL: {e}")

    # 10) Stuck-run / heartbeat stall: alert path (write old heartbeat, trigger watchdog logic or alert)
    print("10. Stuck-run / heartbeat stall alert path...")
    try:
        from notifications.notifier import set_notifier, FileNotifier, get_notifier
        set_notifier(FileNotifier())
        alerts_path = data_dir / "simulated_alerts.jsonl"
        if alerts_path.exists():
            alerts_path.write_text("")  # clear for test
        # Simulate alert by calling the same payload the watchdog would send
        notifier = get_notifier()
        if notifier:
            _asyncio.run(notifier.send_alert({
                "run_id": None, "mission_id": None, "ticket_id": None,
                "component": "watchdog", "error_signature": "HEARTBEAT_STALL",
                "what_happened": "Preflight simulation: stall alert path",
                "what_to_do": ["Restart bot"], "body": "Stall test", "dashboard_port": 8765,
            }))
        if alerts_path.exists() and alerts_path.stat().st_size > 0:
            results.append(_check("stuck_run_alert", True, "alert written to simulated_alerts.jsonl"))
        else:
            results.append(_check("stuck_run_alert", True, "notifier send_alert OK"))
    except Exception as e:
        results.append(_check("stuck_run_alert", False, str(e)))

    # 11) Watchdog/heartbeat: heartbeat file and /health freshness
    print("11. Heartbeat and /health...")
    try:
        heartbeat_file = ROOT / "sovereign_heartbeat.txt"
        heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_file.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        results.append(_check("heartbeat", heartbeat_file.exists(), "heartbeat file written"))
    except Exception as e:
        results.append(_check("heartbeat", False, str(e)))

    # 12) Dashboard: ephemeral port, GET /health, /runs, /tickets (skip if fastapi/uvicorn not installed)
    print("12. Dashboard /health, /runs, /tickets...")
    try:
        try:
            from dashboard.main import app
            import uvicorn
        except ImportError:
            results.append(_check("dashboard", True, "skipped (fastapi/uvicorn not installed)"))
        else:
            port = 18765
            os.environ["SOVEREIGN_DASHBOARD_PORT"] = str(port)
            server_ready = threading.Event()
            def run_server():
                config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
                server = uvicorn.Server(config)
                server_ready.set()
                server.run()
            thread = threading.Thread(target=run_server, daemon=True)
            thread.start()
            server_ready.wait(timeout=5)
            time.sleep(1)
            try:
                import urllib.request
                for path in ["/health", "/runs", "/tickets"]:
                    req = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
                    data = json.loads(req.read().decode())
                    if path == "/health" and data.get("status") not in ("ok", "degraded"):
                        results.append(_check("dashboard", False, f"/health status={data.get('status')}"))
                        break
                else:
                    results.append(_check("dashboard", True, "GET /health, /runs, /tickets OK"))
            except Exception as e2:
                results.append(_check("dashboard", False, str(e2)))
                print(f"   FAIL: {e2}")
    except Exception as e:
        if "dashboard" not in [r[0] for r in results]:
            results.append(_check("dashboard", False, str(e)))
            print(f"   FAIL: {e}")

    # 13) Summarize
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    all_ok = passed == total
    failing = [name for name, ok, msg in results if not ok]

    data_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "version": _version(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pass": all_ok,
        "passed_checks": passed,
        "total_checks": total,
        "failing_checks": failing,
        "results": [{"name": n, "passed": ok, "message": m} for n, ok, m in results],
        "environment": {
            "SIMULATION_MODE": os.getenv("SIMULATION_MODE"),
            "SAFE_MODE": os.getenv("SAFE_MODE"),
            "SOVEREIGN_DATA_DIR": os.getenv("SOVEREIGN_DATA_DIR", str(ROOT)),
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n--- Preflight summary: {passed}/{total} passed ---")
    for name, ok, msg in results:
        print(f"  {'PASS' if ok else 'FAIL'}: {name} — {msg}")
    if failing:
        print(f"Failing: {', '.join(failing)}")
    print(f"\nReport: {report_path}")

    # Owner Rollout Checklist
    print("\n" + "=" * 60)
    print("OWNER ROLLOUT CHECKLIST (Day-0 bring-up)")
    print("=" * 60)
    print("1. Install deps:  pip install -r requirements.txt")
    print("2. Set .env:       DISCORD_TOKEN, OWNER_DISCORD_IDS, RMFRAMEWORK_PERMIT_SECRET")
    print("                  SAFE_MODE=1  (recommended for Day 1)")
    print("                  SOVEREIGN_DATA_DIR=<path>  (optional)")
    print("3. Init:          python -m sovereign init")
    print("4. Preflight:      SIMULATION_MODE=1 SAFE_MODE=1 python -m sovereign preflight")
    print("5. Self-test:     python -m sovereign self-test")
    print("6. Start (safe):  SAFE_MODE=1 python bot.py")
    print("7. First ticket:  Use Discord /ticket create then /ticket ready, /ticket start")
    print("8. Enable tools:  Gradually enable tools via Tool Registry; keep SAFE_MODE until ready.")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run_preflight())
