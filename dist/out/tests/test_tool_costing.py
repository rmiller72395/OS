# tests/test_tool_costing.py — compute_tool_cost (v5.0)

from __future__ import annotations

import pytest
from skills.tool_costing import compute_tool_cost


def test_per_call():
    assert compute_tool_cost({"usd_per_call": 0.1}, 0, 3) == pytest.approx(0.3)


def test_per_minute():
    assert compute_tool_cost({"usd_per_min": 1.0}, 60.0, 1) == pytest.approx(1.0)
    assert compute_tool_cost({"usd_per_min": 1.0}, 30.0, 1) == pytest.approx(0.5)


def test_flat():
    assert compute_tool_cost({"usd_flat": 5.0}, 0, 0) == pytest.approx(5.0)


def test_combination():
    assert compute_tool_cost(
        {"usd_per_call": 0.01, "usd_per_min": 0.1, "usd_flat": 0.5},
        60.0, 2,
    ) == pytest.approx(0.02 + 0.1 + 0.5)


def test_empty_returns_zero():
    assert compute_tool_cost({}, 10, 1) == 0.0
    assert compute_tool_cost(None, 10, 1) == 0.0
