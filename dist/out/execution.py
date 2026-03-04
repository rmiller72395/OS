# execution.py — RMFramework Execution Layer (v5.0)
#
# Fail-closed tool execution: registry, timeout, audit callback.
# Refactored: BaseSkill interface, two-tier (Global/Restricted), Gatekeeper for approval.
# Hardened: limits, URL/param validation, private-IP block, result redaction.
# Windows-native; no Linux-only primitives.
# See EXECUTION_LAYER_SPEC.md, EXECUTION_LAYER_REFACTOR_PLAN.md, TECHNICAL_MANIFESTO.md.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from execution_models import ActionResult, ExecutionContext
from skills import AccessLevel, BaseSkill, get_skill, list_skills as skill_list, register_skill, run_via_gatekeeper
from skills.exceptions import ValidationError
from skills.execution_manager import ExecutionManager

# Intelligence Engine: preflight, resilience, telemetry. Used by run_actions when enabled.
_intelligence_engine: Optional[ExecutionManager] = None


def get_execution_manager() -> ExecutionManager:
    """Return the shared ExecutionManager (Intelligence Engine). Lazy-initialized."""
    global _intelligence_engine
    if _intelligence_engine is None:
        _intelligence_engine = ExecutionManager()
    return _intelligence_engine

# Re-export for consumers (bot.py)
__all__ = [
    "ACTION_JSON_MARKER",
    "ActionResult",
    "ExecutionContext",
    "MAX_ACTIONS_PER_RUN",
    "MAX_ACTION_JSON_BYTES",
    "ToolDef",
    "get_execution_manager",
    "get_tool",
    "list_tools",
    "parse_action_json",
    "register_tool",
    "run_actions",
]

# ---------------------------------------------------------------------------
# Hardening constants (fail-closed)
# ---------------------------------------------------------------------------

MAX_ACTIONS_PER_RUN = 20
MAX_ACTION_JSON_BYTES = 100_000
HTTP_ALLOWED_METHODS: Set[str] = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
HTTP_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB
HTTP_URL_MAX_LENGTH = 2048
RUN_SCRIPT_MAX_ARGS = 32
RUN_SCRIPT_MAX_ARG_LENGTH = 1024
RUN_SCRIPT_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_.\-/\\]+$")  # no spaces, no shell metachars
SECRET_KEYS_REDACT = frozenset({"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"})

# Optional aiohttp for http_request tool
try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False


@dataclass
class ToolDef:
    name: str
    description: str
    handler: Callable[..., Any]  # async (params: dict, context: ExecutionContext) -> ActionResult
    requires_permit: bool = True


# ---------------------------------------------------------------------------
# Action request format: extract ACTION_JSON from worker result text
# ---------------------------------------------------------------------------

ACTION_JSON_MARKER = "ACTION_JSON"


def parse_action_json(result_text: str) -> Optional[Dict[str, Any]]:
    """Extract first ACTION_JSON block after marker. Returns None if missing or invalid (fail-closed)."""
    if not result_text or not isinstance(result_text, str):
        return None
    marker = ACTION_JSON_MARKER
    idx = result_text.find(marker)
    if idx < 0:
        return None
    rest = result_text[idx + len(marker):].strip()
    start = rest.find("{")
    if start < 0:
        return None
    rest = rest[start:]
    if len(rest) > MAX_ACTION_JSON_BYTES:
        return None
    depth = 0
    end = -1
    for i, c in enumerate(rest):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end <= 0:
        return None
    try:
        obj = json.loads(rest[:end])
        if not isinstance(obj, dict) or "actions" not in obj or not isinstance(obj["actions"], list):
            return None
        actions = obj["actions"]
        if len(actions) > MAX_ACTIONS_PER_RUN:
            return None
        return obj
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Redaction and validation helpers
# ---------------------------------------------------------------------------

def _redact_summary(text: str, max_len: int = 500) -> str:
    """Truncate and redact common secret substrings (case-insensitive)."""
    if not text:
        return ""
    out = text[:max_len]
    for key in SECRET_KEYS_REDACT:
        out = re.sub(re.escape(key) + r"\s*[:=]\s*\S+", key + "=[REDACTED]", out, flags=re.I)
        out = re.sub(re.escape(key) + r"\s*[:=]\s*[\"'][^\"']*[\"']", key + "=[REDACTED]", out, flags=re.I)
    return out


def _is_http_url_blocked(url: str) -> Tuple[bool, str]:
    """
    Returns (blocked, reason). Block file://, localhost, 127.0.0.1, ::1, private IPs.
    Allow only http/https. Windows-native (no Unix-specific checks).
    """
    if not url or len(url) > HTTP_URL_MAX_LENGTH:
        return True, "url missing or too long"
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return True, "only http/https allowed"
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        host = (p.hostname or "").strip().lower()
        if not host:
            return True, "invalid host"
        if host in ("localhost", "localhost.", "::1"):
            return True, "localhost not allowed"
        if host == "127.0.0.1":
            return True, "127.0.0.1 not allowed"
        parts = host.split(".")
        if len(parts) == 4 and parts[0].isdigit():
            a, b, c, d = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            if (a == 10) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
                return True, "private IP not allowed"
        if host.startswith("[") and "]" in host:
            return True, "IPv6 not allowed (SSRF risk)"
    except Exception:
        return True, "url parse error"
    return False, ""


# ---------------------------------------------------------------------------
# Tool registry: backward-compatible ToolDef adapter over skill registry
# ---------------------------------------------------------------------------


class _ToolDefSkillAdapter(BaseSkill):
    """Adapts a legacy ToolDef to the BaseSkill interface (GLOBAL, no schema validation)."""

    def __init__(self, tool_def: ToolDef) -> None:
        super().__init__(
            name=tool_def.name,
            description=tool_def.description,
            version="1.0.0",
            access_level=AccessLevel.RESTRICTED if tool_def.requires_permit else AccessLevel.GLOBAL,
        )
        self._def = tool_def

    def validate(self, params: Dict[str, Any]) -> None:
        super().validate(params)

    async def _execute_impl(self, params: Dict[str, Any], context: ExecutionContext) -> ActionResult:
        return await self._def.handler(params, context)


def register_tool(tool: ToolDef) -> None:
    """Register a legacy ToolDef as a skill (adapter) for backward compatibility."""
    register_skill(_ToolDefSkillAdapter(tool))


def get_tool(name: str) -> Optional[ToolDef]:
    """Look up by name; return a ToolDef view (handler = skill.execute) for backward compatibility."""
    skill = get_skill(name)
    if not skill:
        return None
    requires_permit = skill.access_level == AccessLevel.RESTRICTED
    return ToolDef(
        name=skill.name,
        description=skill.description,
        handler=skill.execute,
        requires_permit=requires_permit,
    )


def list_tools() -> List[Dict[str, Any]]:
    """List all registered skills as tool metadata (name, description, requires_permit)."""
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "requires_permit": s["access_level"] == AccessLevel.RESTRICTED.value,
        }
        for s in skill_list()
    ]


# ---------------------------------------------------------------------------
# Built-in skills: http_request (GLOBAL), run_script (RESTRICTED)
# ---------------------------------------------------------------------------

class HttpRequestSkill(BaseSkill):
    """HTTP request (GET/POST etc.). Hardened: scheme, method, URL, body size. GLOBAL for speed."""

    def __init__(self) -> None:
        super().__init__(
            name="http_request",
            description="HTTP request (GET/POST); params: method, url, headers?, body?",
            version="1.0.0",
            access_level=AccessLevel.GLOBAL,
            idempotent=False,
        )

    def validate(self, params: Dict[str, Any]) -> None:
        super().validate(params)
        if not params.get("url") or not isinstance(params.get("url"), str):
            raise ValidationError("missing or invalid url")
        method = (params.get("method") or "GET").upper()
        if method not in HTTP_ALLOWED_METHODS:
            raise ValidationError(f"method not allowed; use one of {sorted(HTTP_ALLOWED_METHODS)}")

    async def _execute_impl(self, params: Dict[str, Any], context: ExecutionContext) -> ActionResult:
        method = (params.get("method") or "GET").upper()
        url = params.get("url") or ""
        blocked, reason = _is_http_url_blocked(url)
        if blocked:
            return ActionResult("FAIL", reason, None)
        headers = params.get("headers")
        if not isinstance(headers, dict):
            headers = {}
        if len(headers) > 50:
            return ActionResult("FAIL", "too many headers", None)
        body = params.get("body")
        body_bytes: Optional[bytes] = None
        if body is not None:
            if isinstance(body, (dict, list)):
                try:
                    body_bytes = json.dumps(body, default=str).encode("utf-8")
                except Exception:
                    return ActionResult("FAIL", "body serialization failed", None)
            elif isinstance(body, str):
                body_bytes = body.encode("utf-8")
            elif isinstance(body, bytes):
                body_bytes = body
            else:
                return ActionResult("FAIL", "body must be dict, list, str, or bytes", None)
            if len(body_bytes) > HTTP_MAX_BODY_BYTES:
                return ActionResult("FAIL", f"body exceeds {HTTP_MAX_BODY_BYTES} bytes", None)
        timeout_s = min(30.0, context.timeout_seconds)
        if not _HAS_AIOHTTP:
            return ActionResult("FAIL", "aiohttp not installed; cannot run http_request", None)

        async def _do() -> ActionResult:
            async with aiohttp.ClientSession() as session:
                kwargs = {"method": method, "url": url, "headers": headers, "timeout": aiohttp.ClientTimeout(total=timeout_s)}
                if body_bytes is not None:
                    kwargs["data"] = body_bytes
                async with session.request(**kwargs) as resp:
                    text = await resp.text()
                    preview = _redact_summary(text[:500], 500)
                    return ActionResult(
                        "SUCCESS",
                        f"status={resp.status} len={len(text)}",
                        {"status": resp.status, "body_preview": preview},
                    )
        try:
            return await asyncio.wait_for(_do(), timeout=timeout_s + 5.0)
        except asyncio.TimeoutError:
            return ActionResult("TIMEOUT", f"request timed out after {timeout_s}s", None)
        except Exception as e:
            return ActionResult("FAIL", _redact_summary(str(e)[:500], 500), None)


class RunScriptSkill(BaseSkill):
    """Run script from allowlisted dir. RESTRICTED (state-changing). Hardened: path, args, realpath."""

    def __init__(self) -> None:
        super().__init__(
            name="run_script",
            description="Run script from allowlisted dir; params: script_path, args?, timeout_seconds?; set RMFRAMEWORK_SCRIPT_ALLOWLIST",
            version="1.0.0",
            access_level=AccessLevel.RESTRICTED,
            idempotent=False,
        )

    def validate(self, params: Dict[str, Any]) -> None:
        super().validate(params)
        script_path = params.get("script_path")
        if not script_path or not isinstance(script_path, str):
            raise ValidationError("missing or invalid script_path")
        if ".." in script_path:
            raise ValidationError(".. not allowed in script_path")
        normalized = script_path.strip().replace("\\", "/").lstrip("/")
        if not RUN_SCRIPT_SAFE_PATH_RE.match(normalized):
            raise ValidationError("script_path contains disallowed characters")

    async def _execute_impl(self, params: Dict[str, Any], context: ExecutionContext) -> ActionResult:
        script_path = params.get("script_path") or ""
        script_path = script_path.strip().replace("\\", "/").lstrip("/")
        while ".." in script_path:
            script_path = script_path.replace("..", "")
        script_path = script_path.strip("/")
        if not script_path:
            return ActionResult("FAIL", "script_path empty after sanitization", None)
        if not RUN_SCRIPT_SAFE_PATH_RE.match(script_path):
            return ActionResult("FAIL", "script_path contains disallowed characters", None)
        allowlist_raw = os.getenv("RMFRAMEWORK_SCRIPT_ALLOWLIST", "")
        allowlist = [p.strip() for p in allowlist_raw.split(",") if p.strip()]
        if not allowlist:
            return ActionResult("FAIL", "RMFRAMEWORK_SCRIPT_ALLOWLIST not set; execution denied", None)
        resolved = None
        for base in allowlist:
            base_abs = os.path.abspath(base)
            try:
                base_real = os.path.realpath(base_abs)
            except OSError:
                base_real = base_abs
            candidate_abs = os.path.abspath(os.path.join(base_abs, script_path))
            try:
                candidate_real = os.path.realpath(candidate_abs)
            except OSError:
                continue
            try:
                if os.path.commonpath([base_real, candidate_real]) != base_real:
                    continue
            except ValueError:
                continue
            if os.path.isfile(candidate_real):
                resolved = candidate_real
                break
        if not resolved:
            return ActionResult("FAIL", "script_path not under allowlisted dir or not a file", None)
        args = params.get("args")
        if not isinstance(args, list):
            args = []
        if len(args) > RUN_SCRIPT_MAX_ARGS:
            return ActionResult("FAIL", f"too many args (max {RUN_SCRIPT_MAX_ARGS})", None)
        str_args = []
        for a in args:
            s = str(a)
            if len(s) > RUN_SCRIPT_MAX_ARG_LENGTH:
                return ActionResult("FAIL", f"arg length exceeds {RUN_SCRIPT_MAX_ARG_LENGTH}", None)
            str_args.append(s)
        timeout_s = min(120.0, float(params.get("timeout_seconds", 60) or 60))
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", resolved, *str_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.path.dirname(resolved),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                return ActionResult("TIMEOUT", f"script timed out after {timeout_s}s", None)
            out = (stdout or b"").decode("utf-8", errors="replace")
            err = (stderr or b"").decode("utf-8", errors="replace")
            out_preview = _redact_summary(out[:500], 500)
            err_preview = _redact_summary(err[:300], 300)
            return ActionResult(
                "SUCCESS" if proc.returncode == 0 else "FAIL",
                f"returncode={proc.returncode} stdout_len={len(out)}",
                {"returncode": proc.returncode, "stdout_preview": out_preview, "stderr_preview": err_preview},
            )
        except Exception as e:
            return ActionResult("FAIL", _redact_summary(str(e)[:500], 500), None)


def _params_hash(params: Dict[str, Any]) -> str:
    """Stable hash for audit; avoid logging full secrets."""
    try:
        return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Runner: run_actions with timeout and audit callback
# ---------------------------------------------------------------------------

async def run_actions(
    actions: List[Dict[str, Any]],
    context: ExecutionContext,
    *,
    log_action: Optional[Callable[..., Any]] = None,
    log_approval: Optional[Callable[..., Any]] = None,
    approval_provider: Optional[Any] = None,
    stop_on_first_failure: bool = True,
    use_intelligence_engine: bool = True,
) -> List[ActionResult]:
    """Execute a list of actions. By default uses the Intelligence Engine (preflight, resilience, telemetry).
    Set use_intelligence_engine=False for legacy path (gatekeeper only).
    GLOBAL skills run immediately; RESTRICTED require approval_provider. Each run is traceable (chain_id, trace_id).
    Fail-closed: unknown tool or invalid params → skip and log SKIP_*.
    """
    if use_intelligence_engine:
        manager = get_execution_manager()
        return await manager.run_actions(
            actions,
            context,
            log_action=log_action,
            log_approval=log_approval,
            approval_provider=approval_provider,
            stop_on_first_failure=stop_on_first_failure,
        )
    return await _run_actions_legacy(
        actions,
        context,
        log_action=log_action,
        log_approval=log_approval,
        approval_provider=approval_provider,
        stop_on_first_failure=stop_on_first_failure,
    )


async def _run_actions_legacy(
    actions: List[Dict[str, Any]],
    context: ExecutionContext,
    *,
    log_action: Optional[Callable[..., Any]] = None,
    log_approval: Optional[Callable[..., Any]] = None,
    approval_provider: Optional[Any] = None,
    stop_on_first_failure: bool = True,
) -> List[ActionResult]:
    """Legacy path: gatekeeper only, no preflight/resilience/telemetry."""
    results: List[ActionResult] = []
    for action in actions:
        if not isinstance(action, dict):
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, "?", "?", "SKIP_UNKNOWN_TOOL", "invalid action item")
            results.append(ActionResult("SKIP_UNKNOWN_TOOL", "invalid action item", None))
            if stop_on_first_failure:
                break
            continue
        tool_name = (action.get("tool") or "").strip().upper()
        params = action.get("params")
        if not isinstance(params, dict):
            params = {}
        if not tool_name:
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, "?", _params_hash(params), "SKIP_UNKNOWN_TOOL", "missing tool name")
            results.append(ActionResult("SKIP_UNKNOWN_TOOL", "missing tool name", None))
            if stop_on_first_failure:
                break
            continue
        skill = get_skill(tool_name)
        if not skill:
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_UNKNOWN_TOOL", "tool not registered")
            results.append(ActionResult("SKIP_UNKNOWN_TOOL", "tool not registered", None))
            if stop_on_first_failure:
                break
            continue
        requires_permit = skill.access_level == AccessLevel.RESTRICTED
        if requires_permit and not (context.permit_id and str(context.permit_id).strip()):
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_NO_PERMIT", "permit required but missing")
            results.append(ActionResult("SKIP_NO_PERMIT", "permit required but missing", None))
            if stop_on_first_failure:
                break
            continue
        if context.allowed_tools is not None and tool_name not in context.allowed_tools:
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_UNKNOWN_TOOL", "tool not in allowlist")
            results.append(ActionResult("SKIP_UNKNOWN_TOOL", "tool not in allowlist", None))
            if stop_on_first_failure:
                break
            continue
        # v5.0 default-deny: require ticket/run and authorize_tool_call (no bypass)
        ticket_id = getattr(context, "ticket_id", None)
        run_id = getattr(context, "run_id", None)
        if not ticket_id and not run_id:
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", "no ticket/run context; grant required")
            results.append(ActionResult("SKIP_TOOL_DENIED", "no ticket/run context; grant required", None))
            if stop_on_first_failure:
                break
            continue
        try:
            from datetime import datetime, timezone
            from skills.tool_registry import get_tool_registry
            from skills.tool_grants import get_tool_grant_store, authorize_tool_call
            from skills.tool_costing import compute_tool_cost
            registry = get_tool_registry()
            grants = get_tool_grant_store()
            registry.ensure_schema()
            grants.ensure_schema()
            tool_def = registry.get_tool(tool_name)
            if not tool_def or not tool_def.enabled:
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", "tool not in registry or disabled")
                results.append(ActionResult("SKIP_TOOL_DENIED", "tool not in registry or disabled", None))
                if stop_on_first_failure:
                    break
                continue
            requested_scopes = action.get("scopes") if isinstance(action.get("scopes"), list) else (tool_def.scopes or [])
            proposed_cost = compute_tool_cost(tool_def.cost_model_json, 0.0, 1)
            now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            auth_result = authorize_tool_call(
                registry, grants, tool_name, requested_scopes,
                ticket_id, run_id, params, proposed_cost, now_utc,
            )
            if not auth_result.allowed:
                reason = auth_result.reason + ((": " + "; ".join(auth_result.constraint_violations or [])) if auth_result.constraint_violations else "")
                if log_action:
                    await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", reason)
                results.append(ActionResult("SKIP_TOOL_DENIED", reason, {"matched_grant_id": auth_result.matched_grant_id}))
                if stop_on_first_failure:
                    break
                continue
        except Exception as e:
            if log_action:
                await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), "SKIP_TOOL_DENIED", str(e))
            results.append(ActionResult("SKIP_TOOL_DENIED", f"authorization error: {e}", None))
            if stop_on_first_failure:
                break
            continue
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
        )
        try:
            result = await asyncio.wait_for(
                run_via_gatekeeper(
                    skill,
                    params,
                    ctx,
                    params_hash=_params_hash(params),
                    approval_provider=approval_provider,
                    log_action=None,
                    log_approval=log_approval,
                ),
                timeout=context.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = ActionResult("TIMEOUT", f"tool timed out after {context.timeout_seconds}s", None)
        except Exception as e:
            logging.exception("Execution tool error")
            result = ActionResult("FAIL", str(e)[:500], None)
        if not isinstance(result, ActionResult):
            result = ActionResult("FAIL", "handler did not return ActionResult", None)
        results.append(result)
        if log_action:
            await _safe_log(log_action, context.mission_id, context.work_item_id, context.permit_id, tool_name, _params_hash(params), result.outcome, _redact_summary(result.result_summary))
        if stop_on_first_failure and result.outcome not in ("SUCCESS",):
            break
    return results


async def _safe_log(log_action: Callable[..., Any], mission_id: str, work_item_id: int, permit_id: Optional[str], tool: str, params_hash: str, outcome: str, result_summary: str) -> None:
    try:
        if asyncio.iscoroutinefunction(log_action):
            await log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary)
        else:
            log_action(mission_id, work_item_id, permit_id, tool, params_hash, outcome, result_summary)
    except Exception as e:
        logging.error(f"action_log callback failed: {e}")


# ---------------------------------------------------------------------------
# Register built-in skills on import
# ---------------------------------------------------------------------------

register_skill(HttpRequestSkill())
register_skill(RunScriptSkill())
