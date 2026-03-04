# skills/tool_registry.py — Read-only registry interface + DB for tool_registry (v5.0)
#
# Tools are default-deny; only registered and enabled tools can be granted.
# See Tool Registry + Scoped Tool Grants spec.

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from skills.ops_db import get_ops_db_path

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    tool_name: str
    description: str
    input_schema_json: Dict[str, Any]
    output_schema_json: Optional[Dict[str, Any]]
    scopes: List[str]
    side_effect: bool
    idempotency_required: bool
    cost_model_json: Dict[str, Any]
    default_timeout_s: int
    max_timeout_s: int
    rate_limit_json: Dict[str, Any]
    allowlist_json: Dict[str, Any]
    enabled: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_registry (
    tool_name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    input_schema_json TEXT NOT NULL DEFAULT '{}',
    output_schema_json TEXT,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    side_effect INTEGER NOT NULL DEFAULT 0,
    idempotency_required INTEGER NOT NULL DEFAULT 0,
    cost_model_json TEXT NOT NULL DEFAULT '{}',
    default_timeout_s INTEGER NOT NULL DEFAULT 60,
    max_timeout_s INTEGER NOT NULL DEFAULT 300,
    rate_limit_json TEXT NOT NULL DEFAULT '{}',
    allowlist_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_registry_enabled ON tool_registry(enabled);
CREATE INDEX IF NOT EXISTS idx_tool_registry_created ON tool_registry(created_at);
"""


def _row_to_tool(row: sqlite3.Row) -> ToolDef:
    def j(s, default):
        if s is None:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    return ToolDef(
        tool_name=row["tool_name"],
        description=row["description"] or "",
        input_schema_json=j(row["input_schema_json"], {}),
        output_schema_json=j(row["output_schema_json"], None) if row["output_schema_json"] else None,
        scopes=j(row["scopes_json"], []),
        side_effect=bool(row["side_effect"]),
        idempotency_required=bool(row["idempotency_required"]),
        cost_model_json=j(row["cost_model_json"], {}),
        default_timeout_s=int(row["default_timeout_s"]) if row["default_timeout_s"] is not None else 60,
        max_timeout_s=int(row["max_timeout_s"]) if row["max_timeout_s"] is not None else 300,
        rate_limit_json=j(row["rate_limit_json"], {}),
        allowlist_json=j(row["allowlist_json"], {}),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


class ToolRegistry:
    """Read-only registry interface + DB init/migrations for tool_registry."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._path = db_path or get_ops_db_path()
        self._init_done = False

    def _get_conn(self) -> sqlite3.Connection:
        import pathlib
        pathlib.Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def ensure_schema(self) -> None:
        try:
            conn = self._get_conn()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
                self._init_done = True
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolRegistry ensure_schema failed: %s", e)
            raise

    def upsert_tool(self, tool: ToolDef) -> None:
        """Insert or replace tool (owner/admin only via command; OK if only used internally for now)."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_registry
                    (tool_name, description, input_schema_json, output_schema_json, scopes_json,
                     side_effect, idempotency_required, cost_model_json, default_timeout_s, max_timeout_s,
                     rate_limit_json, allowlist_json, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tool.tool_name,
                        tool.description,
                        json.dumps(tool.input_schema_json),
                        json.dumps(tool.output_schema_json) if tool.output_schema_json else None,
                        json.dumps(tool.scopes),
                        1 if tool.side_effect else 0,
                        1 if tool.idempotency_required else 0,
                        json.dumps(tool.cost_model_json),
                        tool.default_timeout_s,
                        tool.max_timeout_s,
                        json.dumps(tool.rate_limit_json),
                        json.dumps(tool.allowlist_json),
                        1 if tool.enabled else 0,
                        tool.created_at or now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolRegistry upsert_tool failed: %s", e)
            raise

    def get_tool(self, tool_name: str) -> Optional[ToolDef]:
        """Return tool by name or None."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                cur = conn.execute(
                    "SELECT * FROM tool_registry WHERE tool_name = ?",
                    (tool_name.strip(),),
                )
                row = cur.fetchone()
                return _row_to_tool(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolRegistry get_tool failed: %s", e)
            return None

    def list_tools(self, enabled_only: bool = False) -> List[ToolDef]:
        """List all tools; optionally only enabled."""
        try:
            conn = self._get_conn()
            try:
                self.ensure_schema()
                if enabled_only:
                    cur = conn.execute(
                        "SELECT * FROM tool_registry WHERE enabled = 1 ORDER BY tool_name",
                    )
                else:
                    cur = conn.execute("SELECT * FROM tool_registry ORDER BY tool_name")
                return [_row_to_tool(r) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.warning("ToolRegistry list_tools failed: %s", e)
            return []


# ---------------------------------------------------------------------------
# Bootstrap: register built-in starter tools (idempotent upsert)
# ---------------------------------------------------------------------------

STARTER_READONLY_TOOL_NAMES = [
    "time_now",
    "uuid_new",
    "json_validate",
    "http_get_json_readonly",
    "public_api_catalog_search",
]
STARTER_READONLY_SCOPES = [
    "read:time",
    "read:uuid",
    "read:json",
    "read:http",
    "read:catalog",
]


def bootstrap_builtin_tools(
    registry: ToolRegistry,
    config: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Register built-in tool definitions into tool_registry DB. Idempotent (upsert by tool_name).
    Does NOT auto-enable risky or side-effect tools.
    config: optional dict with public_api_allowlist_domains, http_* keys (from sovereign_config or env).
    Returns count of tools upserted.
    """
    config = config or {}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    count = 0

    def upsert(
        tool_name: str,
        description: str,
        scopes: List[str],
        side_effect: bool,
        enabled: bool,
        default_timeout_s: int = 20,
        max_timeout_s: int = 60,
        rate_limit_json: Optional[Dict[str, Any]] = None,
        allowlist_json: Optional[Dict[str, Any]] = None,
        cost_usd_per_call: float = 0.0,
    ) -> None:
        nonlocal count
        allowlist = allowlist_json or {}
        if tool_name == "http_get_json_readonly":
            domains = config.get("public_api_allowlist_domains") or []
            if isinstance(domains, list):
                allowlist = {"domains": list(domains), "url_prefixes": config.get("public_api_allowlist_url_prefixes") or []}
        t = ToolDef(
            tool_name=tool_name,
            description=description,
            input_schema_json={},
            output_schema_json=None,
            scopes=scopes,
            side_effect=side_effect,
            idempotency_required=not side_effect,
            cost_model_json={"usd_per_call": cost_usd_per_call},
            default_timeout_s=default_timeout_s,
            max_timeout_s=max_timeout_s,
            rate_limit_json=rate_limit_json or {"calls_per_minute": 30},
            allowlist_json=allowlist,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        registry.upsert_tool(t)
        count += 1

    # ---- Starter tools (enabled=True) ----
    upsert(
        "time_now",
        "Returns UTC time and local time (America/New_York). Params: none.",
        ["read:time"],
        side_effect=False,
        enabled=True,
        default_timeout_s=5,
        max_timeout_s=10,
    )
    upsert(
        "uuid_new",
        "Generate a UUID. Params: none.",
        ["read:uuid"],
        side_effect=False,
        enabled=True,
        default_timeout_s=5,
        max_timeout_s=10,
    )
    upsert(
        "json_validate",
        "Validate JSON string to dict; optional JSON schema. Params: json_str, schema (optional).",
        ["read:json"],
        side_effect=False,
        enabled=True,
        default_timeout_s=10,
        max_timeout_s=30,
    )
    upsert(
        "http_get_json_readonly",
        "HTTP GET to fetch JSON from allowlisted domains only. Params: url. Enforces allowlist, max bytes, Content-Type JSON.",
        ["read:http"],
        side_effect=False,
        enabled=True,
        default_timeout_s=config.get("http_default_timeout_s", 20) or 20,
        max_timeout_s=min(60, int(config.get("http_max_timeout_s", 60) or 60)),
        rate_limit_json={"calls_per_minute": 30},
        cost_usd_per_call=0.0,
    )
    upsert(
        "public_api_catalog_search",
        "Search local catalog of known public APIs (no network). Params: query (optional). Returns name, description, base_url, docs_url, auth, category.",
        ["read:catalog"],
        side_effect=False,
        enabled=True,
        default_timeout_s=5,
        max_timeout_s=15,
    )

    # ---- Optional / disabled by default ----
    upsert(
        "http_get_text_readonly",
        "HTTP GET to fetch text/HTML from allowlisted domains. Params: url. enabled=false by default.",
        ["read:http"],
        side_effect=False,
        enabled=False,
        default_timeout_s=20,
        max_timeout_s=60,
    )

    # ---- Side-effect tools: must exist but disabled ----
    for name, desc in [
        ("run_script", "Run script from allowlisted dir. RESTRICTED."),
        ("http_request", "HTTP request (GET/POST). General-purpose; use http_get_json_readonly for read-only."),
    ]:
        t = ToolDef(
            tool_name=name,
            description=desc,
            input_schema_json={},
            output_schema_json=None,
            scopes=["write:script", "write:http"] if "script" in name else ["read:http", "write:http"],
            side_effect=True,
            idempotency_required=False,
            cost_model_json={"usd_per_call": 0.0},
            default_timeout_s=60,
            max_timeout_s=300,
            rate_limit_json={"calls_per_minute": 10},
            allowlist_json={},
            enabled=False,
            created_at=now,
            updated_at=now,
        )
        registry.upsert_tool(t)
        count += 1

    logger.info("bootstrap_builtin_tools: upserted %d tools", count)
    return count


# Singleton for app use
_registry: Optional[ToolRegistry] = None


def get_tool_registry(db_path: Optional[str] = None) -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry(db_path)
    return _registry
