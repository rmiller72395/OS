# tests/test_capability_plan.py — Capability plan validation and plan_hash (v5.0)

from __future__ import annotations

import tempfile
import os
import pytest
from skills.capability_plan import validate_capability_plan, plan_hash, CapabilityPlanStore


def test_validate_capability_plan_valid():
    plan = {
        "ticket_id": "T1",
        "objective": "Do something",
        "success_criteria": ["done"],
        "steps": [{"step_id": "1", "description": "Step 1", "tool_needed": "tool_a", "verification": "ok"}],
        "required_tools": [
            {"tool_name": "tool_a", "reason": "need it", "scopes_needed": ["read:*"], "side_effect": False, "idempotency_plan": "yes", "allowlist_constraints": {}, "estimated_usage": {}, "estimated_cost_usd": 0.1},
        ],
        "budget": {"expected_total_usd": 1},
        "risks": [],
    }
    ok, errs = validate_capability_plan(plan)
    assert ok, errs
    assert len(errs) == 0


def test_validate_capability_plan_missing_fields():
    ok, errs = validate_capability_plan({"ticket_id": "T1"})
    assert not ok
    assert any("objective" in e for e in errs) or any("success_criteria" in e for e in errs)


def test_plan_hash_deterministic():
    plan = {"ticket_id": "T1", "objective": "X", "success_criteria": [], "steps": [], "required_tools": [], "budget": {}, "risks": []}
    h1 = plan_hash(plan)
    h2 = plan_hash(plan)
    assert h1 == h2
    assert len(h1) == 64
    plan2 = {**plan, "objective": "Y"}
    assert plan_hash(plan2) != h1


def test_capability_plan_store_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        store = CapabilityPlanStore(path)
        store.ensure_schema()
        plan = {"ticket_id": "T1", "objective": "O", "success_criteria": [], "steps": [], "required_tools": [], "budget": {}, "risks": []}
        h = plan_hash(plan)
        store.upsert_plan("T1", plan, h, "test")
        assert store.get_plan("T1") == plan
        assert store.get_plan_hash("T1") == h
        assert store.get_plan("T2") is None
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass
