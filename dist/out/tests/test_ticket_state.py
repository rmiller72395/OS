# tests/test_ticket_state.py — Ticket state transitions (v4.10)

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Use a temp dir for tickets DB so we don't touch real data
_tmp = tempfile.mkdtemp()
os.environ["SOVEREIGN_DATA_DIR"] = _tmp


def test_create_ticket():
    from tickets.db import create_ticket, get_ticket, TicketStatus
    t = create_ticket("Test", "Desc", priority=1)
    assert t.ticket_id.startswith("TKT-")
    assert t.status == TicketStatus.NEW.value
    t2 = get_ticket(t.ticket_id)
    assert t2 and t2.ticket_id == t.ticket_id


def test_transition_new_to_ready():
    from tickets.db import create_ticket, transition_ticket, TicketStatus
    t = create_ticket("T2", "D2")
    t2 = transition_ticket(t.ticket_id, TicketStatus.READY.value)
    assert t2 and t2.status == TicketStatus.READY.value


def test_transition_ready_to_running():
    from tickets.db import create_ticket, transition_ticket, TicketStatus
    t = create_ticket("T3", "D3")
    transition_ticket(t.ticket_id, TicketStatus.READY.value)
    t2 = transition_ticket(t.ticket_id, TicketStatus.RUNNING.value, last_run_id="run-123")
    assert t2 and t2.status == TicketStatus.RUNNING.value and t2.last_run_id == "run-123"


def test_invalid_transition_rejected():
    from tickets.db import create_ticket, transition_ticket, TicketStatus
    t = create_ticket("T4", "D4")
    # NEW -> RUNNING is invalid (must go READY first)
    t2 = transition_ticket(t.ticket_id, TicketStatus.RUNNING.value)
    assert t2 is None


def test_ticket_comments_roundtrip():
    from tickets.db import create_ticket, add_comment, list_comments

    t = create_ticket("T5", "D5", priority=2)
    c1 = add_comment(t.ticket_id, "12345", "first comment", kind="operator")
    c2 = add_comment(t.ticket_id, "system", "system note", kind="system")
    assert c1 is not None and c2 is not None

    comments = list_comments(t.ticket_id)
    # At least the two we just added should be present
    assert len(comments) >= 2
    kinds = {c["kind"] for c in comments}
    assert "operator" in kinds and "system" in kinds
