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


# Singleton for app use
_registry: Optional[ToolRegistry] = None


def get_tool_registry(db_path: Optional[str] = None) -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry(db_path)
    return _registry
