# tests/test_config_migration.py — Config schema migration (v4.10)

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_schema import migrate_config, validate_schema_version, CONFIG_SCHEMA_VERSION, V2_DEFAULTS


def test_migrate_v1_to_v2():
    cfg = {"spend": 10.0, "limit": 50.0, "austerity": 45.0, "ledger": [], "config_schema_version": 1}
    out = migrate_config(cfg)
    assert out.get("config_schema_version") == 2
    for k in ("pause_new_work", "resume_mode", "monitoring_channel_id", "heartbeat_s"):
        assert k in out
    assert out.get("heartbeat_s") == V2_DEFAULTS["heartbeat_s"]


def test_validate_schema_version_ok():
    validate_schema_version({"config_schema_version": 1})
    validate_schema_version({"config_schema_version": CONFIG_SCHEMA_VERSION})


def test_validate_schema_version_future_raises():
    try:
        validate_schema_version({"config_schema_version": 99})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "99" in str(e)
