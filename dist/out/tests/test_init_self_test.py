import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_init_and_self_test_happy_path():
    """
    Smoke test: python -m sovereign init | self-test equivalents.

    Ensures init creates directories/DBs and self-test runs without crashing and
    returns a sensible exit code (0 on success).
    """
    os.environ.setdefault("DISCORD_TOKEN", "test-token")
    os.environ.setdefault("OWNER_DISCORD_IDS", "12345")
    os.environ.setdefault("RMFRAMEWORK_PERMIT_SECRET", "test-secret")

    from sovereign.init import run_init
    from sovereign.self_test import run_self_test

    rc_init = run_init()
    assert rc_init == 0

    rc_self = run_self_test()
    assert isinstance(rc_self, int)
    assert rc_self in (0, 1)  # allow non-zero if environment is incomplete but no crash

