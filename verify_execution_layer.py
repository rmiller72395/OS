from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from execution import ExecutionContext, run_actions, get_execution_manager
from execution_models import ActionResult, ExecutionContext as EC
from skills import AccessLevel, BaseSkill
from skills.gatekeeper import ApprovalProvider, ApprovalRequest, ApprovalResult
from skills.registry import register_skill
from skills.tool_registry import get_tool_registry
from skills.tool_grants import get_tool_grant_store, ToolGrant


class NoOpGlobalSkill(BaseSkill):
    def __init__(self) -> None:
        super().__init__(
            name="noop_global",
            description="Verification global skill (no-op).",
            access_level=AccessLevel.GLOBAL,
        )

    async def _execute_impl(self, params: Dict[str, Any], context: EC) -> ActionResult:
        return ActionResult("SUCCESS", "noop_global ok", None)


class NoOpRestrictedSkill(BaseSkill):
    def __init__(self) -> None:
        super().__init__(
            name="noop_restricted",
            description="Verification restricted skill (no-op).",
            access_level=AccessLevel.RESTRICTED,
        )

    async def _execute_impl(self, params: Dict[str, Any], context: EC) -> ActionResult:
        return ActionResult("SUCCESS", "noop_restricted ok", None)


class AutoApprovalProvider(ApprovalProvider):
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        return ApprovalResult(
            approved=True,
            approved_by="verification-script",
            approved_at="now",
            reason="auto-approved for verification",
        )


async def _run_verification() -> int:
    # Integrity: BaseSkill subclassing and ExecutionManager load
    try:
        global_skill = NoOpGlobalSkill()
        restricted_skill = NoOpRestrictedSkill()
    except Exception as e:
        print(f"[verify] Failed to construct skills: {e}", file=sys.stderr)
        return 1

    try:
        register_skill(global_skill)
        register_skill(restricted_skill)
    except Exception as e:
        print(f"[verify] Failed to register skills: {e}", file=sys.stderr)
        return 1

    try:
        manager = get_execution_manager()
        assert manager is not None
    except Exception as e:
        print(f"[verify] Failed to load ExecutionManager: {e}", file=sys.stderr)
        return 1

    # v5.0: default-deny — seed tool registry + run-scoped grant so run_actions can execute
    run_id_verify = "verify-run-1"
    try:
        from skills.tool_registry import ToolDef as RegistryToolDef
        reg = get_tool_registry()
        grants = get_tool_grant_store()
        reg.ensure_schema()
        grants.ensure_schema()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for name, desc in [("noop_global", "Verification global"), ("noop_restricted", "Verification restricted")]:
            t = RegistryToolDef(
                tool_name=name.upper(),
                description=desc,
                input_schema_json={},
                output_schema_json=None,
                scopes=["read:*"],
                side_effect=False,
                idempotency_required=False,
                cost_model_json={},
                default_timeout_s=10,
                max_timeout_s=30,
                rate_limit_json={},
                allowlist_json={},
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            reg.upsert_tool(t)
        if not grants.get_active_grant(run_id=run_id_verify):
            g = ToolGrant(
                grant_id=f"verify-{run_id_verify}-{id(run_id_verify)}",
                ticket_id=None,
                run_id=run_id_verify,
                allowed_tools=["NOOP_GLOBAL", "NOOP_RESTRICTED"],
                allowed_scopes=["read:*"],
                constraints_json={},
                max_tool_spend_usd=1.0,
                max_calls=10,
                expires_at=None,
                issued_by="verify_execution_layer",
                reason="verification run",
                created_at=now,
            )
            grants.create_grant(g)
    except Exception as e:
        print(f"[verify] Failed to seed registry/grants: {e}", file=sys.stderr)
        return 1

    # Mock Execution: Global skill (run_id required for default-deny)
    ctx_global = ExecutionContext(
        mission_id="verify-mission-global",
        work_item_id=1,
        permit_id=None,
        worker="verify-worker",
        channel_id=None,
        timeout_seconds=10.0,
        allowed_tools=["NOOP_GLOBAL"],
        run_id=run_id_verify,
    )
    actions_global = [{"tool": "noop_global", "params": {}}]
    try:
        results_global: List[ActionResult] = await run_actions(
            actions_global,
            ctx_global,
            stop_on_first_failure=True,
            use_intelligence_engine=True,
        )
    except Exception as e:
        print(f"[verify] Global skill run_actions failed: {e}", file=sys.stderr)
        return 1

    if not results_global or results_global[0].outcome != "SUCCESS":
        print(
            f"[verify] Global skill outcome unexpected: {results_global[0].outcome if results_global else 'NO_RESULT'}",
            file=sys.stderr,
        )
        return 1

    # Mock Execution: Restricted skill with approval (run_id required for default-deny)
    ctx_restricted = ExecutionContext(
        mission_id="verify-mission-restricted",
        work_item_id=2,
        permit_id="permit-verify",
        worker="verify-worker",
        channel_id=None,
        timeout_seconds=10.0,
        allowed_tools=["NOOP_RESTRICTED"],
        run_id=run_id_verify,
    )
    actions_restricted = [{"tool": "noop_restricted", "params": {}}]
    try:
        results_restricted: List[ActionResult] = await run_actions(
            actions_restricted,
            ctx_restricted,
            approval_provider=AutoApprovalProvider(),
            stop_on_first_failure=True,
            use_intelligence_engine=True,
        )
    except Exception as e:
        print(f"[verify] Restricted skill run_actions failed: {e}", file=sys.stderr)
        return 1

    if not results_restricted or results_restricted[0].outcome != "SUCCESS":
        print(
            f"[verify] Restricted skill outcome unexpected: {results_restricted[0].outcome if results_restricted else 'NO_RESULT'}",
            file=sys.stderr,
        )
        return 1

    print("[verify] Execution Layer verification succeeded (global + restricted).")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_run_verification())
    except Exception as e:
        print(f"[verify] Unexpected top-level error: {e}", file=sys.stderr)
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()

