import os
import sys
import tempfile
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Configure env BEFORE importing knowledge_store so module-level tuning picks these up
os.environ.setdefault("MEMORY_LOOKBACK_DAYS", "1")
os.environ.setdefault("MEMORY_TIMEOUT_MIN_FAILURES", "1")
os.environ.setdefault("MAX_TIMEOUT_S", "120")
os.environ.setdefault("MEMORY_TIMEOUT_MULTIPLIER", "2.0")

from skills.knowledge_store import (  # type: ignore  # imported after env setup
    KnowledgeStore,
    ConsultMemoryResult,
    _redact_params,
    _params_hash,
)


def test_redaction_nested_and_secret_keys():
    params = {
        "password": "secret123",
        "nested": {
            "apiKey": "abc",
            "inner": [{"token": "xyz"}, {"normal": "ok"}],
        },
        "list": ["val", {"session_token": "should-hide"}],
    }
    red = _redact_params(params)
    assert red["password"] == "***REDACTED***"
    assert red["nested"]["apiKey"] == "***REDACTED***"
    assert red["nested"]["inner"][0]["token"] == "***REDACTED***"
    # non-secret values preserved structurally
    assert red["nested"]["inner"][1]["normal"] == "ok"
    assert red["list"][0] == "val"
    assert red["list"][1]["session_token"] == "***REDACTED***"


def test_params_hash_deterministic_and_skill_sensitive():
    p1 = {"a": 1, "b": 2}
    p2 = {"b": 2, "a": 1}
    h1 = _params_hash("MySkill", p1)
    h2 = _params_hash("myskill", p2)
    assert h1 == h2  # order-insensitive, skill-name normalized

    # Different skill -> different hash even with same params
    h3 = _params_hash("OtherSkill", p1)
    assert h3 != h1


def test_consult_memory_suggests_higher_timeout_after_timeout_failure(tmp_path=None):
    tmp_dir = Path(tempfile.mkdtemp()) if tmp_path is None else tmp_path
    db_path = tmp_dir / "ks_timeout.db"
    store = KnowledgeStore(db_path=str(db_path))

    params = {"url": "https://example.com/api"}

    async def scenario():
        await store.record_failure(
            "HTTP_REQUEST",
            "request timed out after 60s",
            params,
            {"timeout_seconds": 60},
            "TIMEOUT",
            "chain-1",
        )
        res: ConsultMemoryResult = await store.consult_memory(
            "HTTP_REQUEST", params, current_timeout_seconds=60.0
        )
        assert res.failure_count >= 1
        assert res.last_failure_error_type == "timeout"
        assert res.suggested_timeout_seconds is not None
        assert res.suggested_timeout_seconds > 60.0

    asyncio.run(scenario())


def test_fail_closed_when_db_unavailable(tmp_path=None):
    """
    If the backing SQLite DB is unavailable/corrupt, KnowledgeStore must not raise;
    consult_memory should return a neutral ConsultMemoryResult.
    """
    import sqlite3 as _sqlite3
    import skills.knowledge_store as ks

    tmp_dir = Path(tempfile.mkdtemp()) if tmp_path is None else tmp_path
    db_path = tmp_dir / "ks_error.db"
    store = ks.KnowledgeStore(db_path=str(db_path))

    original_connect = ks.sqlite3.connect

    def _bad_connect(*a, **kw):
        raise _sqlite3.OperationalError("boom")

    ks.sqlite3.connect = _bad_connect
    try:
        res = asyncio.run(store.consult_memory("FOO", {"x": 1}, current_timeout_seconds=30.0))
        assert isinstance(res, ks.ConsultMemoryResult)
        assert res.failure_count == 0
        assert res.success_count == 0
        assert res.suggested_timeout_seconds is None
        assert res.suggested_alternative_skill is None
    finally:
        ks.sqlite3.connect = original_connect

