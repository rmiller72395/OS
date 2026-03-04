# tickets/db.py — SQLite ticket store and state machine (v4.10)
# Durable, local-first; use-case agnostic.

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default DB path
def _tickets_db_path() -> Path:
    base = os.getenv("SOVEREIGN_DATA_DIR", os.getcwd())
    return Path(base) / "data" / "tickets.db"


class TicketStatus(str, Enum):
    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


VALID_TRANSITIONS: Dict[TicketStatus, List[TicketStatus]] = {
    TicketStatus.NEW: [TicketStatus.READY, TicketStatus.CANCELED],
    TicketStatus.READY: [TicketStatus.RUNNING, TicketStatus.CANCELED],
    TicketStatus.RUNNING: [TicketStatus.DONE, TicketStatus.FAILED, TicketStatus.BLOCKED, TicketStatus.CANCELED],
    TicketStatus.BLOCKED: [TicketStatus.READY, TicketStatus.CANCELED],
    TicketStatus.DONE: [],
    TicketStatus.FAILED: [TicketStatus.READY],
    TicketStatus.CANCELED: [],
}


@dataclass
class Ticket:
    ticket_id: str
    title: str
    description: str
    status: str
    priority: int
    created_at: str
    updated_at: str
    created_by: Optional[str]
    assigned_to: Optional[str]
    labels: List[str]
    budget_hint: Optional[str]
    tools_allowed: Optional[List[str]]
    external_refs: Optional[Dict[str, Any]]
    last_run_id: Optional[str]
    last_error_signature: Optional[str]
    artifacts: Optional[List[Any]]
    block_reason: Optional[str]
    plan_hash: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    priority INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    assigned_to TEXT,
    labels_json TEXT,
    budget_hint TEXT,
    tools_allowed_json TEXT,
    external_refs_json TEXT,
    last_run_id TEXT,
    last_error_signature TEXT,
    artifacts_json TEXT,
    block_reason TEXT,
    plan_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_updated ON tickets(updated_at);

CREATE TABLE IF NOT EXISTS ticket_comments (
    comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket ON ticket_comments(ticket_id);
"""


def _get_conn() -> sqlite3.Connection:
    path = _tickets_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    def jload(s):
        if s is None:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None
    labels = jload(row["labels_json"]) or []
    tools = jload(row["tools_allowed_json"])
    refs = jload(row["external_refs_json"])
    artifacts = jload(row["artifacts_json"])
    return Ticket(
        ticket_id=row["ticket_id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        priority=row["priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        assigned_to=row["assigned_to"],
        labels=labels if isinstance(labels, list) else [],
        budget_hint=row["budget_hint"],
        tools_allowed=tools if isinstance(tools, list) else None,
        external_refs=refs if isinstance(refs, dict) else None,
        last_run_id=row["last_run_id"],
        last_error_signature=row["last_error_signature"],
        artifacts=artifacts if isinstance(artifacts, list) else None,
        block_reason=row["block_reason"],
        plan_hash=row["plan_hash"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class TicketComment:
    comment_id: int
    ticket_id: str
    author: str
    created_at: str
    kind: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _row_to_comment(row: sqlite3.Row) -> TicketComment:
    return TicketComment(
        comment_id=row["comment_id"],
        ticket_id=row["ticket_id"],
        author=row["author"],
        created_at=row["created_at"],
        kind=row["kind"],
        message=row["message"],
    )


def add_comment(
    ticket_id: str,
    author: str,
    message: str,
    *,
    kind: str = "operator",
) -> Optional[TicketComment]:
    """
    Append a comment to a ticket.

    kind: "system" or "operator" (case-insensitive; default "operator").
    Returns the created TicketComment or None if ticket_id does not exist.
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        now = _utc_now()
        kind_norm = (kind or "operator").strip().lower()
        if kind_norm not in ("system", "operator"):
            kind_norm = "operator"
        conn.execute(
            """
            INSERT INTO ticket_comments (ticket_id, author, created_at, kind, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                str(author)[:128],
                now,
                kind_norm,
                str(message or "")[:4000],
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ticket_comments WHERE ticket_id=? AND created_at=? ORDER BY comment_id DESC LIMIT 1",
            (ticket_id, now),
        ).fetchone()
        return _row_to_comment(row) if row else None
    finally:
        conn.close()


def list_comments(ticket_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Return comments for a ticket ordered by created_at ASC."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM ticket_comments
            WHERE ticket_id=?
            ORDER BY created_at ASC, comment_id ASC
            LIMIT ? OFFSET ?
            """,
            (ticket_id, limit, offset),
        ).fetchall()
        return [_row_to_comment(r).to_dict() for r in rows]
    finally:
        conn.close()


def init_db() -> None:
    """Create DB and schema if missing."""
    _get_conn().close()


def next_ticket_id(conn: Optional[sqlite3.Connection] = None) -> str:
    """Generate next id TKT-000001 style."""
    c = conn or _get_conn()
    try:
        r = c.execute(
            "SELECT ticket_id FROM tickets WHERE ticket_id LIKE 'TKT-%' ORDER BY ticket_id DESC LIMIT 1"
        ).fetchone()
        if r:
            raw = r[0]
            try:
                num = int(raw.replace("TKT-", ""))
                return f"TKT-{num + 1:06d}"
            except ValueError:
                pass
        return "TKT-000001"
    finally:
        if conn is None:
            c.close()


def create_ticket(
    title: str,
    description: str,
    *,
    priority: int = 3,
    created_by: Optional[str] = None,
    labels: Optional[List[str]] = None,
    budget_hint: Optional[str] = None,
    tools_allowed: Optional[List[str]] = None,
) -> Ticket:
    conn = _get_conn()
    try:
        ticket_id = next_ticket_id(conn)
        now = _utc_now()
        conn.execute(
            """INSERT INTO tickets (
                ticket_id, title, description, status, priority,
                created_at, updated_at, created_by, labels_json, budget_hint, tools_allowed_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticket_id,
                title[:500] if title else "",
                description[:10000] if description else "",
                TicketStatus.NEW.value,
                max(1, min(5, priority)),
                now,
                now,
                created_by,
                json.dumps(labels or []),
                budget_hint,
                json.dumps(tools_allowed) if tools_allowed else None,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return _row_to_ticket(row)
    finally:
        conn.close()


def get_ticket(ticket_id: str) -> Optional[Ticket]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return _row_to_ticket(row) if row else None
    finally:
        conn.close()


def list_tickets(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE status=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tickets ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_ticket(r).to_dict() for r in rows]
    finally:
        conn.close()


def transition_ticket(
    ticket_id: str,
    new_status: str,
    *,
    last_run_id: Optional[str] = None,
    last_error_signature: Optional[str] = None,
    block_reason: Optional[str] = None,
) -> Optional[Ticket]:
    """Validate state machine and update. Returns updated ticket or None."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        current = TicketStatus(row["status"])
        try:
            target = TicketStatus(new_status)
        except ValueError:
            logging.warning(f"Invalid ticket status: {new_status}")
            return None
        allowed = VALID_TRANSITIONS.get(current, [])
        if target not in allowed and target != current:
            logging.warning(f"Invalid transition {current.value} -> {new_status}")
            return None
        now = _utc_now()
        updates = ["updated_at=?", "status=?"]
        params: List[Any] = [now, new_status]
        if last_run_id is not None:
            updates.append("last_run_id=?")
            params.append(last_run_id)
        if last_error_signature is not None:
            updates.append("last_error_signature=?")
            params.append(last_error_signature)
        if block_reason is not None:
            updates.append("block_reason=?")
            params.append(block_reason)
        params.append(ticket_id)
        conn.execute(
            f"UPDATE tickets SET {', '.join(updates)} WHERE ticket_id=?",
            params,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return _row_to_ticket(row) if row else None
    finally:
        conn.close()


def update_ticket(
    ticket_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    assigned_to: Optional[str] = None,
    artifacts: Optional[List[Any]] = None,
    plan_hash: Optional[str] = None,
) -> Optional[Ticket]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        updates = ["updated_at=?"]
        params: List[Any] = [_utc_now()]
        if title is not None:
            updates.append("title=?")
            params.append(title[:500])
        if description is not None:
            updates.append("description=?")
            params.append(description[:10000])
        if assigned_to is not None:
            updates.append("assigned_to=?")
            params.append(assigned_to)
        if artifacts is not None:
            updates.append("artifacts_json=?")
            params.append(json.dumps(artifacts, default=str))
        if plan_hash is not None:
            updates.append("plan_hash=?")
            params.append(plan_hash)
        params.append(ticket_id)
        conn.execute(f"UPDATE tickets SET {', '.join(updates)} WHERE ticket_id=?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return _row_to_ticket(row) if row else None
    finally:
        conn.close()


def get_ready_tickets(limit: int = 5) -> List[Dict[str, Any]]:
    """Return READY tickets ordered by priority asc, updated_at asc (FIFO within priority)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status=? ORDER BY priority ASC, updated_at ASC LIMIT ?",
            (TicketStatus.READY.value, limit),
        ).fetchall()
        return [_row_to_ticket(r).to_dict() for r in rows]
    finally:
        conn.close()


def get_running_without_run() -> List[Dict[str, Any]]:
    """Return tickets in RUNNING state with no last_run_id (stale after crash)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status=? AND (last_run_id IS NULL OR last_run_id='')",
            (TicketStatus.RUNNING.value,),
        ).fetchall()
        return [_row_to_ticket(r).to_dict() for r in rows]
    finally:
        conn.close()
