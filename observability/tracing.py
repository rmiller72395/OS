# observability/tracing.py — Execution Run Graph (v4.10)
#
# Event-sourced run state: run_id, trace_id, spans (DAG), events.
# Persist to data/runs/<run_id>.jsonl; single-writer, thread-safe.
# Windows-native; no Linux-only primitives.

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Base dir for run logs (default: data/runs relative to cwd)
def _runs_dir() -> Path:
    base = os.getenv("SOVEREIGN_DATA_DIR", os.getcwd())
    return Path(base) / "data" / "runs"

_runs_dir_ensure: bool = False

def _ensure_runs_dir() -> None:
    global _runs_dir_ensure
    if _runs_dir_ensure:
        return
    d = _runs_dir()
    d.mkdir(parents=True, exist_ok=True)
    _runs_dir_ensure = True

# In-process lock for single writer per run_id
_write_locks: Dict[str, asyncio.Lock] = {}
_lock_for_run: asyncio.Lock = asyncio.Lock()

async def _get_run_lock(run_id: str) -> asyncio.Lock:
    async with _lock_for_run:
        if run_id not in _write_locks:
            _write_locks[run_id] = asyncio.Lock()
        return _write_locks[run_id]

def _run_log_path(run_id: str) -> Path:
    _ensure_runs_dir()
    return _runs_dir() / f"{run_id}.jsonl"

async def _append_line(run_id: str, line: str) -> None:
    lock = await _get_run_lock(run_id)
    async with lock:
        path = _run_log_path(run_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass

def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_run(
    mission_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    """
    Start a new run. Returns (run_id, trace_id).
    Emits run_started event to data/runs/<run_id>.jsonl.
    """
    run_id = uuid.uuid4().hex
    trace_id = uuid.uuid4().hex
    _ensure_runs_dir()
    ev = {
        "ts": _utc_now_iso(),
        "type": "run_started",
        "run_id": run_id,
        "trace_id": trace_id,
        "mission_id": mission_id,
        "ticket_id": ticket_id,
        "context": context or {},
    }
    line = json.dumps(ev, default=str)
    # Synchronous first write so run_id is immediately visible
    path = _run_log_path(run_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return run_id, trace_id


async def start_span(
    run_id: str,
    name: str,
    parent_span_id: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> str:
    """Emit span_started event; return span_id."""
    span_id = uuid.uuid4().hex[:16]
    ev = {
        "ts": _utc_now_iso(),
        "type": "span_started",
        "run_id": run_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "attributes": attributes or {},
    }
    await _append_line(run_id, json.dumps(ev, default=str))
    return span_id


async def end_span(
    run_id: str,
    span_id: str,
    status: str = "ok",
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit span_ended event."""
    ev = {
        "ts": _utc_now_iso(),
        "type": "span_ended",
        "run_id": run_id,
        "span_id": span_id,
        "status": status,
        "attributes": attributes or {},
    }
    await _append_line(run_id, json.dumps(ev, default=str))


async def record_event(
    run_id: str,
    event_type: str,
    message: str,
    severity: str = "info",
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a generic event (e.g. error, step_started, step_completed)."""
    ev = {
        "ts": _utc_now_iso(),
        "type": event_type,
        "run_id": run_id,
        "message": message,
        "severity": severity,
        "attributes": attributes or {},
    }
    await _append_line(run_id, json.dumps(ev, default=str))


def run_summary(run_id: str, runs_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Aggregate status, duration, cost, errors from run's JSONL.
    Returns dict: status, duration_seconds, total_model_calls, total_cost, error_count, errors[], last_events[].
    """
    dir_path = runs_dir or _runs_dir()
    path = dir_path / f"{run_id}.jsonl"
    out: Dict[str, Any] = {
        "run_id": run_id,
        "status": "unknown",
        "duration_seconds": None,
        "total_model_calls": 0,
        "total_cost": 0.0,
        "error_count": 0,
        "errors": [],
        "last_events": [],
    }
    if not path.exists():
        return out
    start_ts: Optional[str] = None
    end_ts: Optional[str] = None
    events: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(ev)
                ts = ev.get("ts")
                if ts:
                    if ev.get("type") == "run_started":
                        start_ts = ts
                    if ev.get("type") in ("run_ended", "run_failed"):
                        end_ts = ts
                if ev.get("type") == "event" or ev.get("severity") == "error":
                    out["error_count"] += 1
                    msg = ev.get("message", str(ev))
                    out["errors"].append(msg[:500])
                if ev.get("attributes"):
                    att = ev["attributes"]
                    out["total_cost"] += float(att.get("cost", 0) or 0)
                    if att.get("model_call"):
                        out["total_model_calls"] += 1
    except OSError as e:
        logging.warning(f"run_summary read error for {run_id}: {e}")
        return out
    if events:
        out["last_events"] = [e for e in events[-20:]]
    if start_ts and end_ts:
        try:
            from datetime import datetime, timezone
            def _parse_utc(s: str):
                # ISO format with Z or +00:00
                s = s.replace("Z", "+00:00")
                return datetime.fromisoformat(s)
            t0 = _parse_utc(start_ts)
            t1 = _parse_utc(end_ts)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=timezone.utc)
            if t1.tzinfo is None:
                t1 = t1.replace(tzinfo=timezone.utc)
            out["duration_seconds"] = (t1 - t0).total_seconds()
        except Exception:
            pass
    for ev in reversed(events):
        if ev.get("type") == "run_failed":
            out["status"] = "failed"
            break
        if ev.get("type") == "run_ended":
            out["status"] = "completed"
            break
    if out["status"] == "unknown" and start_ts:
        out["status"] = "running"
    return out
