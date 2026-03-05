# notifications/notifier.py — Alert delivery: Discord or file (simulation)

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


class Notifier(ABC):
    """Interface for sending alert payloads."""

    @abstractmethod
    async def send_alert(self, payload: Dict[str, Any]) -> None:
        """Send an alert. Payload: run_id, mission_id, ticket_id, component, error_signature, what_happened, what_to_do, lines (body lines), dashboard_port."""
        pass


class DiscordNotifier(Notifier):
    """Send alerts to a Discord channel via bot.get_channel and channel.send."""

    def __init__(self, bot_instance: Any, channel_id: Optional[str], max_chars: int = 1950) -> None:
        self._bot = bot_instance
        self._channel_id = (channel_id or "").strip() or None
        self._max_chars = max_chars

    async def send_alert(self, payload: Dict[str, Any]) -> None:
        if not self._channel_id or not self._bot:
            return
        body = payload.get("body") or "\n".join(payload.get("lines") or [])
        if not body:
            return
        try:
            ch = self._bot.get_channel(int(self._channel_id))
            if not ch:
                return
            allowed_mentions = getattr(self._bot, "_no_mentions", None)
            while body:
                chunk = body[: self._max_chars]
                body = body[self._max_chars :]
                await ch.send(chunk, allowed_mentions=allowed_mentions)
                if body:
                    await asyncio.sleep(0.3)
        except Exception as e:
            import logging
            logging.warning("DiscordNotifier send_alert: %s", e)


class FileNotifier(Notifier):
    """Write alert payloads to data/simulated_alerts.jsonl (one JSON object per line). No Discord."""

    def __init__(self, path: Optional[Path] = None) -> None:
        base = os.getenv("SOVEREIGN_DATA_DIR", os.getcwd())
        self._path = path or Path(base) / "data" / "simulated_alerts.jsonl"

    async def send_alert(self, payload: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({k: v for k, v in payload.items() if k != "body"} | {"body_preview": (payload.get("body") or "")[:2000]}, default=str) + "\n"
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()


_notifier: Optional[Notifier] = None


def get_notifier() -> Optional[Notifier]:
    return _notifier


def set_notifier(n: Optional[Notifier]) -> None:
    global _notifier
    _notifier = n


def create_notifier(bot_instance: Any = None, channel_id: Optional[str] = None) -> Notifier:
    """Create DiscordNotifier or FileNotifier based on SIMULATION_MODE."""
    if os.getenv("SIMULATION_MODE", "").strip() == "1":
        return FileNotifier()
    return DiscordNotifier(bot_instance, channel_id)
