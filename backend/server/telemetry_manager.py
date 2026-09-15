"""
telemetry_manager.py — Aggregates and caches robot telemetry payloads.

Provides a shared fleet view that both TaskManager and the bidding agent use.
"""

from __future__ import annotations
import time
from typing import Optional, Callable


STALE_THRESHOLD_S = 5.0   # seconds without telemetry → mark as offline


class TelemetryManager:
    """
    Maintains the latest telemetry snapshot for every known robot.

    fleet[robot_id] = {robotId, x, y, heading, status, battery,
                        currentTaskId, blocked, online, _received_at}
    """

    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self.fleet: dict[str, dict] = {}

    def on_telemetry(self, payload: dict) -> None:
        """Handle an incoming robots/telemetry payload."""
        robot_id = payload.get("robotId")
        if not robot_id:
            return
        snapshot = dict(payload)
        snapshot["_received_at"] = time.time()
        self.fleet[robot_id] = snapshot

    def prune_stale(self) -> None:
        """Mark robots as offline if no telemetry received recently."""
        now = time.time()
        for robot_id, snap in self.fleet.items():
            if snap.get("online", True) and (now - snap.get("_received_at", now)) > STALE_THRESHOLD_S:
                self.fleet[robot_id]["online"] = False
                self._log(f"Robot {robot_id} marked offline (no telemetry for {STALE_THRESHOLD_S}s)")

    def get(self, robot_id: str) -> Optional[dict]:
        return self.fleet.get(robot_id)

    def snapshot(self) -> list[dict]:
        """Return a list of all current telemetry snapshots."""
        return [
            {k: v for k, v in s.items() if not k.startswith("_")}
            for s in self.fleet.values()
        ]

    @property
    def online_count(self) -> int:
        return sum(1 for s in self.fleet.values() if s.get("online", True))

    def is_all_reported(self, roster: list[dict]) -> bool:
        """True when every roster member has sent at least one telemetry message."""
        return all(r["id"] in self.fleet for r in roster)
