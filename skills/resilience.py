# skills/resilience.py — Self-healing wrapper: exponential backoff, circuit breaker, alternative routing
#
# Transient errors → retry with exponential backoff. Consecutive failures → trip circuit, disable skill, alert.
# On failure, try alternative skill if registered. All async, non-blocking.
# See Intelligence Engine spec.

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from execution_models import ActionResult, ExecutionContext
from skills.base import BaseSkill
from skills.exceptions import RetryableError

logger = logging.getLogger(__name__)

# Circuit breaker: failures before trip
CIRCUIT_FAILURE_THRESHOLD = 3
# Seconds before half-open (try again)
CIRCUIT_RESET_SECONDS = 60.0
# Exponential backoff: base delay, max delay, max retries
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
BACKOFF_MAX_RETRIES = 4


@dataclass
class CircuitState:
    """Per-skill circuit state: failures count, tripped_at, last_failure_at."""

    failures: int = 0
    tripped_at: Optional[float] = None
    last_success_at: Optional[float] = None


_circuit_states: Dict[str, CircuitState] = {}
_circuit_lock = asyncio.Lock()

# Alert callback when circuit trips (skill_name -> None). Set by ExecutionManager.
_circuit_alert_callback: Optional[Callable[[str, int], Any]] = None


def set_circuit_alert_callback(callback: Optional[Callable[[str, int], Any]]) -> None:
    """Set callback(skill_name, failure_count) when a circuit trips."""
    global _circuit_alert_callback
    _circuit_alert_callback = callback


async def get_circuit_state(skill_name: str) -> CircuitState:
    """Get or create circuit state for skill. Thread-safe via lock."""
    key = (skill_name or "").strip().upper()
    async with _circuit_lock:
        if key not in _circuit_states:
            _circuit_states[key] = CircuitState()
        return _circuit_states[key]


async def record_success(skill_name: str) -> None:
    """Reset failure count on success (closed state)."""
    state = await get_circuit_state(skill_name)
    async with _circuit_lock:
        state.failures = 0
        state.tripped_at = None
        state.last_success_at = time.time()


async def record_failure(skill_name: str) -> bool:
    """
    Increment failure count. If >= CIRCUIT_FAILURE_THRESHOLD, trip circuit and alert.
    Returns True if circuit was just tripped.
    """
    key = (skill_name or "").strip().upper()
    async with _circuit_lock:
        if key not in _circuit_states:
            _circuit_states[key] = CircuitState()
        state = _circuit_states[key]
        state.failures += 1
        just_tripped = False
        if state.failures >= CIRCUIT_FAILURE_THRESHOLD:
            if state.tripped_at is None:
                state.tripped_at = time.time()
                just_tripped = True
                logger.error(
                    "circuit_breaker_tripped skill=%s failures=%s",
                    skill_name,
                    state.failures,
                )
                if _circuit_alert_callback:
                    try:
                        if asyncio.iscoroutinefunction(_circuit_alert_callback):
                            await _circuit_alert_callback(skill_name, state.failures)
                        else:
                            _circuit_alert_callback(skill_name, state.failures)
                    except Exception as e:
                        logger.exception("circuit_alert_callback failed: %s", e)
    return just_tripped


def is_circuit_open(skill_name: str, state: CircuitState, now: float) -> bool:
    """True if circuit is tripped and not yet past reset window (half-open allowed after reset)."""
    if state.tripped_at is None:
        return False
    if (now - state.tripped_at) >= CIRCUIT_RESET_SECONDS:
        return False
    return True


async def is_skill_disabled(skill_name: str) -> bool:
    """True if circuit is open (skill should not be invoked)."""
    state = await get_circuit_state(skill_name)
    return is_circuit_open(skill_name, state, time.time())


async def try_half_open(skill_name: str) -> bool:
    """If past reset window, reset failure count (half-open: one trial). Returns True if reset."""
    key = (skill_name or "").strip().upper()
    async with _circuit_lock:
        if key not in _circuit_states:
            return True
        state = _circuit_states[key]
        if state.tripped_at is None:
            return True
        if (time.time() - state.tripped_at) < CIRCUIT_RESET_SECONDS:
            return False
        state.failures = 0
        state.tripped_at = None
        logger.info("circuit_half_open skill=%s", skill_name)
        return True


# ---------------------------------------------------------------------------
# Alternative skill routing: skill_name -> list of alternative skill names
# ---------------------------------------------------------------------------

_alternatives: Dict[str, List[str]] = {}
_alternatives_lock = asyncio.Lock()


def register_alternative(skill_name: str, alternative_skill_name: str) -> None:
    """Register an alternative skill for fallback when skill_name fails."""
    key = (skill_name or "").strip().upper()
    alt = (alternative_skill_name or "").strip().upper()
    if key not in _alternatives:
        _alternatives[key] = []
    if alt not in _alternatives[key]:
        _alternatives[key].append(alt)


def get_alternatives(skill_name: str) -> List[str]:
    """Return list of alternative skill names (order = try order)."""
    return list(_alternatives.get((skill_name or "").strip().upper(), []))


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------

async def sleep_backoff(attempt: int) -> None:
    """Sleep with exponential backoff; cap at BACKOFF_MAX_SECONDS."""
    delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** attempt))
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Run execute with resilience: backoff retries, circuit check, alternative routing
# ---------------------------------------------------------------------------

async def run_with_resilience(
    skill: BaseSkill,
    params: Dict[str, Any],
    context: ExecutionContext,
    execute_fn: Callable[..., Any],
    skill_name: str,
    get_skill_fn: Callable[[str], Any],
) -> ActionResult:
    """
    Execute with self-healing:
    1. If circuit open, try half-open; if still open return FAIL (circuit tripped).
    2. Run execute_fn with exponential backoff on RetryableError (up to BACKOFF_MAX_RETRIES).
    3. On success: record_success, return result.
    4. On failure: record_failure. If alternatives exist, try first alternative (recursively with same resilience).
    """
    key = (skill_name or "").strip().upper()
    if await is_skill_disabled(key):
        ok = await try_half_open(key)
        if not ok:
            return ActionResult(
                "FAIL",
                f"circuit open for skill {skill.name}; execution disabled to prevent cascade",
                {"circuit_tripped": True},
            )

    last_error: Optional[Exception] = None
    for attempt in range(BACKOFF_MAX_RETRIES + 1):
        try:
            result = await execute_fn()
            if isinstance(result, ActionResult) and result.outcome == "SUCCESS":
                await record_success(key)
            else:
                await record_failure(key)
            return result
        except RetryableError as e:
            last_error = e
            if attempt < BACKOFF_MAX_RETRIES:
                await sleep_backoff(attempt)
                logger.info(
                    "resilience retry skill=%s attempt=%s",
                    skill.name,
                    attempt + 1,
                )
            else:
                await record_failure(key)
                return ActionResult("FAIL", str(e.message), None)
        except Exception as e:
            last_error = e
            await record_failure(key)
            return ActionResult("FAIL", str(e)[:500], None)

    # Try alternative skills
    alts = get_alternatives(key)
    for alt_name in alts:
        alt_skill = get_skill_fn(alt_name) if get_skill_fn else None
        if not alt_skill or alt_skill is skill:
            continue
        logger.info(
            "resilience alternative_routing primary=%s alternative=%s",
            skill.name,
            alt_skill.name,
        )
        try:
            result = await run_with_resilience(
                alt_skill,
                params,
                context,
                lambda: alt_skill.execute(params, context),
                alt_skill.name,
                get_skill_fn,
            )
            if result.outcome == "SUCCESS":
                return result
        except Exception:
            continue

    return ActionResult(
        "FAIL",
        (last_error and str(last_error)[:500]) or "execution failed after retries and alternatives",
        None,
    )
