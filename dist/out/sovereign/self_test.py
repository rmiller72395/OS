# sovereign/self_test.py — Config, Discord, run log, tickets, dashboard health (v4.10)

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _version_str() -> str:
    vpath = ROOT / "VERSION"
    if vpath.exists():
        return vpath.read_text(encoding="utf-8").strip()
    return "unknown"


def run_self_test() -> int:
    os.chdir(ROOT)
    errors = []

    # 1) Config schema
    print("1. Validating config schema...")
    try:
        import json
        from config_schema import migrate_config, validate_schema_version, CONFIG_SCHEMA_VERSION
        config_file = ROOT / "sovereign_config.json"
        if config_file.exists():
            cfg = json.loads(config_file.read_text(encoding="utf-8"))
            cfg = migrate_config(cfg)
            validate_schema_version(cfg)
            print(f"   Config schema version: {cfg.get('config_schema_version', 1)} (supported {CONFIG_SCHEMA_VERSION})")
        else:
            print("   No sovereign_config.json; will be created on first run.")
    except Exception as e:
        errors.append(f"Config: {e}")
        print(f"   FAIL: {e}")
    else:
        print("   OK")

    # 2) Discord connectivity (token + channel IDs)
    print("2. Checking Discord env...")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        print("   WARN: DISCORD_TOKEN not set (required for bot run; self-test continues)")
    else:
        print("   DISCORD_TOKEN set")
    owners = os.getenv("OWNER_DISCORD_IDS", "").strip()
    if not owners:
        print("   WARN: OWNER_DISCORD_IDS not set")
    else:
        print("   OWNER_DISCORD_IDS set")
    mon = os.getenv("MONITORING_CHANNEL_ID", "").strip()
    if mon:
        print("   MONITORING_CHANNEL_ID set")
    else:
        print("   WARN: MONITORING_CHANNEL_ID not set (alerts will not be sent)")

    # 3) Write test run log
    print("3. Writing test run log...")
    try:
        from observability.tracing import start_run, record_event, run_summary
        run_id, trace_id = start_run(mission_id="self-test", context={"test": True})
        asyncio.run(record_event(run_id, "event", "self-test event", "info", {}))
        summary = run_summary(run_id)
        assert summary["run_id"] == run_id
        print(f"   Run log: data/runs/{run_id}.jsonl")
    except Exception as e:
        errors.append(f"Run log: {e}")
        print(f"   FAIL: {e}")
    else:
        print("   OK")

    # 4) Ticket DB + sample ticket (dry-run)
    print("4. Tickets DB and sample ticket...")
    try:
        from tickets.db import init_db, create_ticket, get_ticket, list_tickets
        init_db()
        t = create_ticket("Self-test ticket", "Created by self-test", priority=1)
        assert get_ticket(t.ticket_id) is not None
        listed = list_tickets(limit=1)
        assert len(listed) >= 1
        print(f"   Sample ticket: {t.ticket_id}")
    except Exception as e:
        errors.append(f"Tickets: {e}")
        print(f"   FAIL: {e}")
    else:
        print("   OK")

    # 5) Tool registry, grants, capability plan, authorize, usage (v5.0)
    print("5. Tool registry + grants + capability plan...")
    try:
        from datetime import datetime, timezone
        from skills.tool_registry import get_tool_registry, ToolDef
        from skills.tool_grants import get_tool_grant_store, authorize_tool_call, ToolGrant
        from skills.capability_plan import get_capability_plan_store, validate_capability_plan, plan_hash
        from skills.tool_costing import compute_tool_cost
        reg = get_tool_registry()
        grants = get_tool_grant_store()
        plans = get_capability_plan_store()
        reg.ensure_schema()
        grants.ensure_schema()
        plans.ensure_schema()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        # Insert sample tool
        sample = ToolDef(
            tool_name="self_test_tool",
            description="For self-test",
            input_schema_json={},
            output_schema_json=None,
            scopes=["read:*"],
            side_effect=False,
            idempotency_required=True,
            cost_model_json={"usd_per_call": 0.01},
            default_timeout_s=30,
            max_timeout_s=60,
            rate_limit_json={},
            allowlist_json={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        reg.upsert_tool(sample)
        assert reg.get_tool("self_test_tool") is not None
        # Create sample grant (unique id so repeated self-test runs don't conflict)
        grant_id = f"grant-self-test-{uuid.uuid4().hex[:8]}"
        g = ToolGrant(
            grant_id=grant_id,
            ticket_id="TKT-SELFTEST",
            run_id=None,
            allowed_tools=["self_test_tool"],
            allowed_scopes=["read:*"],
            constraints_json={},
            max_tool_spend_usd=1.0,
            max_calls=10,
            expires_at=None,
            issued_by="self_test",
            reason="self-test",
            created_at=now,
        )
        grants.create_grant(g)
        # Authorize: happy path
        auth = authorize_tool_call(reg, grants, "self_test_tool", ["read:*"], "TKT-SELFTEST", None, {}, 0.01, now)
        assert auth.allowed, auth.reason
        # Deny: unknown tool
        auth_no = authorize_tool_call(reg, grants, "nonexistent_tool", [], "TKT-SELFTEST", None, {}, 0, now)
        assert not auth_no.allowed
        # Record usage and check
        grants.record_usage(grant_id, 1, 0.01)
        usage = grants.get_usage(grant_id)
        assert usage is not None and usage[0] == 1 and usage[1] == 0.01
        # Capability plan validation + hash
        plan = {"ticket_id": "T1", "objective": "Test", "success_criteria": [], "steps": [], "required_tools": [], "budget": {}, "risks": []}
        ok, errs = validate_capability_plan(plan)
        assert ok, errs
        h = plan_hash(plan)
        assert len(h) == 64
        plans.upsert_plan("TKT-SELFTEST", plan, h, "self_test")
        assert plans.get_plan("TKT-SELFTEST") is not None
        assert plans.get_plan_hash("TKT-SELFTEST") == h
        print("   Registry, grants, plan, authorize, usage OK")
    except Exception as e:
        errors.append(f"Tool registry/grants: {e}")
        print(f"   FAIL: {e}")
    else:
        print("   OK")

    # 6) Dashboard health
    print("6. Dashboard health check...")
    try:
        from dashboard.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/health")
        if r.status_code != 200:
            errors.append(f"Dashboard /health returned {r.status_code}")
            print(f"   FAIL: /health returned {r.status_code}")
        else:
            print("   GET /health OK")
    except ImportError as e:
        print(f"   SKIP: dashboard/fastapi not available: {e}")
    except Exception as e:
        errors.append(f"Dashboard: {e}")
        print(f"   FAIL: {e}")

    # 7) VERSION file
    print("7. VERSION...")
    try:
        v = _version_str()
        print(f"   {v}")
    except Exception as e:
        print(f"   WARN: {e}")

    # Versions
    print(f"\nVersion: {_version_str()}")
    try:
        import discord
        print(f"discord.py: {getattr(discord, '__version__', '?')}")
    except Exception:
        pass

    if errors:
        print(f"\nSelf-test had {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nSelf-test passed.")
    return 0
