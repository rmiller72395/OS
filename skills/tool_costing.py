# skills/tool_costing.py — Tool cost computation from cost_model_json (v5.0)
#
# Supports per_call, per_minute, flat. Used to record tool spend and update grant usage.
# Windows-native; no external deps.

from __future__ import annotations

from typing import Any, Dict


def compute_tool_cost(
    cost_model_json: Dict[str, Any],
    duration_s: float = 0.0,
    units: int = 1,
) -> float:
    """
    Compute cost in USD from cost model.

    Supports:
    - per_call: usd_per_call * units (units = number of calls)
    - per_minute: usd_per_min * (duration_s / 60)
    - flat: usd_flat (one-time)
    - Combination: all present keys are summed.

    Returns 0.0 if model empty or invalid (fail-closed: no charge on parse error).
    """
    if not cost_model_json or not isinstance(cost_model_json, dict):
        return 0.0
    total = 0.0
    try:
        if "usd_per_call" in cost_model_json:
            per_call = float(cost_model_json["usd_per_call"])
            total += per_call * max(0, int(units))
        if "usd_per_min" in cost_model_json and duration_s is not None:
            per_min = float(cost_model_json["usd_per_min"])
            total += per_min * max(0.0, float(duration_s)) / 60.0
        if "usd_flat" in cost_model_json:
            total += float(cost_model_json["usd_flat"])
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, total)
