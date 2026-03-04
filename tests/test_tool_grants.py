# tests/test_tool_grants.py — Tool registry, grants, authorize_tool_call, usage (v5.0)

from __future__ import annotations

import os
import tempfile
import pytest
from datetime import datetime, timezone

# Use temp DBs to avoid polluting project data
@pytest.fixture
def ops_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except Exception:
        pass


@pytest.fixture
def registry(ops_db_path):
    from skills.tool_registry import ToolRegistry, ToolDef
    reg = ToolRegistry(ops_db_path)
    reg.ensure_schema()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    reg.upsert_tool(ToolDef(
        tool_name="test_tool",
        description="Test",
        input_schema_json={},
        output_schema_json=None,
        scopes=["read:*", "write:file"],
        side_effect=True,
        idempotency_required=True,
        cost_model_json={"usd_per_call": 0.05},
        default_timeout_s=60,
        max_timeout_s=120,
        rate_limit_json={},
        allowlist_json={"domains": ["example.com"]},
        enabled=True,
        created_at=now,
        updated_at=now,
    ))
    return reg


@pytest.fixture
def grants_store(ops_db_path):
    from skills.tool_grants import ToolGrantStore, ToolGrant
    store = ToolGrantStore(ops_db_path)
    store.ensure_schema()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store.create_grant(ToolGrant(
        grant_id="g1",
        ticket_id="T1",
        run_id=None,
        allowed_tools=["test_tool"],
        allowed_scopes=["read:*", "write:file"],
        constraints_json={},
        max_tool_spend_usd=2.0,
        max_calls=5,
        expires_at=None,
        issued_by="test",
        reason="test",
        created_at=now,
    ))
    return store


def test_authorize_denies_unknown_tool(registry, grants_store):
    from skills.tool_grants import authorize_tool_call
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = authorize_tool_call(registry, grants_store, "unknown_tool", [], "T1", None, {}, 0, now)
    assert r.allowed is False
    assert "not in registry" in r.reason or "tool not in registry" in r.reason


def test_authorize_denies_disabled_tool(registry, grants_store, ops_db_path):
    from skills.tool_registry import ToolDef
    from skills.tool_grants import authorize_tool_call
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    reg = registry
    disabled = reg.get_tool("test_tool")
    disabled = ToolDef(
        tool_name=disabled.tool_name,
        description=disabled.description,
        input_schema_json=disabled.input_schema_json,
        output_schema_json=disabled.output_schema_json,
        scopes=disabled.scopes,
        side_effect=disabled.side_effect,
        idempotency_required=disabled.idempotency_required,
        cost_model_json=disabled.cost_model_json,
        default_timeout_s=disabled.default_timeout_s,
        max_timeout_s=disabled.max_timeout_s,
        rate_limit_json=disabled.rate_limit_json,
        allowlist_json=disabled.allowlist_json,
        enabled=False,
        created_at=now,
        updated_at=now,
    )
    reg.upsert_tool(disabled)
    r = authorize_tool_call(reg, grants_store, "test_tool", ["read:*"], "T1", None, {}, 0.05, now)
    assert r.allowed is False
    assert "disabled" in r.reason.lower()


def test_authorize_denies_missing_grant(registry, grants_store):
    from skills.tool_grants import authorize_tool_call
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = authorize_tool_call(registry, grants_store, "test_tool", ["read:*"], "OTHER_TICKET", None, {}, 0.05, now)
    assert r.allowed is False
    assert "no active grant" in r.reason or "grant" in r.reason.lower()


def test_authorize_denies_scope_escalation(registry, grants_store):
    from skills.tool_grants import authorize_tool_call
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = authorize_tool_call(registry, grants_store, "test_tool", ["read:*", "admin:*"], "T1", None, {}, 0.05, now)
    assert r.allowed is False
    assert "scope" in r.reason.lower() or (r.constraint_violations and any("scope" in str(v).lower() for v in r.constraint_violations))


def test_authorize_allows_valid_call(registry, grants_store):
    from skills.tool_grants import authorize_tool_call
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = authorize_tool_call(registry, grants_store, "test_tool", ["read:*"], "T1", None, {}, 0.05, now)
    assert r.allowed is True
    assert r.matched_grant_id == "g1"


def test_authorize_denies_expired_grant(registry, grants_store, ops_db_path):
    import sqlite3
    from skills.tool_grants import authorize_tool_call
    past = "2020-01-01T00:00:00Z"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(ops_db_path)
    conn.execute(
        "INSERT INTO tool_grants (grant_id, ticket_id, run_id, allowed_tools_json, allowed_scopes_json, constraints_json, max_tool_spend_usd, max_calls, expires_at, issued_by, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("g_expired", "T2", None, '["test_tool"]', '["read:*"]', '{}', 10, 10, past, "test", "test", now),
    )
    conn.execute("INSERT INTO tool_grant_usage (grant_id, calls_used, spend_used_usd, updated_at) VALUES (?,0,0,?)", ("g_expired", now))
    conn.commit()
    conn.close()
    r = authorize_tool_call(registry, grants_store, "test_tool", ["read:*"], "T2", None, {}, 0.05, now)
    assert r.allowed is False  # expired grant not returned by get_active_grant


def test_authorize_enforces_max_calls(registry, grants_store):
    from skills.tool_grants import authorize_tool_call
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for _ in range(5):
        grants_store.record_usage("g1", 1, 0.01)
    r = authorize_tool_call(registry, grants_store, "test_tool", ["read:*"], "T1", None, {}, 0.05, now)
    assert r.allowed is False
    assert "max_calls" in r.reason.lower() or "calls" in r.reason.lower()


def test_tool_spend_updates_usage_counters(grants_store):
    grants_store.record_usage("g1", 2, 0.10)
    u = grants_store.get_usage("g1")
    assert u is not None
    assert u[0] >= 2
    assert u[1] >= 0.10


def test_fail_closed_on_db_error():
    from skills.tool_grants import authorize_tool_call, ToolGrantStore
    from skills.tool_registry import ToolRegistry
    reg = ToolRegistry("/nonexistent/path/sovereign_ops.db")
    store = ToolGrantStore("/nonexistent/path/sovereign_ops.db")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # get_tool will fail or return None; get_active_grant may fail. authorize_tool_call should deny on error.
    r = authorize_tool_call(reg, store, "any_tool", [], "T1", None, {}, 0, now)
    assert r.allowed is False
