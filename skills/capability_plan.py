# skills/capability_plan.py — Capability plan schema, validation, storage (v5.0)
#
# CEO produces CapabilityPlan (tools + scopes + cost estimates) before execution.
# If plan requests NEW tools/scopes not allowed => BLOCK ticket and request approval.

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from skills.ops_db import get_ops_db_path

logger = logging.getLogger(__name__)


@dataclass
class ToolRequest:
    tool_name: str
    reason: str
    scopes_needed: List[str]
    side_effect: bool
    idempotency_plan: str
    allowlist_constraints: Dict[str, Any]
    estimated_usage: Dict[str, Any]
    estimated_cost_usd: float
    fallback_tool: Optional[str] = None


@dataclass
class CapabilityPlan:
    ticket_id: str
    objective: str
    success_criteria: List[str]
    steps: List[Dict[str, Any]]
    required_tools: List[ToolRequest]
    budget: Dict[str, Any]
    risks: List[str]
    notes: Optional[str] = None


_PLANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_plans (
    ticket_id TEXT PRIMARY KEY,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _canonical_json(obj: Any) -> str:
    """Stable canonical JSON for hashing."""
    return json.dumps(obj, sort_keys=True, default=str)


def plan_hash(plan: Dict[str, Any]) -> str:
    """SHA256 hex of canonical JSON of plan (deterministic)."""
    return hashlib.sha256(_canonical_json(plan).encode()).hexdigest()


def validate_capability_plan(plan: Any) -> tuple[bool, List[str]]:
    """
    Validate plan structure. Returns (ok, list of error messages).
    Schema: ticket_id, objective, success_criteria[], steps[], required_tools[], budget{}, risks[], notes?
    """
    errors: List[str] = []
    if not isinstance(plan, dict):
        return False, ["plan must be a dict"]
    if not plan.get("ticket_id"):
        errors.append("missing ticket_id")
    if "objective" not in plan:
        errors.append("missing objective")
    if "success_criteria" not in plan:
        errors.append("missing success_criteria")
    elif not isinstance(plan["success_criteria"], list):
        errors.append("success_criteria must be list")
    if "steps" not in plan:
        errors.append("missing steps")
    elif not isinstance(plan["steps"], list):
        errors.append("steps must be list")
    if "required_tools" not in plan:
        errors.append("missing required_tools")
    elif not isinstance(plan["required_tools"], list):
        errors.append("required_tools must be list")
    else:
        for i, tr in enumerate(plan["required_tools"]):
            if not isinstance(tr, dict):
                errors.append(f"required_tools[{i}] must be object")
            else:
                if "tool_name" not in tr:
                    errors.append(f"required_tools[{i}].tool_name missing")
                if "scopes_needed" in tr and not isinstance(tr["scopes_needed"], list):
                    errors.append(f"required_tools[{i}].scopes_needed must be list")
    if "budget" not in plan:
        errors.append("missing budget")
    elif not isinstance(plan["budget"], dict):
        errors.append("budget must be object")
    if "risks" not in plan:
        errors.append("missing risks")
    elif not isinstance(plan["risks"], list):
        errors.append("risks must be list")
    return len(errors) == 0, errors


class CapabilityPlanStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._path = db_path or get_ops_db_path()
        self._schema_done = False

    def _get_conn(self) -> sqlite3.Connection:
        import pathlib
        pathlib.Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def ensure_schema(self) -> None:
        try:
            conn = self._get_conn()
            try:
                conn.executescript(_PLANS_SCHEMA)
                conn.commit()
                self._schema_done = True
            finally:
                conn.close()
        except Exception as e:
            logger.warning("CapabilityPlanStore ensure_schema failed: %s", e)
            raise

    def upsert_plan(self, ticket_id: str, plan_json: Dict[str, Any], plan_hash_val: str, created_by: str) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO capability_plans (ticket_id, plan_json, plan_hash, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (ticket_id, json.dumps(plan_json), plan_hash_val, created_by, now, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("CapabilityPlanStore upsert_plan failed: %s", e)
            raise

    def get_plan(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                cur = conn.execute("SELECT plan_json FROM capability_plans WHERE ticket_id = ?", (ticket_id,))
                row = cur.fetchone()
                if row:
                    return json.loads(row[0])
                return None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("CapabilityPlanStore get_plan failed: %s", e)
            return None

    def get_plan_hash(self, ticket_id: str) -> Optional[str]:
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                cur = conn.execute("SELECT plan_hash FROM capability_plans WHERE ticket_id = ?", (ticket_id,))
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("CapabilityPlanStore get_plan_hash failed: %s", e)
            return None


_plan_store: Optional[CapabilityPlanStore] = None


def get_capability_plan_store(db_path: Optional[str] = None) -> CapabilityPlanStore:
    global _plan_store
    if _plan_store is None:
        _plan_store = CapabilityPlanStore(db_path)
    return _plan_store


def plan_requests_new_tools_or_scopes(ticket_id: str) -> tuple[bool, str]:
    """
    If capability plan exists and requests tools/scopes not in the current ticket grant,
    return (True, reason) so caller can BLOCK ticket and request Discord approval.
    Release bar: execution must NOT proceed until approved.
    """
    try:
        store = get_capability_plan_store()
        plan = store.get_plan(ticket_id)
        if not plan or not plan.get("required_tools"):
            return False, ""
        from skills.tool_grants import get_tool_grant_store
        grants = get_tool_grant_store()
        grants.ensure_schema()
        grant = grants.get_active_grant(ticket_id=ticket_id)
        if not grant:
            return True, "Capability plan requires tools but no active grant for ticket; approval required."
        allowed_upper = [t.strip().upper() for t in grant.allowed_tools]
        grant_scopes = set(s.strip().lower() for s in grant.allowed_scopes)
        missing: List[str] = []
        for tr in plan["required_tools"]:
            if not isinstance(tr, dict):
                continue
            tool_name = (tr.get("tool_name") or "").strip().upper()
            if not tool_name:
                continue
            if tool_name not in allowed_upper:
                missing.append(tool_name)
            scopes = tr.get("scopes_needed") or []
            for s in scopes:
                scope_lower = s.strip().lower()
                wildcard = "*" in [x.strip().lower() for x in (grant.allowed_scopes or [])]
                if scope_lower not in grant_scopes and not wildcard:
                    missing.append(f"{tool_name}:scope {s!r}")
        if missing:
            return True, "Capability plan requests tools/scopes beyond current grant: " + "; ".join(missing[:5])
        return False, ""
    except Exception as e:
        logger.warning("plan_requests_new_tools_or_scopes: %s", e)
        return True, f"Capability plan check failed: {e}"
