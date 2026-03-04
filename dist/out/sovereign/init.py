# sovereign/init.py — First-run bootstrap (v4.10)

from __future__ import annotations

import os
import sys
from pathlib import Path

# Run from project root (parent of sovereign package)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_init() -> int:
    """Create dirs, copy config template if missing, validate env, init DBs."""
    os.chdir(ROOT)
    data = ROOT / "data"
    runs = data / "runs"
    backups = ROOT / "backups"
    logs = ROOT / "logs"
    for d in (data, runs, backups, logs):
        d.mkdir(parents=True, exist_ok=True)
    print("Created directories: data/, data/runs/, backups/, logs/")

    # Config template
    config_file = ROOT / "sovereign_config.json"
    template = ROOT / "sovereign_config.template.json"
    if not config_file.exists() and template.exists():
        import shutil
        shutil.copy(template, config_file)
        print(f"Copied {template.name} -> sovereign_config.json")
    elif not config_file.exists():
        # Create minimal default
        import json
        from config_schema import V2_DEFAULTS
        default = {
            "spend": 0.0,
            "limit": 50.0,
            "austerity": 45.0,
            "ledger": [],
            "cost_unknown": False,
            "config_io_error": False,
            "owner_threshold_usd": 0.0,
            "workers_auto_run": True,
            "workers_max_auto": 2,
            "managers_enabled": True,
            "manager_fanout": 2,
            "global_allowed_tools": [],
            "config_schema_version": 2,
            **V2_DEFAULTS,
        }
        config_file.write_text(json.dumps(default, indent=2), encoding="utf-8")
        print("Created sovereign_config.json from defaults")

    # Env check
    required = ["DISCORD_TOKEN", "OWNER_DISCORD_IDS", "RMFRAMEWORK_PERMIT_SECRET"]
    missing = [k for k in required if not os.getenv(k, "").strip()]
    if missing:
        print(f"WARNING: Missing env: {', '.join(missing)}. Set in .env or environment.")
    else:
        print("Required env vars present (DISCORD_TOKEN, OWNER_DISCORD_IDS, RMFRAMEWORK_PERMIT_SECRET)")

    # Tickets DB
    try:
        from tickets.db import init_db
        init_db()
        print("Initialized tickets DB (data/tickets.db)")
    except Exception as e:
        print(f"Tickets DB init failed: {e}")
        return 1

    # Audit DB (bot's) — ensure schema exists
    audit_db = ROOT / "sovereign_audit.db"
    if not audit_db.exists():
        print("Note: sovereign_audit.db will be created on first bot run.")

    print("\nNext steps:")
    print("  1. Set .env (DISCORD_TOKEN, OWNER_DISCORD_IDS, RMFRAMEWORK_PERMIT_SECRET)")
    print("  2. Run: python -m sovereign self-test")
    print("  3. Start bot: python bot.py (or run_windows.ps1)")
    print("  4. Task Scheduler: run run_windows.ps1 at startup if headless")
    return 0
