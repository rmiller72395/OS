import os
import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse stubbed discord/msvcrt/etc setup from test_validator (side effects on import)
from tests import test_validator as _tv  # noqa: F401

os.environ.setdefault("MONITORING_CHANNEL_ID", "1234567890")

import bot  # type: ignore  # imported after stubs/env


class _DummyChannel:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, content: str, **kwargs) -> None:
        self.messages.append(content)


class _DummyBot:
    def __init__(self, ch: _DummyChannel) -> None:
        self._ch = ch

    def get_channel(self, cid: int):
        return self._ch


def test_alert_throttle_deduplicates_within_window():
    """Second alert with same error_signature within throttle window should be throttled, not full duplicate."""
    ch = _DummyChannel()
    b = _DummyBot(ch)

    # Reset throttle state for deterministic test
    bot._alert_throttle.clear()

    async def scenario():
        await bot._send_monitoring_alert(
            b,
            run_id="run-1",
            mission_id="m-1",
            ticket_id="TKT-1",
            component="orchestrator",
            error_signature="SAME_ERROR_SIGNATURE",
            what_happened="First failure",
            what_next=["Step 1"],
            last_events=None,
        )
        first_count = len(ch.messages)
        assert first_count >= 1

        # Immediate repeat with same signature should be throttled
        await bot._send_monitoring_alert(
            b,
            run_id="run-2",
            mission_id="m-2",
            ticket_id="TKT-2",
            component="orchestrator",
            error_signature="SAME_ERROR_SIGNATURE",
            what_happened="Repeat failure",
            what_next=["Step 1"],
            last_events=None,
        )
        second_count = len(ch.messages)

        # Expect exactly one extra message (throttle notice), not a full duplicate multi-chunk alert
        assert second_count == first_count + 1

    asyncio.run(scenario())

