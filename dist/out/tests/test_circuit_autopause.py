import os
import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse stubs/env from validator tests so importing bot is safe
from tests import test_validator as _tv  # noqa: F401

os.environ.setdefault("MONITORING_CHANNEL_ID", "")  # disable alert sending for this smoke test

import bot  # type: ignore  # imported after stubs
from tickets import queue_runner


def test_circuit_trip_sets_pause_and_queue_paused():
    """_on_circuit_tripped should mark pause_new_work and pause the queue runner."""
    # Patch queue runner pause to capture state
    paused_state = {"value": None}
    orig_set_queue_paused = queue_runner.set_queue_paused

    def _fake_set_queue_paused(paused: bool) -> None:
        paused_state["value"] = paused

    queue_runner.set_queue_paused = _fake_set_queue_paused

    async def scenario():
        # Ensure config is initialised
        await bot._cfg.init_async()
        # Provide a dummy bot instance so _GLOBAL_BOT_REF lookup succeeds
        class DummyBot:
            def get_channel(self, cid: int):
                return None

        bot._GLOBAL_BOT_REF["bot"] = DummyBot()

        # Start from unpaused state
        async with bot._cfg.lock:
            snap = bot._cfg.snapshot()
            snap["pause_new_work"] = False
            bot._cfg._data = snap  # type: ignore[attr-defined]
            await bot._cfg.flush_durable()

        await bot._on_circuit_tripped("TEST_SKILL", failures=3)

        async with bot._cfg.lock:
            snap_after = bot._cfg.snapshot()
        assert snap_after.get("pause_new_work") is True

    try:
        asyncio.run(scenario())
        assert paused_state["value"] is True
    finally:
        queue_runner.set_queue_paused = orig_set_queue_paused

