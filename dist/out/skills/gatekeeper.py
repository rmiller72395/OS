# skills/gatekeeper.py — Orchestration: route by access_level; approval for RESTRICTED (v5.0)
#
# GLOBAL → execute immediately. RESTRICTED → serialize intent, wait for Approval, log approver, then execute.
# See EXECUTION_LAYER_REFACTOR_PLAN.md.

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from execution_models import ActionResult, ExecutionContext
from skills.base import AccessLevel, BaseSkill

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """Serialized intent for human/process verification before RESTRICTED execution."""

    tool_name: str
    params_hash: str
    mission_id: str
    work_item_id: int
    permit_id: Optional[str]
    worker: str
    side_effects_description: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalResult:
    """Result of an approval check: approved or denied; if approved, who and when."""

    approved: bool
    approved_by: str = ""
    approved_at: str = ""
    reason: str = ""


class ApprovalProvider:
    """
    Protocol for obtaining approval for RESTRICTED skills.
    Implementations: in-memory (tests), Discord-driven (owner /approve_execution <id>), etc.
    """

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        """
        Wait for approval. Return ApprovalResult(approved=True, approved_by=..., approved_at=...)
        or ApprovalResult(approved=False, reason=...).
        Default: deny if no implementation (fail-closed).
        """
        return ApprovalResult(approved=False, reason="no approval provider configured")


async def run_via_gatekeeper(
    skill: BaseSkill,
    params: Dict[str, Any],
    context: ExecutionContext,
    *,
    params_hash: str,
    approval_provider: Optional[ApprovalProvider] = None,
    log_action: Optional[Callable[..., Any]] = None,
    log_approval: Optional[Callable[..., Any]] = None,
    force_approval: bool = False,
) -> ActionResult:
    """
    Route by access_level. GLOBAL → execute immediately. RESTRICTED (or force_approval) → request approval, then execute and log approver.
    """
    if skill.access_level == AccessLevel.GLOBAL and not force_approval:
        return await skill.execute(params, context)

    # RESTRICTED: pause, serialize intent, wait for approval
    request = ApprovalRequest(
        tool_name=skill.name,
        params_hash=params_hash,
        mission_id=context.mission_id,
        work_item_id=context.work_item_id,
        permit_id=context.permit_id,
        worker=context.worker,
        side_effects_description=getattr(skill, "description", skill.description),
    )
    provider = approval_provider or ApprovalProvider()
    result = await provider.request_approval(request)
    if not result.approved:
        logger.info(
            "gatekeeper_denied tool=%s mission_id=%s work_item_id=%s reason=%s",
            skill.name,
            context.mission_id,
            context.work_item_id,
            result.reason,
        )
        return ActionResult("SKIP_NO_PERMIT", result.reason or "approval denied", None)

    # Log identity of approver (audit)
    if log_approval:
        try:
            if asyncio.iscoroutinefunction(log_approval):
                await log_approval(
                    context.mission_id,
                    context.work_item_id,
                    context.permit_id,
                    skill.name,
                    params_hash,
                    result.approved_by,
                    result.approved_at,
                )
            else:
                log_approval(
                    context.mission_id,
                    context.work_item_id,
                    context.permit_id,
                    skill.name,
                    params_hash,
                    result.approved_by,
                    result.approved_at,
                )
        except Exception as e:
            logger.error("log_approval failed: %s", e)

    return await skill.execute(params, context)
