# tickets/queue_runner.py — Queue engine: pick READY tickets, start runs, pause, crash-safe (v4.10)

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from tickets.db import (
    TicketStatus,
    get_ready_tickets,
    get_running_without_run,
    transition_ticket,
)

# Global pause flag: when True, queue runner does not start new work
_queue_paused: bool = False


def set_queue_paused(paused: bool) -> None:
    global _queue_paused
    _queue_paused = paused


def is_queue_paused() -> bool:
    return _queue_paused


async def reconcile_running_tickets(
    resume_mode: str,
    run_completed_callback: Optional[Callable[[str], Any]] = None,
) -> None:
    """
    On startup: find RUNNING tickets without active process.
    resume_mode: 'off' -> mark FAILED; 'safe_skip_completed' -> check run log and set FAILED or READY.
    """
    stale = get_running_without_run()
    for t in stale:
        ticket_id = t["ticket_id"]
        last_run_id = t.get("last_run_id")
        if resume_mode == "safe_skip_completed" and last_run_id:
            # Caller can check run log for completion; here we just set back to READY or FAILED
            if run_completed_callback:
                try:
                    result = await run_completed_callback(last_run_id)
                    if result == "completed":
                        transition_ticket(ticket_id, TicketStatus.DONE.value, last_run_id=last_run_id)
                        continue
                except Exception as e:
                    logging.warning(f"run_completed_callback error for {last_run_id}: {e}")
        transition_ticket(ticket_id, TicketStatus.FAILED.value, last_error_signature="reconciled: no active run")


def get_next_ready_ticket() -> Optional[dict]:
    """Return one READY ticket (highest priority, FIFO)."""
    ready = get_ready_tickets(limit=1)
    return ready[0] if ready else None


def has_active_grant_for_ticket(ticket_id: str) -> bool:
    """
    Return True if there is an active (non-revoked, non-expired) tool grant for this ticket.
    Caller can use this before starting a run to avoid BLOCK later (e.g. transition to BLOCKED if False).
    """
    try:
        from skills.tool_grants import get_tool_grant_store
        store = get_tool_grant_store()
        store.ensure_schema()
        return store.get_active_grant(ticket_id=ticket_id) is not None
    except Exception as e:
        logging.warning("has_active_grant_for_ticket: %s", e)
        return False
