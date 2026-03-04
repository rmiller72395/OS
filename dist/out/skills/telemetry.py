# skills/telemetry.py — Execution telemetry & performance learning (Intelligence Engine)
#
# Post-flight report per execution: input (redacted), output, duration, outcome. Store in SQLite or JSON.
# Flag skills consistently >2s for "Refactoring Needed". Chain ID (UUID) for full execution chain.
# Windows-native; async, non-blocking.
# See Intelligence Engine spec.

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Flag threshold: if skill avg duration exceeds this (ms), log "Refactoring Needed"
SLOW_SKILL_THRESHOLD_MS = 2000
# Default path for telemetry DB (same dir as project or cwd)
DEFAULT_TELEMETRY_PATH = os.environ.get("RMFRAMEWORK_TELEMETRY_DB", "execution_telemetry.db")


@dataclass
class PostFlightReport:
    """Post-flight report for one skill execution."""

    chain_id: str
    trace_id: str
    skill_name: str
    mission_id: str
    work_item_id: int
    input_hash: str
    output_summary: str
    duration_ms: float
    outcome: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    refactoring_flag: bool = False


class TelemetryStore:
    """
    Store and query execution telemetry. SQLite WAL for consistency with AuditDB.
    All write/read can be run in executor to stay non-blocking.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._path = db_path or DEFAULT_TELEMETRY_PATH
        self._init_done = False
        self._lock = asyncio.Lock()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                mission_id TEXT NOT NULL,
                work_item_id INTEGER NOT NULL,
                input_hash TEXT,
                output_summary TEXT,
                duration_ms REAL NOT NULL,
                outcome TEXT NOT NULL,
                refactoring_flag INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_chain ON execution_telemetry(chain_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_skill ON execution_telemetry(skill_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_created ON execution_telemetry(created_at)")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()

    def _write_report_sync(self, report: PostFlightReport) -> None:
        conn = sqlite3.connect(self._path)
        try:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO execution_telemetry
                (chain_id, trace_id, skill_name, mission_id, work_item_id, input_hash, output_summary, duration_ms, outcome, refactoring_flag, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.chain_id,
                    report.trace_id,
                    report.skill_name,
                    report.mission_id,
                    str(report.work_item_id),
                    report.input_hash,
                    report.output_summary[:2000] if report.output_summary else "",
                    report.duration_ms,
                    report.outcome,
                    1 if report.refactoring_flag else 0,
                    report.ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def record_report(self, report: PostFlightReport) -> None:
        """Append a post-flight report. Non-blocking (run in executor)."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._write_report_sync, report)

    def _get_slow_skills_sync(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Skills with avg duration > SLOW_SKILL_THRESHOLD_MS (ms)."""
        conn = sqlite3.connect(self._path)
        try:
            self._ensure_schema(conn)
            cur = conn.execute(
                """
                SELECT skill_name, AVG(duration_ms) as avg_ms, COUNT(*) as runs
                FROM execution_telemetry
                WHERE outcome = 'SUCCESS'
                GROUP BY skill_name
                HAVING avg_ms > ?
                ORDER BY avg_ms DESC
                LIMIT ?
                """,
                (SLOW_SKILL_THRESHOLD_MS, limit),
            )
            rows = cur.fetchall()
            return [
                {"skill_name": r[0], "avg_duration_ms": r[1], "runs": r[2], "refactoring_needed": True}
                for r in rows
            ]
        finally:
            conn.close()

    async def get_slow_skills(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return skills that consistently exceed SLOW_SKILL_THRESHOLD_MS. Non-blocking."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_slow_skills_sync, limit)

    def _get_chain_sync(self, chain_id: str) -> List[Dict[str, Any]]:
        """All reports for an execution chain."""
        conn = sqlite3.connect(self._path)
        try:
            self._ensure_schema(conn)
            cur = conn.execute(
                "SELECT chain_id, trace_id, skill_name, mission_id, work_item_id, duration_ms, outcome, created_at FROM execution_telemetry WHERE chain_id = ? ORDER BY created_at",
                (chain_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "chain_id": r[0],
                    "trace_id": r[1],
                    "skill_name": r[2],
                    "mission_id": r[3],
                    "work_item_id": r[4],
                    "duration_ms": r[5],
                    "outcome": r[6],
                    "created_at": r[7],
                }
                for r in rows
            ]
        finally:
            conn.close()

    async def get_chain(self, chain_id: str) -> List[Dict[str, Any]]:
        """Return all telemetry entries for a chain. Non-blocking."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_chain_sync, chain_id)


# Default store instance
_default_store: Optional[TelemetryStore] = None


def get_telemetry_store(db_path: Optional[str] = None) -> TelemetryStore:
    """Get or create the default TelemetryStore."""
    global _default_store
    if _default_store is None:
        _default_store = TelemetryStore(db_path)
    return _default_store


def build_post_flight_report(
    chain_id: str,
    trace_id: str,
    skill_name: str,
    mission_id: str,
    work_item_id: int,
    input_hash: str,
    output_summary: str,
    duration_ms: float,
    outcome: str,
) -> PostFlightReport:
    """Build report and set refactoring_flag if duration exceeds threshold."""
    refactoring_flag = duration_ms >= SLOW_SKILL_THRESHOLD_MS
    if refactoring_flag:
        logger.warning(
            "telemetry slow_skill skill=%s duration_ms=%.0f threshold_ms=%s refactoring_needed=True",
            skill_name,
            duration_ms,
            SLOW_SKILL_THRESHOLD_MS,
        )
    return PostFlightReport(
        chain_id=chain_id,
        trace_id=trace_id,
        skill_name=skill_name,
        mission_id=mission_id,
        work_item_id=work_item_id,
        input_hash=input_hash,
        output_summary=(output_summary or "")[:2000],
        duration_ms=duration_ms,
        outcome=outcome,
        refactoring_flag=refactoring_flag,
    )
