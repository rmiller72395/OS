import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure bot imports with stubbed deps
from tests import test_validator as _tv  # noqa: F401

import bot  # type: ignore


def test_single_instance_lock_exits_on_lock_failure():
    """If the lock cannot be acquired, _acquire_single_instance_lock_or_exit should raise SystemExit."""
    original_lock_file = bot._lock_file
    bot._GLOBAL_BOT_REF["instance_lock_file"] = None

    def _failing_lock(f, *, blocking: bool = True):
        raise OSError("busy")

    bot._lock_file = _failing_lock
    try:
        try:
            bot._acquire_single_instance_lock_or_exit()
            assert False, "expected SystemExit when lock acquisition fails"
        except SystemExit as e:
            assert e.code != 0
    finally:
        bot._lock_file = original_lock_file

