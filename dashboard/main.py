# dashboard/main.py — FastAPI local dashboard (v4.10)
# GET /health, /runs, /runs/{run_id}, /tickets, /tickets/{id}
# Bind localhost by default; 0.0.0.0 only with explicit config.

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Resolve base dir (project root)
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SOVEREIGN_DATA_DIR", str(BASE_DIR))) / "data"
RUNS_DIR = DATA_DIR / "runs"

app = FastAPI(title="Sovereign Dashboard", version="4.10.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])


def _get_version() -> str:
    vpath = BASE_DIR / "VERSION"
    if vpath.exists():
        return vpath.read_text(encoding="utf-8").strip()
    return "unknown"


@app.get("/health")
def health():
    """Check: run log dir writable, tickets DB readable."""
    checks = {}
    # Run log dir
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        test_file = RUNS_DIR / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        checks["run_log_writable"] = True
    except Exception as e:
        checks["run_log_writable"] = False
        checks["run_log_error"] = str(e)
    # Tickets DB
    try:
        from tickets.db import _get_conn
        conn = _get_conn()
        conn.execute("SELECT 1 FROM tickets LIMIT 1")
        conn.close()
        checks["tickets_db_ok"] = True
    except Exception as e:
        checks["tickets_db_ok"] = False
        checks["tickets_db_error"] = str(e)
    healthy = checks.get("run_log_writable", False) and checks.get("tickets_db_ok", False)
    return {
        "status": "ok" if healthy else "degraded",
        "version": _get_version(),
        "checks": checks,
    }


@app.get("/runs")
def list_runs(limit: int = 20):
    """Recent runs from data/runs/*.jsonl (by mtime)."""
    if not RUNS_DIR.exists():
        return {"runs": [], "count": 0}
    files = list(RUNS_DIR.glob("*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    runs = []
    for p in files[:limit]:
        run_id = p.stem
        try:
            from observability.tracing import run_summary
            s = run_summary(run_id, RUNS_DIR)
            runs.append({
                "run_id": run_id,
                "status": s.get("status", "?"),
                "duration_seconds": s.get("duration_seconds"),
                "total_cost": s.get("total_cost", 0),
                "error_count": s.get("error_count", 0),
            })
        except Exception:
            runs.append({"run_id": run_id, "status": "unknown"})
    return {"runs": runs, "count": len(runs)}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    """Run detail: span tree + events."""
    path = RUNS_DIR / f"{run_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    import json
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    from observability.tracing import run_summary
    summary = run_summary(run_id, RUNS_DIR)
    return {
        "run_id": run_id,
        "summary": summary,
        "events": events,
    }


@app.get("/tickets")
def list_tickets_endpoint(status: str | None = None, limit: int = 50):
    """List tickets; optional status filter."""
    try:
        from tickets.db import list_tickets
        tickets = list_tickets(status=status, limit=limit)
        return {"tickets": tickets, "count": len(tickets)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    """Ticket detail + run history + capability plan summary + active grants."""
    try:
        from tickets.db import get_ticket
        t = get_ticket(ticket_id)
        if not t:
            raise HTTPException(status_code=404, detail="Ticket not found")
        d = t.to_dict()
        # Add run link if last_run_id present
        if d.get("last_run_id"):
            d["last_run_url"] = f"/runs/{d['last_run_id']}"
        # Capability plan summary
        try:
            from skills.capability_plan import get_capability_plan_store
            store = get_capability_plan_store()
            store.ensure_schema()
            plan = store.get_plan(ticket_id)
            d["capability_plan"] = _plan_summary(plan) if plan else None
            d["plan_hash"] = store.get_plan_hash(ticket_id)
        except Exception:
            d["capability_plan"] = None
            d["plan_hash"] = None
        # Active grants (tools/scopes/expiry, tool spend vs cap)
        try:
            from skills.tool_grants import get_tool_grant_store
            grants_store = get_tool_grant_store()
            grants_store.ensure_schema()
            grants = grants_store.list_grants(ticket_id=ticket_id)
            active = [g for g in grants if not g.revoked_at]
            d["active_grants"] = [
                {
                    "grant_id": g.grant_id,
                    "allowed_tools": g.allowed_tools,
                    "allowed_scopes": g.allowed_scopes,
                    "max_tool_spend_usd": g.max_tool_spend_usd,
                    "max_calls": g.max_calls,
                    "expires_at": g.expires_at,
                    "calls_used": (grants_store.get_usage(g.grant_id) or (0, 0))[0],
                    "spend_used_usd": (grants_store.get_usage(g.grant_id) or (0, 0))[1],
                }
                for g in active[:10]
            ]
        except Exception:
            d["active_grants"] = []
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _plan_summary(plan: dict) -> dict:
    """Compact capability plan for API."""
    return {
        "objective": plan.get("objective"),
        "required_tools": [t.get("tool_name") for t in (plan.get("required_tools") or [])],
        "budget": plan.get("budget"),
    }


@app.get("/tools")
def list_tools_endpoint(enabled_only: bool = False):
    """List tools from registry."""
    try:
        from skills.tool_registry import get_tool_registry
        reg = get_tool_registry()
        reg.ensure_schema()
        tools = reg.list_tools(enabled_only=enabled_only)
        return {
            "tools": [
                {"tool_name": t.tool_name, "description": t.description[:200] if t.description else "", "enabled": t.enabled}
                for t in tools
            ],
            "count": len(tools),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tools/{tool_name}")
def get_tool(tool_name: str):
    """Tool detail from registry."""
    try:
        from skills.tool_registry import get_tool_registry
        reg = get_tool_registry()
        reg.ensure_schema()
        t = reg.get_tool(tool_name)
        if not t:
            raise HTTPException(status_code=404, detail="Tool not found")
        return t.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", response_class=HTMLResponse)
def index():
    """Simple HTML index with links."""
    v = _get_version()
    return f"""<!DOCTYPE html>
<html><head><title>Sovereign Dashboard</title></head>
<body>
<h1>Sovereign Dashboard v{v}</h1>
<ul>
<li><a href="/health">Health</a></li>
<li><a href="/runs">Runs</a></li>
<li><a href="/tickets">Tickets</a></li>
<li><a href="/tools">Tools (registry)</a></li>
</ul>
</body></html>
"""


def main():
    import uvicorn
    host = os.getenv("SOVEREIGN_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("SOVEREIGN_DASHBOARD_PORT", "8765"))
    if host == "0.0.0.0":
        print("WARNING: Dashboard bound to 0.0.0.0 — ensure firewall/local network only.")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
