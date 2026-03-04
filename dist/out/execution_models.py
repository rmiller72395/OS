# execution_models.py — Shared dataclasses for Execution Layer (v5.0)
#
# ExecutionContext and ActionResult live here to avoid circular imports
# between execution.py and the skills package. Fail-closed; Windows-native.
# See EXECUTION_LAYER_SPEC.md and EXECUTION_LAYER_REFACTOR_PLAN.md.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionContext:
    mission_id: str
    work_item_id: int
    permit_id: Optional[str]
    worker: str
    channel_id: Optional[int] = None
    timeout_seconds: float = 60.0
    allowed_tools: Optional[List[str]] = None  # None = all registered allowed
    trace_id: Optional[str] = None  # Per-skill execution trace for observability
    chain_id: Optional[str] = None  # UUID for entire execution chain (all sub-skills in one run)
    ticket_id: Optional[str] = None  # For tool grants and capability plan
    run_id: Optional[str] = None  # For tool grants


@dataclass
class ActionResult:
    outcome: str  # SUCCESS | FAIL | TIMEOUT | SKIP_NO_PERMIT | SKIP_UNKNOWN_TOOL
    result_summary: str = ""
    details: Optional[Dict[str, Any]] = None
