# skills/preflight.py — Contextual awareness & pre-flight analysis (Intelligence Engine)
#
# Before any skill runs: state validation, dependency mapping, risk scoring.
# If risk score exceeds threshold, escalate to Restricted (approval required).
# Windows-native; async, non-blocking.
# See EXECUTION_LAYER_REFACTOR_PLAN.md and Intelligence Engine spec.

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from execution_models import ExecutionContext

logger = logging.getLogger(__name__)

# Risk threshold above which a Global skill is escalated to Restricted for this invocation
DEFAULT_RISK_ESCALATION_THRESHOLD = 70
# Max age (seconds) for dependency payload to be considered "fresh"
DEFAULT_DEPENDENCY_MAX_AGE_SECONDS = 300


@dataclass
class PreFlightResult:
    """Result of pre-flight analysis. Pass = proceed; fail = block with reason."""

    passed: bool
    reason: str = ""
    risk_score: int = 0
    escalated_to_restricted: bool = False
    state_checks: Dict[str, bool] = field(default_factory=dict)
    dependency_ok: bool = True


# ---------------------------------------------------------------------------
# State validation: API availability, file locks, system resources
# ---------------------------------------------------------------------------

def _sync_check_api_available(url: str, timeout_seconds: float = 5.0) -> bool:
    """Sync probe URL (HEAD). Used from run_in_executor."""
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as _:
            return True
    except Exception as e:
        logger.debug("check_api_available %s: %s", url[:80], e)
        return False


async def check_api_available(url: str, timeout_seconds: float = 5.0) -> bool:
    """Probe URL (HEAD) to verify API is reachable. Non-blocking (run in executor)."""
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, _sync_check_api_available, url, timeout_seconds),
        timeout=timeout_seconds + 2.0,
    )


def _sync_check_file_unlocked(filepath: str) -> bool:
    """Sync Windows file lock check. Used from run_in_executor."""
    try:
        import msvcrt
        import os
        if not os.path.isfile(filepath):
            return True
        with open(filepath, "rb") as f:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                return True
            except OSError:
                return False
    except ImportError:
        return True
    except Exception as e:
        logger.debug("check_file_unlocked %s: %s", filepath[:80], e)
        return False


async def check_file_unlocked(filepath: str) -> bool:
    """On Windows: try to open file in exclusive mode. Non-blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_check_file_unlocked, filepath)


async def check_resource_available(_context: ExecutionContext) -> bool:
    """Placeholder: check system resources (e.g. memory). Default pass."""
    return True


# Built-in state checkers: key -> async (value, context) -> bool
_STATE_CHECKERS: Dict[str, Callable[..., Any]] = {
    "api_available": lambda url, ctx: check_api_available(url),
    "file_unlocked": lambda path, ctx: check_file_unlocked(path),
    "resource_available": lambda _, ctx: check_resource_available(ctx),
}


def register_state_checker(key: str, checker: Callable[..., Any]) -> None:
    """Register a custom state checker. checker(value, context) -> await bool."""
    _STATE_CHECKERS[key] = checker


async def run_state_validation(
    requirements: List[Tuple[str, Any]],
    context: ExecutionContext,
) -> Tuple[bool, Dict[str, bool]]:
    """
    requirements: e.g. [("api_available", "https://api.example.com"), ("file_unlocked", "/path/to/file")]
    Returns (all_passed, {check_key: passed}).
    """
    results: Dict[str, bool] = {}
    for req_key, req_value in requirements:
        checker = _STATE_CHECKERS.get(req_key)
        if not checker:
            logger.warning("preflight unknown requirement key=%s", req_key)
            results[req_key] = True
            continue
        try:
            result = checker(req_value, context)
            if asyncio.iscoroutine(result):
                ok = await result
            else:
                ok = bool(result)
            results[req_key] = ok
        except Exception as e:
            logger.exception("preflight check failed key=%s", req_key)
            results[req_key] = False
    all_passed = all(results.values())
    return all_passed, results


# ---------------------------------------------------------------------------
# Dependency mapping: verify payload from upstream skill is valid and fresh
# ---------------------------------------------------------------------------

# In-memory cache: (mission_id, work_item_id, skill_name) -> (payload_hash, ts)
_dependency_output_cache: Dict[Tuple[str, int, str], Tuple[str, float]] = {}


def record_skill_output(chain_id: str, mission_id: str, work_item_id: int, skill_name: str, output_hash: str) -> None:
    """Record that a skill produced output (for dependency freshness)."""
    key = (mission_id, work_item_id, skill_name)
    _dependency_output_cache[key] = (output_hash, time.time())


def get_dependency_freshness(mission_id: str, work_item_id: int, skill_name: str, max_age_seconds: float) -> Tuple[bool, bool]:
    """
    Returns (exists, is_fresh). If no record, exists=False. If record older than max_age_seconds, is_fresh=False.
    """
    key = (mission_id, work_item_id, skill_name)
    entry = _dependency_output_cache.get(key)
    if not entry:
        return False, False
    _, ts = entry
    return True, (time.time() - ts) <= max_age_seconds


async def run_dependency_check(
    params: Dict[str, Any],
    context: ExecutionContext,
    max_age_seconds: float = DEFAULT_DEPENDENCY_MAX_AGE_SECONDS,
) -> bool:
    """
    If params contain "_depends_on": [{"skill": "B", "output_key": "data", "max_age_seconds": 60}],
    verify that B's output for this mission/work_item is recorded and fresh.
    """
    depends = params.get("_depends_on")
    if not isinstance(depends, list) or not depends:
        return True
    for dep in depends:
        if not isinstance(dep, dict):
            continue
        skill_name = (dep.get("skill") or "").strip().upper()
        if not skill_name:
            continue
        max_age = float(dep.get("max_age_seconds", max_age_seconds))
        exists, fresh = get_dependency_freshness(context.mission_id, context.work_item_id, skill_name, max_age)
        if not exists:
            logger.warning("preflight dependency missing skill=%s mission_id=%s", skill_name, context.mission_id)
            return False
        if not fresh:
            logger.warning("preflight dependency stale skill=%s mission_id=%s", skill_name, context.mission_id)
            return False
    return True


# ---------------------------------------------------------------------------
# Risk scoring: dynamic score from params; escalate to Restricted if > threshold
# ---------------------------------------------------------------------------

def _risk_http_request(params: Dict[str, Any]) -> int:
    """Score 0-100 for http_request params. Higher for write methods, delete, sensitive paths."""
    score = 0
    method = (params.get("method") or "GET").upper()
    if method in ("POST", "PUT", "PATCH"):
        score += 25
    if method == "DELETE":
        score += 50
    url = (params.get("url") or "").lower()
    if "delete" in url or "remove" in url or "destroy" in url:
        score += 20
    if "admin" in url or "internal" in url:
        score += 15
    body = params.get("body")
    if body is not None and body != "":
        score += 10
    return min(100, score)


def _risk_run_script(params: Dict[str, Any]) -> int:
    """Score 0-100 for run_script. Higher for paths with rm, del, drop, etc."""
    score = 20
    path = (params.get("script_path") or "").lower()
    risky = re.compile(r"\b(rm|del|drop|format|wipe|shred)\b", re.I)
    if risky.search(path):
        score += 40
    args = params.get("args") or []
    if args:
        score += min(20, len(args) * 5)
    return min(100, score)


_RISK_SCORERS: Dict[str, Callable[[Dict[str, Any]], int]] = {
    "HTTP_REQUEST": _risk_http_request,
    "RUN_SCRIPT": _risk_run_script,
}


def register_risk_scorer(skill_name: str, scorer: Callable[[Dict[str, Any]], int]) -> None:
    """Register a risk scorer for a skill. Scorer(params) -> 0-100."""
    _RISK_SCORERS[skill_name.upper()] = scorer


def compute_risk_score(skill_name: str, params: Dict[str, Any]) -> int:
    """Return 0-100 risk score for this skill+params."""
    scorer = _RISK_SCORERS.get((skill_name or "").strip().upper())
    if not scorer:
        return 0
    try:
        return min(100, max(0, scorer(params)))
    except Exception:
        return 50


# ---------------------------------------------------------------------------
# Run full pre-flight
# ---------------------------------------------------------------------------

async def run_preflight(
    skill_name: str,
    skill_requirements: List[Tuple[str, Any]],
    params: Dict[str, Any],
    context: ExecutionContext,
    *,
    risk_escalation_threshold: int = DEFAULT_RISK_ESCALATION_THRESHOLD,
    dependency_max_age: float = DEFAULT_DEPENDENCY_MAX_AGE_SECONDS,
) -> PreFlightResult:
    """
    Run state validation, dependency check, and risk scoring.
    If risk_score > threshold, set escalated_to_restricted=True (caller must route via Gatekeeper).
    """
    state_ok, state_checks = await run_state_validation(skill_requirements, context)
    if not state_ok:
        return PreFlightResult(
            passed=False,
            reason="state validation failed",
            state_checks=state_checks,
            dependency_ok=True,
        )
    dep_ok = await run_dependency_check(params, context, max_age_seconds=dependency_max_age)
    if not dep_ok:
        return PreFlightResult(
            passed=False,
            reason="dependency missing or stale",
            state_checks=state_checks,
            dependency_ok=False,
        )
    risk_score = compute_risk_score(skill_name, params)
    escalated = risk_score > risk_escalation_threshold
    if escalated:
        logger.info(
            "preflight risk escalation skill=%s risk_score=%s threshold=%s",
            skill_name,
            risk_score,
            risk_escalation_threshold,
        )
    return PreFlightResult(
        passed=True,
        reason="",
        risk_score=risk_score,
        escalated_to_restricted=escalated,
        state_checks=state_checks,
        dependency_ok=True,
    )
