# skills/execution_manager.py — Intelligence Engine: preflight, resilience, telemetry, orchestration
#
# ExecutionManager runs the full pipeline: context check (preflight) → risk escalation →
# circuit check → resilience wrapper (backoff, alternative) → gatekeeper → execute →
# post-flight report and telemetry. All async, non-blocking.
# See Intelligence Engine spec.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from execution_models import ActionResult, ExecutionContext
from skills.base import AccessLevel, BaseSkill
from skills.gatekeeper import ApprovalProvider, run_via_gatekeeper
from skills.preflight import PreFlightResult, record_skill_output as preflight_record_output, run_preflight
from skills.registry import get_skill
from skills.resilience import (
    get_alternatives,
    register_alternative,
    run_with_resilience,
    set_circuit_alert_callback,
    is_skill_disabled,
)
from skills.telemetry import (
    PostFlightReport,
    build_post_flight_report,
    get_telemetry_store,
    TelemetryStore,
)
from skills.knowledge_store import (
    get_knowledge_store,
    KnowledgeStore,
)
from skills.tool_costing import compute_tool_cost
from skills.tool_grants import (
    authorize_tool_call,
    get_tool_grant_store,
    ToolGrantStore,
)
from skills.tool_registry import get_tool_registry, ToolRegistry

logger = logging.getLogger(__name__)


def _params_hash(params: Dict[str, Any]) -> str:
    try:
        return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except Exception:
        return "?"


class ExecutionManager:
    """
    Orchestrates the Intelligence Engine: pre-flight analysis, risk escalation,
    circuit breaker, exponential backoff, alternative routing, gatekeeper, and post-flight telemetry.
    """

    def __init__(
        self,
        telemetry_store: Optional[TelemetryStore] = None,
        knowledge_store: Optional[KnowledgeStore] = None,
        risk_escalation_threshold: int = 70,
        circuit_alert_callback: Optional[Callable[[str, int], Any]] = None,
    ) -> None:
        self._telemetry = telemetry_store or get_telemetry_store()
        self._knowledge = knowledge_store or get_knowledge_store()
        self._risk_threshold = risk_escalation_threshold
        if circuit_alert_callback:
            set_circuit_alert_callback(circuit_alert_callback)

        # Optional: configure alternative skills via ALTERNATIVE_SKILLS env (JSON mapping primary->alternative)
        try:
            raw_alts = os.getenv("ALTERNATIVE_SKILLS", "").strip()
            if raw_alts:
                mapping = json.loads(raw_alts)
                if isinstance(mapping, dict):
                    for primary, alt in mapping.items():
                        if primary and isinstance(alt, str):
                            register_alternative(str(primary), alt)
        except Exception as e:
            logger.warning("Failed to load ALTERNATIVE_SKILLS mapping: %s", e)

    async def _ensure_alternatives_registered(self, skill: BaseSkill) -> None:
        """Register skill's alternative_skill_names with resilience layer."""
        for alt in skill.alternative_skill_names or []:
            if (alt or "").strip().upper() != (skill.name or "").strip().upper():
                register_alternative(skill.name, (alt or "").strip())

    async def _run_single_action(
        self,
        skill: BaseSkill,
        tool_name: str,
        params: Dict[str, Any],
        context: ExecutionContext,
        chain_id: str,
        *,
        approval_provider: Optional[ApprovalProvider] = None,
        log_action: Optional[Callable[..., Any]] = None,
        log_approval: Optional[Callable[..., Any]] = None,
    ) -> ActionResult:
        # Failure injection (SIMULATION_MODE / preflight): raise before execution for deterministic tests
        try:
            from skills.testing.failure_injection import check_inject_failure
            check_inject_failure(tool_name, step_id=f"{getattr(context, 'run_id', '')}:{context.work_item_id}")
        except (TimeoutError, ConnectionError, ValueError):
            raise
        except Exception:
            pass

        trace_id = getattr(context, "trace_id", None) or uuid.uuid4().hex[:8]
        ctx = ExecutionContext(
            mission_id=context.mission_id,
            work_item_id=context.work_item_id,
            permit_id=context.permit_id,
            worker=context.worker,
            channel_id=context.channel_id,
            timeout_seconds=context.timeout_seconds,
            allowed_tools=context.allowed_tools,
            trace_id=trace_id,
            chain_id=chain_id,
            ticket_id=getattr(context, "ticket_id", None),
            run_id=getattr(context, "run_id", None),
        )
        params_hash = _params_hash(params)

        # 1. Skill-specific pre_flight_check (optional)
        try:
            custom = await skill.pre_flight_check(params, ctx)
            if custom is not None and getattr(custom, "passed", True) is False:
                reason = getattr(custom, "reason", "pre_flight_check failed")
                return ActionResult("FAIL", reason, None)
        except Exception as e:
            logger.exception("pre_flight_check error skill=%s", skill.name)
            return ActionResult("FAIL", str(e)[:500], None)

        # 2. Global preflight: state, dependency, risk
        preflight = await run_preflight(
            tool_name,
            getattr(skill, "requirements", []) or [],
            params,
            ctx,
            risk_escalation_threshold=self._risk_threshold,
        )
        if not preflight.passed:
            return ActionResult("FAIL", preflight.reason or "preflight failed", None)
        force_approval = preflight.escalated_to_restricted

        # 2.5. Consult Memory: adjust strategy from past success/failure patterns
        try:
            hints = await self._knowledge.consult_memory(
                tool_name, params, ctx.timeout_seconds
            )
            if hints.suggested_timeout_seconds and hints.suggested_timeout_seconds > ctx.timeout_seconds:
                ctx = ExecutionContext(
                    mission_id=ctx.mission_id,
                    work_item_id=ctx.work_item_id,
                    permit_id=ctx.permit_id,
                    worker=ctx.worker,
                    channel_id=ctx.channel_id,
                    timeout_seconds=hints.suggested_timeout_seconds,
                    allowed_tools=ctx.allowed_tools,
                    trace_id=ctx.trace_id,
                    chain_id=ctx.chain_id,
                )
                logger.info(
                    "knowledge_store timeout_adjusted skill=%s timeout_seconds=%.0f (from memory)",
                    tool_name,
                    ctx.timeout_seconds,
                )
            # Try suggested alternative skill first if we have prior failures for this task
            if hints.suggested_alternative_skill and hints.failure_count > 0:
                alt_skill = get_skill(hints.suggested_alternative_skill)
                if alt_skill and alt_skill is not skill:
                    logger.info(
                        "knowledge_store trying_alternative_first primary=%s alternative=%s (from memory)",
                        tool_name,
                        hints.suggested_alternative_skill,
                    )
                    start_alt = time.perf_counter()
                    async def _do_execute_alt() -> ActionResult:
                        return await run_via_gatekeeper(
                            alt_skill,
                            params,
                            ctx,
                            params_hash=params_hash,
                            approval_provider=approval_provider,
                            log_action=None,
                            log_approval=log_approval,
                            force_approval=force_approval,
                        )
                    alt_result = await run_with_resilience(
                        alt_skill,
                        params,
                        ctx,
                        _do_execute_alt,
                        hints.suggested_alternative_skill,
                        get_skill,
                    )
                    if alt_result.outcome == "SUCCESS":
                        duration_ms = (time.perf_counter() - start_alt) * 1000.0
                        try:
                            extra = await alt_skill.post_flight_report(params, ctx, alt_result, duration_ms)
                        except Exception:
                            extra = None
                        report = build_post_flight_report(
                            chain_id=chain_id,
                            trace_id=trace_id,
                            skill_name=alt_skill.name,
                            mission_id=context.mission_id,
                            work_item_id=context.work_item_id,
                            input_hash=params_hash,
                            output_summary=alt_result.result_summary or "",
                            duration_ms=duration_ms,
                            outcome=alt_result.outcome,
                        )
                        if extra and isinstance(extra, dict) and extra.get("refactoring_needed"):
                            report.refactoring_flag = True
                        await self._telemetry.record_report(report)
                        await self._knowledge.record_success(
                            alt_skill.name,
                            [alt_skill.name],
                            params,
                            chain_id,
                            context.mission_id,
                            context.work_item_id,
                        )
                        output_hash = hashlib.sha256((alt_result.result_summary or "").encode()).hexdigest()[:16]
                        preflight_record_output(chain_id, context.mission_id, context.work_item_id, alt_skill.name, output_hash)
                        return alt_result
                    # Alternative was tried first and failed; record for correlation
                    try:
                        env_ctx = {"timeout_seconds": ctx.timeout_seconds, "mission_id": context.mission_id, "work_item_id": context.work_item_id}
                        await self._knowledge.record_failure(
                            alt_skill.name,
                            alt_result.result_summary or alt_result.outcome,
                            params,
                            env_ctx,
                            alt_result.outcome,
                            chain_id,
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("knowledge_store consult_memory error: %s", e)

        # 3. Execute via resilience (backoff, circuit, alternatives) and gatekeeper
        start_ms = time.perf_counter()
        async def _do_execute() -> ActionResult:
            return await run_via_gatekeeper(
                skill,
                params,
                ctx,
                params_hash=params_hash,
                approval_provider=approval_provider,
                log_action=None,
                log_approval=log_approval,
                force_approval=force_approval,
            )

        result = await run_with_resilience(
            skill,
            params,
            ctx,
            _do_execute,
            tool_name,
            get_skill,
        )
        duration_ms = (time.perf_counter() - start_ms) * 1000.0

        # 4. Post-flight report and telemetry
        try:
            extra = await skill.post_flight_report(params, ctx, result, duration_ms)
        except Exception:
            extra = None
        report = build_post_flight_report(
            chain_id=chain_id,
            trace_id=trace_id,
            skill_name=skill.name,
            mission_id=context.mission_id,
            work_item_id=context.work_item_id,
            input_hash=params_hash,
            output_summary=result.result_summary or "",
            duration_ms=duration_ms,
            outcome=result.outcome,
        )
        if extra and isinstance(extra, dict):
            if extra.get("refactoring_needed") and not report.refactoring_flag:
                report.refactoring_flag = True
        await self._telemetry.record_report(report)

        # Commit insights to Knowledge Store (Global Memory) after every execution
        try:
            skill_chain = [tool_name]
            if result.outcome == "SUCCESS":
                await self._knowledge.record_success(
                    skill.name,
                    skill_chain,
                    params,
                    chain_id,
                    context.mission_id,
                    context.work_item_id,
                )
            else:
                env_context = {
                    "timeout_seconds": ctx.timeout_seconds,
                    "mission_id": context.mission_id,
                    "work_item_id": context.work_item_id,
                }
                await self._knowledge.record_failure(
                    skill.name,
                    result.result_summary or result.outcome,
                    params,
                    env_context,
                    result.outcome,
                    chain_id,
                )
        except Exception as e:
            logger.warning("knowledge_store record after execution failed: %s", e)

        # Record output for dependency freshness (if SUCCESS)
        if result.outcome == "SUCCESS":
            output_hash = hashlib.sha256((result.result_summary or "").encode()).hexdigest()[:16]
            preflight_record_output(chain_id, context.mission_id, context.work_item_id, skill.name, output_hash)

        return result

    async def run_actions(
        self,
        actions: List[Dict[str, Any]],
        context: ExecutionContext,
        *,
        log_action: Optional[Callable[..., Any]] = None,
        log_approval: Optional[Callable[..., Any]] = None,
        approval_provider: Optional[ApprovalProvider] = None,
        stop_on_first_failure: bool = True,
        action_log_has_committed: Optional[Callable[[str], Any]] = None,
    ) -> List[ActionResult]:
        """
        Execute actions through the full Intelligence Engine pipeline.
        Generates one chain_id (UUID) per call for end-to-end traceability.
        For side_effect tools: requires idempotency_key (run_id:step_id), writes started then committed.
        """
        chain_id = uuid.uuid4().hex
        results: List[ActionResult] = []

        for action_idx, action in enumerate(actions):
            if not isinstance(action, dict):
                results.append(ActionResult("SKIP_UNKNOWN_TOOL", "invalid action item", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, "?", "?", "SKIP_UNKNOWN_TOOL", "invalid action item")
                if stop_on_first_failure:
                    break
                continue

            tool_name = (action.get("tool") or "").strip().upper()
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            if not tool_name:
                results.append(ActionResult("SKIP_UNKNOWN_TOOL", "missing tool name", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, "?", _params_hash(params), "SKIP_UNKNOWN_TOOL", "missing tool name")
                if stop_on_first_failure:
                    break
                continue

            skill = get_skill(tool_name)
            if not skill:
                results.append(ActionResult("SKIP_UNKNOWN_TOOL", "tool not registered", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_UNKNOWN_TOOL", "tool not registered")
                if stop_on_first_failure:
                    break
                continue

            if skill.access_level == AccessLevel.RESTRICTED and not (context.permit_id and str(context.permit_id).strip()):
                results.append(ActionResult("SKIP_NO_PERMIT", "permit required but missing", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_NO_PERMIT", "permit required but missing")
                if stop_on_first_failure:
                    break
                continue

            if context.allowed_tools is not None and tool_name not in context.allowed_tools:
                results.append(ActionResult("SKIP_UNKNOWN_TOOL", "tool not in allowlist", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_UNKNOWN_TOOL", "tool not in allowlist")
                if stop_on_first_failure:
                    break
                continue

            # Tool grants: require registry + active grant for every tool call (default-deny)
            auth_result = None
            tool_def_for_cost = None
            ticket_id = getattr(context, "ticket_id", None)
            run_id = getattr(context, "run_id", None)
            if not ticket_id and not run_id:
                results.append(ActionResult("SKIP_TOOL_DENIED", "no ticket/run context; grant required", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", "no ticket/run context; grant required")
                if stop_on_first_failure:
                    break
                continue
            try:
                registry = get_tool_registry()
                grants = get_tool_grant_store()
                registry.ensure_schema()
                grants.ensure_schema()
                tool_def_for_cost = registry.get_tool(tool_name)
                if not tool_def_for_cost or not tool_def_for_cost.enabled:
                    results.append(ActionResult("SKIP_TOOL_DENIED", "tool not in registry or disabled", None))
                    if log_action:
                        await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", "tool not in registry or disabled")
                    if stop_on_first_failure:
                        break
                    continue
                requested_scopes = action.get("scopes") if isinstance(action.get("scopes"), list) else (tool_def_for_cost.scopes or [])
                proposed_cost = compute_tool_cost(tool_def_for_cost.cost_model_json, 0.0, 1)
                from datetime import datetime, timezone
                now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                auth_result = authorize_tool_call(
                    registry, grants, tool_name, requested_scopes,
                    ticket_id, run_id,
                    params, proposed_cost, now_utc,
                )
                if not auth_result.allowed:
                    results.append(ActionResult(
                        "SKIP_TOOL_DENIED",
                        auth_result.reason + ((": " + "; ".join(auth_result.constraint_violations or [])) if auth_result.constraint_violations else ""),
                        {"matched_grant_id": auth_result.matched_grant_id},
                    ))
                    if log_action:
                        await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", auth_result.reason)
                    if stop_on_first_failure:
                        break
                    continue
            except Exception as e:
                logger.warning("Tool grant check failed (fail-closed): %s", e)
                results.append(ActionResult("SKIP_TOOL_DENIED", f"authorization error: {e}", None))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", str(e))
                if stop_on_first_failure:
                    break
                continue

            # Side-effect tools: idempotency_key (run_id:work_item_id:action_idx), started then committed
            idempotency_key: Optional[str] = None  # set below when side_effect
            if tool_def_for_cost and getattr(tool_def_for_cost, "side_effect", False):
                run_id_ctx = getattr(context, "run_id", None)
                if not run_id_ctx:
                    results.append(ActionResult("SKIP_TOOL_DENIED", "side_effect requires run_id for idempotency_key", None))
                    if log_action:
                        await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", "side_effect requires run_id")
                    if stop_on_first_failure:
                        break
                    continue
                idempotency_key = f"{run_id_ctx}:{context.work_item_id}:{action_idx}"
                if action_log_has_committed:
                    try:
                        committed = await action_log_has_committed(idempotency_key) if asyncio.iscoroutinefunction(action_log_has_committed) else action_log_has_committed(idempotency_key)
                        if committed:
                            results.append(ActionResult("SUCCESS", "already committed (idempotent)", None))
                            if log_action:
                                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SUCCESS", "already committed", idempotency_key=idempotency_key, phase="committed")
                            if stop_on_first_failure:
                                break
                            continue
                    except Exception as e:
                        logger.warning("action_log_has_committed check failed: %s", e)
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "started", "", idempotency_key=idempotency_key, phase="started")

            if await is_skill_disabled(tool_name):
                results.append(ActionResult("FAIL", f"skill {tool_name} disabled (circuit open)", {"circuit_tripped": True}))
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "FAIL", "circuit open")
                if stop_on_first_failure:
                    break
                continue

            await self._ensure_alternatives_registered(skill)

            start = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    self._run_single_action(
                        skill,
                        tool_name,
                        params,
                        context,
                        chain_id,
                        approval_provider=approval_provider,
                        log_action=log_action,
                        log_approval=log_approval,
                    ),
                    timeout=context.timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = ActionResult("TIMEOUT", f"tool timed out after {context.timeout_seconds}s", None)
            except Exception as e:
                logger.exception("ExecutionManager run error")
                result = ActionResult("FAIL", str(e)[:500], None)

            if not isinstance(result, ActionResult):
                result = ActionResult("FAIL", "handler did not return ActionResult", None)

            # Record tool spend against grant when ticket/run scoped and allowed
            if result.outcome == "SUCCESS" and auth_result and auth_result.matched_grant_id and tool_def_for_cost:
                try:
                    duration_s = time.perf_counter() - start
                    actual_cost = compute_tool_cost(tool_def_for_cost.cost_model_json, duration_s, 1)
                    get_tool_grant_store().record_usage(auth_result.matched_grant_id, 1, actual_cost)
                except Exception as e:
                    logger.warning("Tool grant usage record failed: %s", e)

            results.append(result)
            if log_action:
                await _safe_log(
                    log_action, context.mission_id, context.work_item_id, context.permit_id,
                    tool_name, _params_hash(params), result.outcome, (result.result_summary or "")[:500],
                    idempotency_key=idempotency_key if tool_def_for_cost and getattr(tool_def_for_cost, "side_effect", False) else None,
                    phase="committed" if (tool_def_for_cost and getattr(tool_def_for_cost, "side_effect", False)) else "committed",
                )

            if stop_on_first_failure and result.outcome not in ("SUCCESS",):
                break

        return results


async def _safe_log(
    log_action: Callable[..., Any],
    mission_id: str,
    work_item_id: int,
    permit_id: Optional[str],
    tool: str,
    params_hash: str,
    outcome: str,
    result_summary: str,
    idempotency_key: Optional[str] = None,
    phase: str = "committed",
) -> None:
    try:
        kwargs: Dict[str, Any] = {}
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key
        if phase:
            kwargs["phase"] = phase
        if asyncio.iscoroutinefunction(log_action):
            await log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary, **kwargs)
        else:
            log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary, **kwargs)
    except TypeError:
        # Backward compat: callback may not accept idempotency_key/phase
        try:
            if asyncio.iscoroutinefunction(log_action):
                await log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary)
            else:
                log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary)
        except Exception as e:
            logger.error("action_log callback failed: %s", e)
    except Exception as e:
        logger.error("action_log callback failed: %s", e)
