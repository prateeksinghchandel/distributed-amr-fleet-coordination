"""
safety.py — Physical safety layer (highest priority).

This stage is deliberately dumb and conservative: if any peer is predicted to
come inside the summed robot radii plus a small hard margin within the guard
horizon — or is already within that margin — the motion is overridden to a full
stop. Every other layer (avoidance, chokepoints, trajectory) is advisory; this
one is authoritative.
"""

from __future__ import annotations
import math

from robot.navigation.interfaces import SafetyController, NavContext
from robot.navigation.reservation import _peer_id, _peer_pos

GUARD_HORIZON_S = 0.8
GUARD_MARGIN_M = 0.06


def _peer_velocity(peer: dict) -> tuple[float, float]:
    nav = peer.get("nav") or {}
    vel = nav.get("velocity")
    if isinstance(vel, (list, tuple)) and len(vel) >= 2:
        return (float(vel[0]), float(vel[1]))
    heading = float(peer.get("heading", 0.0))
    speed = float(peer.get("speed", 0.0))
    return (speed * math.cos(heading), speed * math.sin(heading))


class AlgorithmicSafetyController(SafetyController):
    """Hard-stop guard against imminent peer collisions."""

    def __init__(self, state, log):
        self.state = state
        self._log = log
        self._stop_ticks = 0

    def compute(self, ctx: NavContext, desired_vx, desired_vy, now: float) -> dict:
        s = self.state
        peers = [p for p in ctx.fleet
                 if _peer_id(p) and _peer_id(p) != s.id and p.get("online", True)]

        for p in peers:
            px, py = _peer_pos(p)
            dist = math.hypot(px - s.x, py - s.y)
            sum_r = s.radius + float(p.get("radius", 0.4)) + GUARD_MARGIN_M
            if dist <= sum_r:
                self._stop_ticks += 1
                return self._result(0.0, 0.0, True, "imminent_contact")

            pvx, pvy = _peer_velocity(p)
            rel_vx = desired_vx - pvx
            rel_vy = desired_vy - pvy
            v2 = rel_vx * rel_vx + rel_vy * rel_vy
            if v2 <= 1e-9:
                continue
            dx = px - s.x
            dy = py - s.y
            tca = (dx * rel_vx + dy * rel_vy) / v2
            if tca < 0.0 or tca > GUARD_HORIZON_S:
                continue  # passing or already clear
            ex = s.x + (desired_vx - pvx) * tca
            ey = s.y + (desired_vy - pvy) * tca
            if math.hypot(px - ex, py - ey) <= sum_r:
                self._stop_ticks += 1
                return self._result(0.0, 0.0, True, "imminent_contact")

        self._stop_ticks = 0
        return self._result(desired_vx, desired_vy, False, None)

    def _result(self, vx, vy, stop, reason) -> dict:
        return {"vx": vx, "vy": vy, "state": "hard_stop" if stop else "normal",
                "reason": reason, "stop": bool(stop)}