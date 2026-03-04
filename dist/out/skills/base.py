# skills/base.py — Strict Skill interface and BaseSkill (v5.0)
#
# Every skill conforms: metadata (name, description, version, access_level),
# validate(params), execute(params, context) with built-in telemetry.
# See EXECUTION_LAYER_REFACTOR_PLAN.md.

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from execution_models import ActionResult, ExecutionContext
from skills.exceptions import AlertableError, ExecutionError, RetryableError, ValidationError

MAX_RETRIES = 2

logger = logging.getLogger(__name__)


class AccessLevel(str, Enum):
    """Determines routing: GLOBAL → execute immediately; RESTRICTED → Gatekeeper approval required."""

    GLOBAL = "GLOBAL"  # Foundational utility; no external validation; execute immediately
    RESTRICTED = "RESTRICTED"  # State-changing or high-risk; MUST go through Gatekeeper


class BaseSkill(ABC):
    """
    Strict Skill interface. All framework skills must conform.
    Metadata: name, description, version, access_level.
    Execution: execute() with built-in telemetry (logs, timing).
    Validation: validate() before execute(); raises ValidationError if invalid.
    Intelligence Engine: pre_flight_check(), post_flight_report(), requirements, alternative_skill_names.
    """

    def __init__(
        self,
        name: str,
        description: str,
        version: str = "1.0.0",
        access_level: AccessLevel = AccessLevel.GLOBAL,
        idempotent: bool = False,
        requirements: Optional[List[Tuple[str, Any]]] = None,
        alternative_skill_names: Optional[List[str]] = None,
    ) -> None:
        self.name = name.strip()
        self.description = description
        self.version = version
        self.access_level = access_level
        self.idempotent = idempotent
        self.requirements: List[Tuple[str, Any]] = requirements or []
        self.alternative_skill_names: List[str] = list(alternative_skill_names or [])
        if not self.name:
            raise ValueError("Skill name cannot be empty")

    async def pre_flight_check(self, params: Dict[str, Any], context: ExecutionContext) -> Optional[Any]:
        """
        Optional pre-flight check. Return falsy or passed result to proceed; return result with passed=False to block.
        Engine also runs global preflight (state, dependency, risk). Default: return None (proceed).
        """
        return None

    async def post_flight_report(
        self,
        params: Dict[str, Any],
        context: ExecutionContext,
        result: ActionResult,
        duration_ms: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Optional extra data for telemetry. Engine always records chain_id, trace_id, duration, outcome.
        Return dict to merge into post-flight report. Default: return None.
        """
        return None

    def validate(self, params: Dict[str, Any]) -> None:
        """
        Pre-execution validation. Ensure inputs meet schema requirements.
        Raises ValidationError if invalid. Default: accept any dict.
        """
        if not isinstance(params, dict):
            raise ValidationError("params must be a dict")

    @abstractmethod
    async def _execute_impl(self, params: Dict[str, Any], context: ExecutionContext) -> ActionResult:
        """Implement actual execution. Subclasses override this."""
        ...

    async def execute(self, params: Dict[str, Any], context: ExecutionContext) -> ActionResult:
        """
        Standardized execute() with telemetry: validation, trace_id, timing, logging.
        Calls validate() then _execute_impl().
        """
        trace_id = getattr(context, "trace_id", None) or str(uuid.uuid4())[:8]
        start = time.perf_counter()
        logger.info(
            "skill_start name=%s trace_id=%s mission_id=%s work_item_id=%s",
            self.name,
            trace_id,
            context.mission_id,
            context.work_item_id,
        )
        try:
            self.validate(params)
            result = await self._execute_impl(params, context)
            elapsed = time.perf_counter() - start
            logger.info(
                "skill_end name=%s trace_id=%s outcome=%s elapsed_ms=%.0f",
                self.name,
                trace_id,
                result.outcome,
                elapsed * 1000,
            )
            return result
        except ValidationError as e:
            elapsed = time.perf_counter() - start
            logger.warning(
                "skill_validation_failed name=%s trace_id=%s reason=%s elapsed_ms=%.0f",
                self.name,
                trace_id,
                e.message,
                elapsed * 1000,
            )
            return ActionResult("FAIL", str(e.message), None)
        except RetryableError as e:
            elapsed = time.perf_counter() - start
            last_error = e
            for attempt in range(MAX_RETRIES):
                try:
                    result = await self._execute_impl(params, context)
                    logger.info(
                        "skill_retry_success name=%s trace_id=%s attempt=%s",
                        self.name,
                        trace_id,
                        attempt + 1,
                    )
                    return result
                except RetryableError as retry_e:
                    last_error = retry_e
            logger.warning(
                "skill_retry_exhausted name=%s trace_id=%s elapsed_ms=%.0f",
                self.name,
                trace_id,
                (time.perf_counter() - start) * 1000,
            )
            return ActionResult("FAIL", str(last_error.message), None)
        except AlertableError as e:
            elapsed = time.perf_counter() - start
            logger.error(
                "skill_alert name=%s trace_id=%s reason=%s elapsed_ms=%.0f",
                self.name,
                trace_id,
                e.message,
                elapsed * 1000,
            )
            return ActionResult("FAIL", str(e.message), None)
        except ExecutionError as e:
            elapsed = time.perf_counter() - start
            logger.warning(
                "skill_execution_error name=%s trace_id=%s reason=%s elapsed_ms=%.0f",
                self.name,
                trace_id,
                e.message,
                elapsed * 1000,
            )
            return ActionResult("FAIL", str(e.message), None)
        except Exception as e:
            elapsed = time.perf_counter() - start
            logger.exception(
                "skill_execution_error name=%s trace_id=%s elapsed_ms=%.0f",
                self.name,
                trace_id,
                elapsed * 1000,
            )
            return ActionResult("FAIL", str(e)[:500], None)
