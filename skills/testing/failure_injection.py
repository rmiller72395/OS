# skills/testing/failure_injection.py — Deterministic failure injection for tests/simulation
#
# When SIMULATION_MODE=1 or in preflight, call inject_failure(tool_name, "TimeoutError")
# so that the next execution of that tool raises. Enables testing retries/escalations without real network.

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Type

_injections: Dict[str, Type[Exception]] = {}
_pattern_by_step: Dict[str, Type[Exception]] = {}  # step_id -> exception type


def _resolve_exception(name: str) -> Type[Exception]:
    if name == "TimeoutError":
        return TimeoutError
    if name == "ConnectionError":
        return ConnectionError
    if name == "ValueError":
        return ValueError
    return ValueError  # default


def inject_failure(pattern: str, exception: Optional[str] = None) -> None:
    """
    Inject a failure for a given pattern (tool_name or step_id).
    exception: "TimeoutError", "ConnectionError", "ValueError". Default "ValueError".
    Next call to check_inject_failure(tool_name) or check_inject_failure(step_id=...) will raise.
    """
    exc = _resolve_exception((exception or "ValueError").strip())
    key = (pattern or "").strip().upper()
    if key:
        _injections[key] = exc


def inject_failure_for_step(step_id: str, exception: Optional[str] = None) -> None:
    """Inject failure for a step_id (e.g. work_item_id or run_id:work_item_id:idx)."""
    exc = _resolve_exception((exception or "ValueError").strip())
    key = (step_id or "").strip()
    if key:
        _pattern_by_step[key] = exc


def check_inject_failure(tool_name: str, step_id: Optional[str] = None) -> None:
    """
    If a failure was injected for this tool_name or step_id, raise the configured exception.
    Call at start of tool execution (e.g. in ExecutionManager._run_single_action).
    """
    if not (os.getenv("SIMULATION_MODE", "").strip() == "1" or os.getenv("SOVEREIGN_PREFLIGHT", "").strip() == "1"):
        return
    key = (tool_name or "").strip().upper()
    if key and key in _injections:
        raise _injections[key]("inject_failure")
    if step_id and (step_id in _pattern_by_step):
        raise _pattern_by_step[step_id]("inject_failure")


def clear_inject_failure(tool_name: Optional[str] = None) -> None:
    """Clear injected failure for tool_name, or all if tool_name is None."""
    if tool_name:
        key = (tool_name or "").strip().upper()
        _injections.pop(key, None)
        _pattern_by_step.pop(tool_name.strip(), None)
    else:
        _injections.clear()
        _pattern_by_step.clear()
