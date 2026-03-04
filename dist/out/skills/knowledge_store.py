# skills/knowledge_store.py — Global Memory / Knowledge Store (RMFramework v4.10+)
#
# One-shot learning: record task success patterns, failure correlation, and environmental context.
# Consult memory before execution to adjust strategy (timeout, alternative skill). Fail-closed;
# Windows-native; SQLite WAL for consistency with AuditDB/Telemetry.
# See INTELLIGENCE_ENGINE_SPEC.md and GOVERNANCE_AND_VISION.md.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Redact param keys that may contain secrets (do not import execution to avoid circular deps).
# Substring match, case-insensitive, including nested structures.
_SECRET_KEYS_REDACT = frozenset(
    {
        "token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "authorization",
        "bearer",
        "cookie",
        "session",
        "client_secret",
        "private_key",
    }
)


def _default_db_path() -> str:
    """Resolve knowledge DB path (env overrides, else SOVEREIGN_DATA_DIR/data/knowledge_store.db)."""
    env_override = os.environ.get("RMFRAMEWORK_KNOWLEDGE_DB") or os.environ.get(
        "KNOWLEDGE_DB_PATH"
    )
    if env_override:
        return env_override
    base = os.getenv("SOVEREIGN_DATA_DIR", os.getcwd())
    return str(Path(base) / "data" / "knowledge_store.db")


DEFAULT_KNOWLEDGE_DB = _default_db_path()

# Tuning knobs (env-driven, fail-closed defaults)
_MAX_TIMEOUT_S = float(os.environ.get("MAX_TIMEOUT_S", "600") or "600")
_MEMORY_TIMEOUT_MULTIPLIER = float(
    os.environ.get("MEMORY_TIMEOUT_MULTIPLIER", "1.5") or "1.5"
)
_MEMORY_LOOKBACK_DAYS = float(os.environ.get("MEMORY_LOOKBACK_DAYS", "7") or "7")
_MEMORY_TIMEOUT_MIN_FAILURES = int(
    os.environ.get("MEMORY_TIMEOUT_MIN_FAILURES", "2") or "2"
)


def _redact_value(key: Optional[str], value: Any) -> Any:
    """
    Recursively redact secret-like values.

    - Keys that contain any secret substring (case-insensitive) → \"***REDACTED***\"
    - Nested dicts/lists are walked recursively.
    - Non-JSON-serializable objects are coerced to str()[:500].
    """
    key_lower = (key or "").lower()
    if any(secret in key_lower for secret in _SECRET_KEYS_REDACT):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact_value(None, v) for v in value]
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)[:500]


def _redact_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of params with secret-like keys redacted for storage (nested-safe)."""
    if not isinstance(params, dict):
        return {}
    return {k: _redact_value(k, v) for k, v in params.items()}


def _params_hash(skill_name: str, params: Dict[str, Any]) -> str:
    """
    Stable hash for correlation: sha256(redacted params + skill_name).

    Uses canonical JSON (sort_keys=True) of {\"skill\": NAME, \"params\": REDACTED_PARAMS}.
    """
    try:
        payload = {
            "skill": (skill_name or "").strip().upper(),
            "params": _redact_params(params),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
    except Exception:
        return "?"


def _error_type_from_message(message: str) -> str:
    """Classify error for correlation (e.g. timeout, boomi, network)."""
    if not message:
        return "unknown"
    msg = (message or "").lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "boomi" in msg:
        return "boomi"
    if "connection" in msg or "network" in msg or "unreachable" in msg:
        return "network"
    if "circuit" in msg:
        return "circuit"
    return "error"


@dataclass
class ConsultMemoryResult:
    """Result of consulting the Knowledge Store before execution."""

    suggested_timeout_seconds: Optional[float] = None
    suggested_alternative_skill: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0
    last_failure_reason: Optional[str] = None
    last_failure_error_type: Optional[str] = None


class KnowledgeStore:
    """
    Global Memory: task success patterns, failure correlation, optimization hints.
    All writes run in executor (non-blocking). SQLite WAL for consistency.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._path = db_path or DEFAULT_KNOWLEDGE_DB
        self._init_done = False
        self._lock = asyncio.Lock()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_success_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    skill_chain TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    input_params_json TEXT,
                    chain_id TEXT,
                    mission_id TEXT,
                    work_item_id INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_success_skill_hash ON task_success_patterns(skill_name, input_hash);
                CREATE INDEX IF NOT EXISTS idx_success_skill ON task_success_patterns(skill_name);
                CREATE INDEX IF NOT EXISTS idx_success_created ON task_success_patterns(created_at);

                CREATE TABLE IF NOT EXISTS failure_correlation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    params_snapshot_json TEXT,
                    env_context_json TEXT NOT NULL,
                    outcome TEXT,
                    chain_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_failure_skill_hash ON failure_correlation(skill_name, input_hash);
                CREATE INDEX IF NOT EXISTS idx_failure_skill ON failure_correlation(skill_name);
                CREATE INDEX IF NOT EXISTS idx_failure_error_type ON failure_correlation(error_type);
                CREATE INDEX IF NOT EXISTS idx_failure_created ON failure_correlation(created_at);
                """
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        except Exception as e:
            logger.warning("knowledge_store schema init failed: %s", e)

    def _record_success_sync(
        self,
        skill_name: str,
        skill_chain: List[str],
        params: Dict[str, Any],
        chain_id: str,
        mission_id: str,
        work_item_id: int,
    ) -> None:
        conn = sqlite3.connect(self._path)
        try:
            self._ensure_schema(conn)
            try:
                input_hash = _params_hash(skill_name, params)
                skill_chain_json = json.dumps(skill_chain)
                params_redacted = _redact_params(params)
                params_json = json.dumps(params_redacted)[:4000]
                conn.execute(
                    """
                    INSERT INTO task_success_patterns
                    (skill_name, skill_chain, input_hash, input_params_json, chain_id, mission_id, work_item_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        skill_name,
                        skill_chain_json,
                        input_hash,
                        params_json,
                        chain_id,
                        mission_id,
                        str(work_item_id),
                        datetime.utcnow().isoformat() + "Z",
                    ),
                )
                conn.commit()
            except Exception as e:
                logger.warning("knowledge_store record_success failed: %s", e)
        finally:
            conn.close()

    def _record_failure_sync(
        self,
        skill_name: str,
        error_message: str,
        params: Dict[str, Any],
        env_context: Dict[str, Any],
        outcome: str,
        chain_id: str,
    ) -> None:
        conn = sqlite3.connect(self._path)
        try:
            self._ensure_schema(conn)
            try:
                input_hash = _params_hash(skill_name, params)
                error_type = _error_type_from_message(error_message)
                params_redacted = _redact_params(params)
                params_json = json.dumps(params_redacted)[:4000]
                env_json = json.dumps(env_context)[:2000]
                conn.execute(
                    """
                    INSERT INTO failure_correlation
                    (skill_name, error_message, error_type, input_hash, params_snapshot_json, env_context_json, outcome, chain_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        skill_name,
                        (error_message or "")[:2000],
                        error_type,
                        input_hash,
                        params_json,
                        env_json,
                        outcome,
                        chain_id,
                        datetime.utcnow().isoformat() + "Z",
                    ),
                )
                conn.commit()
            except Exception as e:
                logger.warning("knowledge_store record_failure failed: %s", e)
        finally:
            conn.close()

    def _consult_memory_sync(
        self,
        skill_name: str,
        params: Dict[str, Any],
        current_timeout_seconds: float,
    ) -> Dict[str, Any]:
        """Sync consult: return suggestions and counts. Run in executor. Fail-closed on error."""
        conn = sqlite3.connect(self._path)
        try:
            self._ensure_schema(conn)
            try:
                input_hash = _params_hash(skill_name, params)
                key = (skill_name or "").strip().upper()

                # Success count for this skill + input_hash
                cur = conn.execute(
                    "SELECT COUNT(*) FROM task_success_patterns WHERE skill_name = ? AND input_hash = ?",
                    (key, input_hash),
                )
                success_count = cur.fetchone()[0] or 0

                # Failures for this skill + input_hash in recent window (most recent first)
                lookback_days = max(_MEMORY_LOOKBACK_DAYS, 0.0)
                cutoff_ts = None
                if lookback_days > 0:
                    cutoff_ts = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat() + "Z"

                if cutoff_ts:
                    cur = conn.execute(
                        """
                        SELECT error_message, error_type, env_context_json, created_at
                        FROM failure_correlation
                        WHERE skill_name = ? AND input_hash = ? AND created_at >= ?
                        ORDER BY created_at DESC
                        LIMIT 20
                        """,
                        (key, input_hash, cutoff_ts),
                    )
                else:
                    cur = conn.execute(
                        """
                        SELECT error_message, error_type, env_context_json, created_at
                        FROM failure_correlation
                        WHERE skill_name = ? AND input_hash = ?
                        ORDER BY created_at DESC
                        LIMIT 20
                        """,
                        (key, input_hash),
                    )
                rows = cur.fetchall()
                failure_count = len(rows)
                last_reason = rows[0][0] if rows else None
                last_error_type = rows[0][1] if rows else None
                last_env = None
                if rows and rows[0][2]:
                    try:
                        last_env = json.loads(rows[0][2])
                    except Exception:
                        last_env = None

                # Timeout suggestion: only if we have enough recent timeout failures
                suggested_timeout: Optional[float] = None
                timeout_failures = [
                    r for r in rows if (r[1] or "").lower() == "timeout"
                ]
                if (
                    timeout_failures
                    and len(timeout_failures) >= _MEMORY_TIMEOUT_MIN_FAILURES
                ):
                    try:
                        latest_env = last_env or {}
                        prev_timeout = float(
                            latest_env.get("timeout_seconds") or current_timeout_seconds
                        )
                    except (TypeError, ValueError):
                        prev_timeout = current_timeout_seconds
                    suggested_timeout = max(
                        0.0,
                        min(
                            _MAX_TIMEOUT_S,
                            prev_timeout * _MEMORY_TIMEOUT_MULTIPLIER,
                        ),
                    )
                    if suggested_timeout <= current_timeout_seconds:
                        suggested_timeout = min(
                            _MAX_TIMEOUT_S,
                            current_timeout_seconds * _MEMORY_TIMEOUT_MULTIPLIER,
                        )

                # If we have repeated failures and this skill has a registered alternative, suggest it
                suggested_alternative: Optional[str] = None
                if failure_count > 0:
                    try:
                        from skills.resilience import get_alternatives

                        alts = get_alternatives(key)
                        if alts:
                            suggested_alternative = alts[0]
                    except Exception:
                        suggested_alternative = None

                return {
                    "suggested_timeout_seconds": suggested_timeout,
                    "suggested_alternative_skill": suggested_alternative,
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "last_failure_reason": last_reason,
                    "last_failure_error_type": last_error_type,
                }
            except Exception as e:
                logger.warning("knowledge_store consult_memory failed: %s", e)
                return {
                    "suggested_timeout_seconds": None,
                    "suggested_alternative_skill": None,
                    "success_count": 0,
                    "failure_count": 0,
                    "last_failure_reason": None,
                    "last_failure_error_type": None,
                }
        finally:
            conn.close()

    async def record_success(
        self,
        skill_name: str,
        skill_chain: List[str],
        params: Dict[str, Any],
        chain_id: str,
        mission_id: str,
        work_item_id: int,
    ) -> None:
        """Record a successful task execution (skill chain + input parameters). Non-blocking."""
        key = (skill_name or "").strip().upper()
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._record_success_sync,
                key,
                skill_chain,
                params,
                chain_id,
                mission_id,
                work_item_id,
            )

    async def record_failure(
        self,
        skill_name: str,
        error_message: str,
        params: Dict[str, Any],
        env_context: Dict[str, Any],
        outcome: str,
        chain_id: str,
    ) -> None:
        """Record a failure with error and environmental context. Non-blocking."""
        key = (skill_name or "").strip().upper()
        msg = (error_message or "")[:2000]
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._record_failure_sync,
                key,
                msg,
                params,
                env_context,
                outcome,
                chain_id,
            )

    async def consult_memory(
        self,
        skill_name: str,
        params: Dict[str, Any],
        current_timeout_seconds: float,
    ) -> ConsultMemoryResult:
        """
        Consult memory before execution. Returns suggested timeout and/or alternative skill
        when past failures correlate with this task. Non-blocking.
        """
        key = (skill_name or "").strip().upper()
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            self._consult_memory_sync,
            key,
            params,
            current_timeout_seconds,
        )
        return ConsultMemoryResult(
            suggested_timeout_seconds=raw.get("suggested_timeout_seconds"),
            suggested_alternative_skill=raw.get("suggested_alternative_skill"),
            success_count=raw.get("success_count") or 0,
            failure_count=raw.get("failure_count") or 0,
            last_failure_reason=raw.get("last_failure_reason"),
            last_failure_error_type=raw.get("last_failure_error_type"),
        )


_default_knowledge_store: Optional[KnowledgeStore] = None


def get_knowledge_store(db_path: Optional[str] = None) -> KnowledgeStore:
    """Get or create the default KnowledgeStore."""
    global _default_knowledge_store
    if _default_knowledge_store is None:
        _default_knowledge_store = KnowledgeStore(db_path)
    return _default_knowledge_store
