# tests/test_tracing.py — Tracing writer concurrency (v4.10)

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_start_run_returns_ids():
    from observability.tracing import start_run
    run_id, trace_id = start_run(mission_id="m1")
    assert run_id and len(run_id) == 32
    assert trace_id and len(trace_id) == 32


def test_record_event_async():
    from observability.tracing import start_run, record_event
    run_id, _ = start_run(mission_id="m2")
    asyncio.run(record_event(run_id, "event", "test", "info", {}))
    # No exception = pass
    assert True


def test_run_summary():
    from observability.tracing import start_run, record_event, run_summary
    run_id, _ = start_run(mission_id="m3")
    rdir = Path(os.getenv("SOVEREIGN_DATA_DIR", str(ROOT))) / "data" / "runs"
    s = run_summary(run_id, rdir)
    assert s["run_id"] == run_id
    assert s["status"] in ("running", "unknown", "completed", "failed")


def test_concurrent_writes_same_run():
    from observability.tracing import start_run, record_event
    run_id, _ = start_run(mission_id="m4")
    async def many():
        await asyncio.gather(*[record_event(run_id, "event", f"msg{i}", "info", {}) for i in range(10)])
    asyncio.run(many())
    from observability.tracing import run_summary
    rdir = Path(os.getenv("SOVEREIGN_DATA_DIR", str(ROOT))) / "data" / "runs"
    s = run_summary(run_id, rdir)
    assert len(s.get("last_events", [])) >= 1
