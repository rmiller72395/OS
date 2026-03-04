# config_schema.py — Config schema version and migration (v4.10 / v5.0)

from __future__ import annotations

from typing import Any, Dict

CONFIG_SCHEMA_VERSION = 3

# Keys added in v2 (v4.10 rollout)
V2_DEFAULTS: Dict[str, Any] = {
    "pause_new_work": False,
    "resume_mode": "off",
    "monitoring_channel_id": None,
    "ops_channel_id": None,
    "heartbeat_s": 30,
    "health_stall_s": 300,
    "auto_exit_on_stall": True,
    "log_retention_runs": 500,
    "log_retention_days": 14,
    "log_max_mb": 100,
    "log_compress_old": False,
    "backup_on_startup": True,
    "backup_daily": True,
    "backup_keep_days": 7,
}

# Keys added in v3 (v5.0 tool registry + grants + capability plan)
V3_DEFAULTS: Dict[str, Any] = {
    "tool_registry_db_path": None,  # default: SOVEREIGN_DATA_DIR/data/sovereign_ops.db
    "tool_grants_db_path": None,
    "capability_plan_db_path": None,
    "policy_allowed_scopes": [],  # list or dict by role; default deny
    "policy_denied_scopes": [],
    "default_readonly_tools": [],
    "default_readonly_scopes": [],
    "tool_cost_defaults": {},
}


def migrate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Apply migrations; return updated config. Best-effort."""
    version = cfg.get("config_schema_version", 1)
    if version >= CONFIG_SCHEMA_VERSION:
        return cfg
    # v1 -> v2
    if version < 2:
        for k, v in V2_DEFAULTS.items():
            cfg.setdefault(k, v)
        cfg["config_schema_version"] = 2
    # v2 -> v3
    if version < 3:
        for k, v in V3_DEFAULTS.items():
            cfg.setdefault(k, v)
        cfg["config_schema_version"] = 3
    return cfg


def validate_schema_version(cfg: Dict[str, Any]) -> None:
    """Raise if config is from a future schema we can't migrate."""
    version = cfg.get("config_schema_version", 1)
    if version > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"Config schema version {version} is newer than supported {CONFIG_SCHEMA_VERSION}. "
            "Upgrade the application or reset config."
        )
