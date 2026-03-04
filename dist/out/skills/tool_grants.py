# skills/tool_grants.py — Grants + authorization enforcement (v5.0)
#
# Default-deny: tools only invokable when registered, enabled, and granted for ticket/run.
# Fail-closed on DB errors.

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from skills.ops_db import get_ops_db_path

logger = logging.getLogger(__name__)


@dataclass
class ToolGrant:
    grant_id: str
    ticket_id: Optional[str]
    run_id: Optional[str]
    allowed_tools: List[str]
    allowed_scopes: List[str]
    constraints_json: Dict[str, Any]
    max_tool_spend_usd: Optional[float]
    max_calls: Optional[int]
    expires_at: Optional[str]
    issued_by: str
    reason: str
    created_at: str
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None


@dataclass
class AuthorizeResult:
    allowed: bool
    reason: str
    matched_grant_id: Optional[str] = None
    constraint_violations: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.constraint_violations is None:
            self.constraint_violations = []


_GRANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_grants (
    grant_id TEXT PRIMARY KEY,
    ticket_id TEXT,
    run_id TEXT,
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    allowed_scopes_json TEXT NOT NULL DEFAULT '[]',
    constraints_json TEXT NOT NULL DEFAULT '{}',
    max_tool_spend_usd REAL,
    max_calls INTEGER,
    expires_at TEXT,
    issued_by TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    revoke_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_grants_ticket_revoked ON tool_grants(ticket_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_tool_grants_run_revoked ON tool_grants(run_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_tool_grants_expires ON tool_grants(expires_at);

CREATE TABLE IF NOT EXISTS tool_grant_usage (
    grant_id TEXT PRIMARY KEY,
    calls_used INTEGER NOT NULL DEFAULT 0,
    spend_used_usd REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (grant_id) REFERENCES tool_grants(grant_id)
);
"""


def _j(s: Optional[str], default: Any) -> Any:
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


class ToolGrantStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._path = db_path or get_ops_db_path()
        self._schema_done = False

    def _get_conn(self) -> sqlite3.Connection:
        import pathlib
        pathlib.Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        try:
            conn = self._get_conn()
            try:
                conn.executescript(_GRANTS_SCHEMA)
                conn.commit()
                self._schema_done = True
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore ensure_schema failed: %s", e)
            raise

    def create_grant(self, grant: ToolGrant) -> str:
        """Create a grant; return grant_id."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                conn.execute(
                    """
                    INSERT INTO tool_grants
                    (grant_id, ticket_id, run_id, allowed_tools_json, allowed_scopes_json,
                     constraints_json, max_tool_spend_usd, max_calls, expires_at, issued_by, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        grant.grant_id,
                        grant.ticket_id,
                        grant.run_id,
                        json.dumps(grant.allowed_tools),
                        json.dumps(grant.allowed_scopes),
                        json.dumps(grant.constraints_json),
                        grant.max_tool_spend_usd,
                        grant.max_calls,
                        grant.expires_at,
                        grant.issued_by,
                        grant.reason,
                        grant.created_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_grant_usage (grant_id, calls_used, spend_used_usd, updated_at)
                    VALUES (?, 0, 0, ?)
                    """,
                    (grant.grant_id, grant.created_at),
                )
                conn.commit()
                return grant.grant_id
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore create_grant failed: %s", e)
            raise

    def get_active_grant(
        self,
        ticket_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[ToolGrant]:
        """Return one active (non-revoked, non-expired) grant for ticket or run. Prefer run_id."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                # Prefer run_id grant first
                if run_id:
                    cur = conn.execute(
                        """
                        SELECT * FROM tool_grants
                        WHERE run_id = ? AND revoked_at IS NULL
                          AND (expires_at IS NULL OR expires_at > ?)
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (run_id, now),
                    )
                    row = cur.fetchone()
                    if row:
                        return _row_to_grant(row)
                if ticket_id:
                    cur = conn.execute(
                        """
                        SELECT * FROM tool_grants
                        WHERE ticket_id = ? AND revoked_at IS NULL
                          AND (expires_at IS NULL OR expires_at > ?)
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (ticket_id, now),
                    )
                    row = cur.fetchone()
                    if row:
                        return _row_to_grant(row)
                return None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore get_active_grant failed: %s", e)
            return None

    def list_grants(
        self,
        ticket_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[ToolGrant]:
        """List grants (optionally filtered); includes revoked."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                if ticket_id and run_id:
                    cur = conn.execute(
                        "SELECT * FROM tool_grants WHERE ticket_id = ? OR run_id = ? ORDER BY created_at DESC",
                        (ticket_id, run_id),
                    )
                elif ticket_id:
                    cur = conn.execute(
                        "SELECT * FROM tool_grants WHERE ticket_id = ? ORDER BY created_at DESC",
                        (ticket_id,),
                    )
                elif run_id:
                    cur = conn.execute(
                        "SELECT * FROM tool_grants WHERE run_id = ? ORDER BY created_at DESC",
                        (run_id,),
                    )
                else:
                    cur = conn.execute("SELECT * FROM tool_grants ORDER BY created_at DESC")
                return [_row_to_grant(r) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore list_grants failed: %s", e)
            return []

    def revoke_grant(self, grant_id: str, reason: str, revoked_by: str) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                conn.execute(
                    "UPDATE tool_grants SET revoked_at = ?, revoked_by = ?, revoke_reason = ? WHERE grant_id = ?",
                    (now, revoked_by, reason, grant_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore revoke_grant failed: %s", e)
            raise

    def record_usage(self, grant_id: str, additional_calls: int = 1, additional_spend_usd: float = 0.0) -> None:
        """Atomically increment usage for a grant. Creates row if missing."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                conn.execute(
                    """
                    INSERT INTO tool_grant_usage (grant_id, calls_used, spend_used_usd, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(grant_id) DO UPDATE SET
                        calls_used = calls_used + ?,
                        spend_used_usd = spend_used_usd + ?,
                        updated_at = ?
                    """,
                    (grant_id, additional_calls, additional_spend_usd, now, additional_calls, additional_spend_usd, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore record_usage failed: %s", e)

    def get_usage(self, grant_id: str) -> Optional[tuple]:
        """Return (calls_used, spend_used_usd) or None."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                cur = conn.execute(
                    "SELECT calls_used, spend_used_usd FROM tool_grant_usage WHERE grant_id = ?",
                    (grant_id,),
                )
                row = cur.fetchone()
                return (row[0], row[1]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolGrantStore get_usage failed: %s", e)
            return None


def _row_to_grant(row: sqlite3.Row) -> ToolGrant:
    return ToolGrant(
        grant_id=row["grant_id"],
        ticket_id=row["ticket_id"],
        run_id=row["run_id"],
        allowed_tools=_j(row["allowed_tools_json"], []),
        allowed_scopes=_j(row["allowed_scopes_json"], []),
        constraints_json=_j(row["constraints_json"], {}),
        max_tool_spend_usd=row["max_tool_spend_usd"],
        max_calls=row["max_calls"],
        expires_at=row["expires_at"],
        issued_by=row["issued_by"] or "",
        reason=row["reason"] or "",
        created_at=row["created_at"] or "",
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
        revoke_reason=row["revoke_reason"],
    )


def _check_allowlist(allowlist_json: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    """Best-effort strict: validate params against allowlist (domains, endpoints, folders). Returns list of violations."""
    violations: List[str] = []
    if not allowlist_json or not params:
        return violations
    # allowlist_json can have keys like "domains", "endpoints", "folders", "allowed_keys"
    allowed_domains = allowlist_json.get("domains") or allowlist_json.get("url_domains")
    if isinstance(allowed_domains, list) and params.get("url"):
        url = str(params.get("url", ""))
        for d in allowed_domains:
            if d in url:
                break
        else:
            if allowed_domains and url:
                violations.append("url not in allowed domains")
    allowed_folders = allowlist_json.get("folders") or allowlist_json.get("paths")
    if isinstance(allowed_folders, list):
        path = params.get("path") or params.get("file_path") or params.get("file")
        if path:
            path_str = str(path)
            if not any(p in path_str or path_str.startswith(p) for p in allowed_folders):
                violations.append("path not in allowed folders")
    return violations


def authorize_tool_call(
    registry: "ToolRegistry",
    grants: ToolGrantStore,
    tool_name: str,
    requested_scopes: List[str],
    ticket_id: Optional[str],
    run_id: Optional[str],
    params: Dict[str, Any],
    proposed_tool_cost_usd: float,
    now_utc: str,
) -> AuthorizeResult:
    """
    Authorization: tool must exist and be enabled; active grant must allow tool and scopes;
    allowlist and spend/calls enforced. Fail-closed on error.
    """
    violations: List[str] = []
    try:
        # CISO policy: env POLICY_DENIED_SCOPES / POLICY_ALLOWED_SCOPES (default-deny)
        try:
            denied = os.environ.get("POLICY_DENIED_SCOPES", "").strip()
            if denied:
                denied_set = set(s.strip().lower() for s in denied.split(",") if s.strip())
                for s in requested_scopes:
                    if s.strip().lower() in denied_set:
                        return AuthorizeResult(False, "scope denied by policy", constraint_violations=[f"{s} in POLICY_DENIED_SCOPES"])
            allowed_policy = os.environ.get("POLICY_ALLOWED_SCOPES", "").strip()
            if allowed_policy:
                allowed_set = set(s.strip().lower() for s in allowed_policy.split(",") if s.strip())
                for s in requested_scopes:
                    if s.strip().lower() not in allowed_set:
                        return AuthorizeResult(False, "scope not in policy allowlist", constraint_violations=[f"{s} not in POLICY_ALLOWED_SCOPES"])
        except Exception as e:
            logger.warning("Policy scope check failed (fail-closed): %s", e)
            return AuthorizeResult(False, "policy check error", constraint_violations=violations)

        tool = registry.get_tool(tool_name)
        if not tool:
            return AuthorizeResult(False, "tool not in registry", constraint_violations=violations)
        if not tool.enabled:
            return AuthorizeResult(False, "tool disabled", constraint_violations=violations)

        grant = grants.get_active_grant(ticket_id=ticket_id, run_id=run_id)
        if not grant:
            return AuthorizeResult(False, "no active grant for ticket/run", constraint_violations=violations)

        allowed_tools_upper = [t.strip().upper() for t in grant.allowed_tools]
        if tool_name.strip().upper() not in allowed_tools_upper:
            return AuthorizeResult(
                False,
                "tool not in grant allowed_tools",
                matched_grant_id=grant.grant_id,
                constraint_violations=violations,
            )

        # Scopes: requested must be subset of grant and subset of tool's scopes
        grant_scopes_set = set(s.strip().lower() for s in grant.allowed_scopes)
        tool_scopes_set = set(s.strip().lower() for s in tool.scopes)
        for s in requested_scopes:
            s_lower = s.strip().lower()
            if s_lower not in grant_scopes_set:
                violations.append(f"scope {s!r} not in grant")
            if s_lower not in tool_scopes_set:
                violations.append(f"scope {s!r} not in tool registry")
        if violations:
            return AuthorizeResult(
                False,
                "scope escalation or not allowed",
                matched_grant_id=grant.grant_id,
                constraint_violations=violations,
            )

        # Allowlist (tool's allowlist_json) vs params
        allowlist_violations = _check_allowlist(tool.allowlist_json, params)
        if allowlist_violations:
            return AuthorizeResult(
                False,
                "allowlist violation",
                matched_grant_id=grant.grant_id,
                constraint_violations=allowlist_violations,
            )

        # Expiry already filtered in get_active_grant
        if grant.max_calls is not None:
            usage = grants.get_usage(grant.grant_id)
            calls_used = (usage[0] or 0) if usage else 0
            if calls_used >= grant.max_calls:
                return AuthorizeResult(
                    False,
                    "max_calls exceeded",
                    matched_grant_id=grant.grant_id,
                    constraint_violations=violations,
                )
        if grant.max_tool_spend_usd is not None:
            usage = grants.get_usage(grant.grant_id)
            spend_used = (usage[1] or 0.0) if usage else 0.0
            if spend_used + proposed_tool_cost_usd > grant.max_tool_spend_usd:
                return AuthorizeResult(
                    False,
                    "max_tool_spend would be exceeded",
                    matched_grant_id=grant.grant_id,
                    constraint_violations=violations,
                )

        return AuthorizeResult(
            True,
            "ok",
            matched_grant_id=grant.grant_id,
            constraint_violations=[],
        )
    except Exception as e:
        logger.warning("authorize_tool_call error (fail-closed): %s", e)
        return AuthorizeResult(False, f"authorization error: {e}", constraint_violations=violations)


# Singleton
_grant_store: Optional[ToolGrantStore] = None


def get_tool_grant_store(db_path: Optional[str] = None) -> ToolGrantStore:
    global _grant_store
    if _grant_store is None:
        _grant_store = ToolGrantStore(db_path)
    return _grant_store
