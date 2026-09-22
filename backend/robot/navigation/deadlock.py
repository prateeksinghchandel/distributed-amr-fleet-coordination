"""
deadlock.py — Detection and resolution of stalled multi-robot situations.

A robot is considered *stalled* when it has been asked to wait or avoid for a
worst-case period without making progress. When that stall exceeds the deadlock
threshold, the robot escalates and participates in a deterministic arbitration
over the shared fleet snapshot (see :mod:`reservation`). The holder of the
region keeps priority; the robot that arbitration picks as the *back-up* target
retreats along the reverse of its path until it clears the region, then
replans. This guarantees liveness: any head-on stall has exactly one designated
retreater, chosen by a total order.

Progress is measured geometrically each tick; a robot that has moved even a
small amount is considered non-stalled.
"""

from __future__ import annotations
import math

from robot.navigation.interfaces import DeadlockResolver, NavContext
from robot.navigation import reservation
from robot.navigation.reservation import _peer_id, _peer_pos

WAIT_STALL_S = 2.5            # waiting this long without progress => "stalled"
DEADLOCK_S = 4.0              # stalled this long => escalate to deadlock
BACKUP_S = 1.2                # how long a designated retreater backs up
BACKUP_SPEED_FRACTION = 0.35  # fraction of max speed while retreating
PROGRESS_EPS_M = 0.02
MAX_INTERVENTIONS_BEFORE_REPLAN = 20


class AlgorithmicDeadlockResolver(DeadlockResolver):
    """Wait-based deadlock detection and deterministic backup resolution."""

    def __init__(self, state, log):
        self.state = state
        self._log = log
        self._wait_time = 0.0
        self._backup_until = -float("inf")
        self._last_x = state.x
        self._last_y = state.y
        self._interventions_since_replan = 0
        self._now = 0.0

    def update(self, ctx: NavContext, nav_state: str, nav_reason,
               dt: float, now: float) -> dict:
        s = self.state
        self._now = now
        moved = math.hypot(s.x - self._last_x, s.y - self._last_y)
        self._last_x = s.x
        self._last_y = s.y

        if self._coordinated_wait(ctx, nav_state):
            # Waiting on a region that a peer still legitimately holds (claim or
            # occupancy). This is normal serialization, not a stall: keep the
            # waiting metric accruing but never escalate into replan storms.
            self._wait_time = 0.0
        elif moved >= PROGRESS_EPS_M or nav_state == "normal" or nav_state == "replanning":
            self._wait_time = 0.0
        elif s.speed < PROGRESS_EPS_M or nav_state in ("chokepoint_wait", "hard_stop", "deadlock"):
            self._wait_time += dt
        else:
            # Avoiding state with movement: still make progress, don't count.
            self._wait_time = 0.0

        if now < self._backup_until:
            # Mid-backup: keep retreating until the timer expires.
            vx, vy = self._backup_velocity(ctx)
            return {"deadlock": True, "state": "deadlock_backup",
                    "reason": "backup", "action": "backup",
                    "vx": vx, "vy": vy}

        if self._wait_time >= DEADLOCK_S:
            target = self._deadlock_target(ctx, nav_state, nav_reason)
            if target is not None:
                self._wait_time = 0.0
                self._backup_until = now + BACKUP_S
                self._interventions_since_replan += 1
                vx, vy = self._backup_velocity(ctx)
                self._log(f"{s.id} deadlock at ({s.x:.1f}, {s.y:.1f}) — "
                          f"{target} backs up")
                return {"deadlock": True, "state": "deadlock_backup",
                        "reason": "backup", "action": "backup", "target": target,
                        "vx": vx, "vy": vy}
            return {"deadlock": True, "state": "deadlock",
                    "reason": nav_reason or "stalled", "action": "replan",
                    "vx": 0.0, "vy": 0.0}

        if self._wait_time >= WAIT_STALL_S:
            return {"deadlock": False, "state": "stalled", "reason": nav_reason,
                    "action": None, "interventions": self._interventions_since_replan,
                    "vx": None, "vy": None}

        return {"deadlock": False, "state": nav_state, "reason": nav_reason,
                "action": None, "interventions": self._interventions_since_replan,
                "vx": None, "vy": None}

    def _coordinated_wait(self, ctx: NavContext, nav_state: str) -> bool:
        """True when this robot is waiting for a peer that still legitimately
        holds the shared region (an active reservation or physical occupancy)."""
        if nav_state != "chokepoint_wait":
            return False
        s = self.state
        for p in ctx.fleet:
            if _peer_id(p) == s.id or not p.get("online", True):
                continue
            nav = p.get("nav") or {}
            res = nav.get("reservation")
            if isinstance(res, dict) and res.get("robotId"):
                # Active claim (or a claim that will expire naturally) — the
                # holder is progressing or will release it.
                return True
        # No peer holds a reservation: check for physical occupancy of any region.
        if getattr(s, "map", None) is not None:
            for p in ctx.fleet:
                if _peer_id(p) == s.id or not p.get("online", True):
                    continue
                for region in s.map.regions:
                    if reservation.peer_inside(region, p):
                        return True
        return False

    def on_resolved(self, recovery_time: float) -> None:
        self._wait_time = 0.0
        self._backup_until = -float("inf")
        self._interventions_since_replan = 0

    def should_replan(self) -> bool:
        return self._interventions_since_replan >= MAX_INTERVENTIONS_BEFORE_REPLAN

    # ------------------------------------------------------------------
    # Decision helpers
    # ------------------------------------------------------------------

    def _deadlock_target(self, ctx: NavContext, nav_state: str, nav_reason) -> str | None:
        """Return the robot id that must back up, or None for a lone stall."""
        s = self.state
        me = {"robotId": s.id, "x": s.x, "y": s.y, "online": True,
              "heading": s.heading, "speed": s.speed,
              "nav": {"reservation": getattr(s, 'reservation', None) or {}}}
        peers = [p for p in ctx.fleet if _peer_id(p) and _peer_id(p) != s.id]

        # 1. A corridor region we share with a peer that is physically there.
        region = None
        if getattr(s, 'map', None) is not None:
            region = s.map.region_by_id(nav_reason) if nav_reason else None
        if region is None and getattr(s, 'map', None) is not None:
            px, py = _peer_pos(me)
            hits = s.map.regions_at(px, py)
            region = hits[0] if hits else None

        if region is not None:
            contenders = [me] + [
                p for p in peers if reservation.peer_inside(region, p) or
                reservation.active_claim(p, region.rid, now=self._now)
            ]
            holder = reservation.select_holder(region, contenders, self._now)
            if holder is not None and holder != s.id:
                return holder
            if holder == s.id:
                # We hold — a peer should back up instead. Pick the first
                # non-holder by the deterministic ordering.
                loser = reservation.select_backup(region, holder, contenders, self._now)
                if loser is not None:
                    return loser
            # Waiters outside a region: the holder proceeds, so just wait.
            return None

        # 2. No shared region — mutual avoidance wedge. Back up according to a
        #    deterministic rule independent of region geometry.
        blocked_by = [p for p in peers if self._closing_on(p, ctx)]
        if blocked_by:
            sorted_blocking = sorted(
                blocked_by, key=lambda p: (_peer_id(p),))
            return _peer_id(sorted_blocking[0]) if _peer_id(sorted_blocking[0]) != s.id else None
        return None

    def _closing_on(self, peer: dict, ctx: NavContext) -> bool:
        s = self.state
        px, py = _peer_pos(peer)
        return math.hypot(px - s.x, py - s.y) < 1.2

    def _backup_velocity(self, ctx: NavContext) -> tuple[float, float]:
        """Retreat along the reverse of the current path heading."""
        s = self.state
        path = s.current_path or []
        idx = min(s.path_index if hasattr(s, 'path_index') else 0, max(0, len(path) - 1))
        if path and idx < len(path):
            tx, ty = path[idx]
            dx = s.x - tx
            dy = s.y - ty
        else:
            dx = -math.cos(s.heading)
            dy = -math.sin(s.heading)
        norm = math.hypot(dx, dy) or 1.0
        speed = s.max_speed * BACKUP_SPEED_FRACTION
        return (dx / norm * speed, dy / norm * speed)