# skills/ops_db.py — Shared path for sovereign_ops DB (tool registry, grants, capability plans)
# Single DB file; WAL. Used by tool_registry, tool_grants, capability_plan.

from __future__ import annotations

import os
from pathlib import Path


def get_ops_db_path() -> str:
    """Default path for sovereign_ops.db (tool_registry, tool_grants, capability_plans)."""
    path = os.getenv("TOOL_REGISTRY_DB_PATH") or os.getenv("TOOL_GRANTS_DB_PATH") or os.getenv("CAPABILITY_PLAN_DB_PATH")
    if path:
        return str(path)
    base = os.getenv("SOVEREIGN_DATA_DIR", os.getcwd())
    return str(Path(base) / "data" / "sovereign_ops.db")
