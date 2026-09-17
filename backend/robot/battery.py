"""
battery.py — AMR Battery Management & Autonomous Charging Lifecycle.

Specifications:
- Battery thresholds: WARN = 25.0%, CRITICAL = 10.0%, FULL = 100.0%.
- Dynamic discharge: based on movement speed (travel drain) and standby time (idle drain).
- Autonomous charging: +8.0%/s when parked at an AMR charging bay.
- Decisions: triggers return-to-charge when low or idle without tasks.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


BATTERY_FULL = 100.0
BATTERY_WARN_THRESHOLD = 25.0    # physical return-to-charge threshold (controller.py)
BATTERY_CRITICAL_THRESHOLD = 10.0
CHARGE_RATE_PER_SEC = 8.0        # +8.0% per sec while charging on pad
# NOTE: TRAVEL_DRAIN_PER_METER and IDLE_DRAIN_PER_SEC are the runtime values the
# MotionController applies. The auction bid threshold in agent.py/FleetAgent.js
# (BATTERY_WARN = 20.0) is a separate cost-penalty threshold, not a charge state.
TRAVEL_DRAIN_PER_METER = 0.02    # % consumed per meter moved
IDLE_DRAIN_PER_SEC = 0.02        # % consumed per sec while standby


@dataclass
class BatteryState:
    level: float = BATTERY_FULL
    is_charging: bool = False

    def is_low(self) -> bool:
        return self.level <= BATTERY_WARN_THRESHOLD

    def is_critical(self) -> bool:
        return self.level <= BATTERY_CRITICAL_THRESHOLD

    def is_full(self) -> bool:
        return self.level >= BATTERY_FULL

    def discharge(self, distance_traveled: float, dt: float) -> float:
        if self.is_charging:
            return self.level
        drain = (distance_traveled * TRAVEL_DRAIN_PER_METER) + (dt * IDLE_DRAIN_PER_SEC)
        self.level = max(0.0, self.level - drain)
        return self.level

    def charge(self, dt: float) -> float:
        if self.is_charging:
            self.level = min(BATTERY_FULL, self.level + CHARGE_RATE_PER_SEC * dt)
        return self.level
