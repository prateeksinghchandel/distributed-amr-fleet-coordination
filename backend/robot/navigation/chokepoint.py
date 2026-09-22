"""
chokepoint.py — Explicit region (corridor / intersection / terminal) coordination.

Wraps :class:`~robot.navigation.regionmap.RegionMap` and the distributed
reservation helpers. Each tick the coordinator decides, using only the shared
world geometry, the local path and the fleet snapshot:

* *claim ahead* — before entering a region the robot will cross, it verifies no
  peer is physically inside the region and no unexpired peer claim conflicts;
  when clear, it advertises its own reservation.
* *hold while inside* — the reservation is refreshed each tick until the robot
  leaves the region's bounding box.
* *release* — once the planned route no longer touches the region (or the robot
  is out of it), the reservation is cleared.
* *back off* — if arbitration designates this robot to give way inside a region
  (head-on), it reverses out (see deadlock layer for the final decision).

The coordinator never stops the robot by itself outside of a wait in front of
an occupied region: it only influences the *desired* velocity that upstream
stages may further constrain.
"""

from __future__ import annotations
import math
from typing import Optional

from robot.navigation.interfaces import ChokepointCoordinator, NavContext
from robot.navigation import reservation
from robot.navigation.regionmap import RegionMap
from robot.navigation.reservation import _peer_id, _peer_pos


class AlgorithmicChokepointCoordinator(ChokepointCoordinator):
    """Reservation-based corridor/intersection coordination."""

    def __init__(self, state, log, resolution: float = 0.5):
        self.state = state
        self._log = log
        self.resolution = resolution
        self.map: Optional[RegionMap] = None
        self._obstacles_token = None
        self._bounds = (0.0, 0.0)
        self._reservation: Optional[dict] = None
        self._last_region_id = None
        self._registered_goal: Optional[tuple] = None
        self._goal_terminal_rid: Optional[str] = None

    # ------------------------------------------------------------------
    # Terminal (station) coordination
    # ------------------------------------------------------------------

    @staticmethod
    def _terminal_rid(x: float, y: float) -> str:
        """Deterministic terminal id for a station point.

        The id is derived purely from the rounded coordinates so every robot
        targeting the same pickup/dropoff point (or sharing a delivery dock)
        derives the *identical* region id and geometry — reservations then
        arbitrate correctly without a central lock.
        """
        return f"GOAL-{x:.2f}-{y:.2f}"

    def _ensure_terminal(self, map_, rid: str, x: float, y: float) -> None:
        if map_.region_by_id(rid) is None:
            map_.add_terminal(rid, x, y)

    def _drop_goal_terminal(self, map_) -> None:
        rid = self._goal_terminal_rid
        if rid is None:
            return
        map_.regions = [r for r in map_.regions if r.rid != rid]
        map_._terminal_zones = [t for t in map_._terminal_zones if t.rid != rid]
        self._goal_terminal_rid = None
        self._registered_goal = None

    def _sync_goal_terminal(self, goal: Optional[tuple]) -> None:
        """Register the station point (route endpoint) the robot is heading to.

        Called every tick with the goal; drops the previously registered
        terminal whenever the goal changes (e.g. pickup -> dropoff) so stale
        station reservations never linger.
        """
        map_ = self.map
        if map_ is None:
            return
        goal = tuple(goal) if goal is not None else None
        rid = self._terminal_rid(goal[0], goal[1]) if goal is not None else None
        if rid == self._goal_terminal_rid:
            # Same station (or map rebuilt since registration) — keep it present.
            if rid is not None and map_.region_by_id(rid) is None and goal is not None:
                map_.add_terminal(rid, goal[0], goal[1])
            return
        self._drop_goal_terminal(map_)
        if goal is not None:
            self._ensure_terminal(map_, rid, goal[0], goal[1])
            self._goal_terminal_rid = rid
            self._registered_goal = goal

    # ------------------------------------------------------------------
    # Map lifecycle
    # ------------------------------------------------------------------

    def _ensure_map(self, ctx: NavContext) -> None:
        bounds = ctx.bounds
        w = float(bounds.get("width", 30.0)) if bounds else 30.0
        h = float(bounds.get("height", 20.0)) if bounds else 20.0
        obstacles = ctx.obstacles
        token = f"{round(w, 2)}x{round(h, 2)}:{len(obstacles)}"
        # A token on obstacle count is cheap and deterministic; the revision
        # tracker would need obstacle ids which raw Rect lists do not carry.
        if self._obstacles_token != token:
            self.map = RegionMap(w, h, obstacles, resolution=self.resolution,
                                 robot_radius=self.state.radius)
            self._obstacles_token = token
            self._bounds = (w, h)
            self._goal_terminal_rid = None
            # Terminal zones: register charging pads and delivery docks so they
            # seed intersection detection.
            for pad in (ctx.bounds.get("chargingPads", []) if ctx.bounds else []):
                px = float(pad.get("x", 0.0))
                py = float(pad.get("y", 0.0))
                sp = pad.get("spawnPoint")
                if isinstance(sp, dict) and "x" in sp and "y" in sp:
                    px = float(sp["x"])
                    py = float(sp["y"])
                self.map.add_terminal(f"TERM-{pad.get('id', '')}", px, py)
            for dock in (ctx.bounds.get("deliveryDocks", []) if ctx.bounds else []):
                dp = dock.get("dropoffPoint")
                if not isinstance(dp, dict):
                    continue
                dx = float(dp.get("x", 0.0))
                dy = float(dp.get("y", 0.0))
                self.map.add_terminal(self._terminal_rid(dx, dy), dx, dy)
            # Re-register the active station terminal on the fresh map.
            goal = self._registered_goal
            if goal is not None:
                rid = self._terminal_rid(goal[0], goal[1])
                if self.map.region_by_id(rid) is None:
                    self.map.add_terminal(rid, goal[0], goal[1])
                self._goal_terminal_rid = rid

    # ------------------------------------------------------------------
    # ChokepointCoordinator interface
    # ------------------------------------------------------------------

    def tick(self, ctx: NavContext, path: list, path_index: int, now: float) -> dict:
        s = self.state
        self._ensure_map(ctx)
        # Keep the station point the robot is heading to registered as a
        # terminal zone (dropped/re-added as the goal changes). This makes
        # robots converging on the same pickup/dropoff point serialize.
        self._sync_goal_terminal(tuple(path[-1]) if path else None)
        map_ = self.map
        if map_ is None or not map_.regions:
            self._reservation = None
            return self._result("normal", None, None, s.speed if s.speed else 0.0, 0.0)

        # Region this robot would enter next along its stored path. Build the
        # query from the *current* position so a two-waypoint path (start,
        # goal) still sees regions ahead after ``path_index`` has advanced.
        ahead = [(s.x, s.y)] + [tuple(p) for p in path[max(0, path_index):]]
        region = map_.first_region_on_path(ahead, 0) if ahead else None
        inside = None
        if region is not None:
            inside = region.contains(s.x, s.y, pad=0.0)

        if region is None:
            # Nothing ahead — release any held reservation.
            self._reservation = None
            return self._result("normal", None, None, s.speed, 0.0)

        rid = region.rid
        self._last_region_id = rid

        if inside:
            return self._inside_region(ctx, region, now)

        # Not inside yet: only act when we are actually approaching it.
        dist_ahead = self._distance_to_region(ctx, ahead, 0, region)
        if dist_ahead > reservation.RES_LOOKAHEAD_M:
            # Too far to start reserving; keep current velocity (already
            # checked against earlier regions).
            self._reservation = None
            return self._result("normal", None, None, s.speed, 0.0)

        if self._occupied_by_peer(ctx, region):
            self._reservation = None
            return self._result("chokepoint_wait", "wait_occupied",
                                rid, 0.0, dist_ahead)

        if self._peer_conflict(ctx, region, now) and \
                self._arbitrate(ctx, region, now) != s.id:
            # A peer owns this region (or both of us claimed the same tick).
            # Arbitration picks exactly one winner from the shared snapshot so
            # the loser waits while the winner proceeds — never both retreating
            # (that livelocks at the mouth).
            self._reservation = None
            return self._result("chokepoint_wait", "wait_reserved",
                                rid, 0.0, dist_ahead)

        # Clear to enter: (re)claim.
        priority = int(getattr(s, 'task_priority', 1) or 1)
        eta = self._eta_to_exit(region, dist_ahead, s.speed)
        self._reservation = reservation.claim_dict(
            rid, s.id, now, now + eta, priority, dist_ahead)
        return self._result("chokepoint_entering", None, rid, s.speed, dist_ahead)

    # ------------------------------------------------------------------
    # Region enter / hold / release behavior
    # ------------------------------------------------------------------

    def _inside_region(self, ctx: NavContext, region, now: float) -> dict:
        s = self.state
        priority = int(getattr(s, 'task_priority', 1) or 1)
        if self._reservation is None or self._reservation.get("regionId") != region.rid:
            dist_ahead = self._distance_to_region(ctx, [] if not s.current_path else s.current_path,
                                                  0, region)
            self._reservation = reservation.claim_dict(
                region.rid, s.id, now, now + self._eta_to_exit(region, 0.0, s.speed),
                priority, max(dist_ahead, 0.0))
        else:
            # Refresh the expected-exit so it never silently expires while we
            # are physically inside.
            self._reservation["expectedExit"] = round(now + self._eta_to_exit(region, 0.0, s.speed), 3)
        state = "chokepoint_inside"
        return self._result(state, None, region.rid, s.speed, 0.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _result(self, state, reason, region_id, keep_speed, dist_ahead) -> dict:
        return {
            "state": state,
            "reason": reason,
            "regionId": region_id,
            "reservation": dict(self._reservation) if self._reservation else None,
            "speed": float(keep_speed),
            "distanceAhead": float(dist_ahead),
        }

    def _distance_to_region(self, ctx: NavContext, path, path_index, region) -> float:
        """Distance from the current position to the region entry along path."""
        s = self.state
        if not path:
            return math.hypot(region.center[0] - s.x, region.center[1] - s.y)
        cum = 0.0
        pts = path[max(0, path_index):]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if region.intersects(ax, ay, bx, by, pad=0.0):
                sub = self._segment_entry((ax, ay), (bx, by), region)
                return cum + sub
            cum += math.hypot(bx - ax, by - ay)
        return cum

    def _segment_entry(self, a, b, region) -> float:
        """Distance along a path segment to the first crossing of the region."""
        ax, ay = a
        bx, by = b
        length = math.hypot(bx - ax, by - ay)
        if length <= 1e-9:
            return 0.0
        steps = max(2, math.ceil(length / 0.25))
        for i in range(1, steps + 1):
            t = i / steps
            px = ax + (bx - ax) * t
            py = ay + (by - ay) * t
            if region.contains(px, py, pad=0.0):
                return t * length
        return length

    def _occupied_by_peer(self, ctx: NavContext, region) -> bool:
        s = self.state
        for p in ctx.fleet:
            if _peer_id(p) == s.id or not p.get("online", True):
                continue
            if reservation.peer_inside(region, p, pad=reservation.REGION_OCCUPANCY_PAD_M):
                return True
        return False

    def _peer_conflict(self, ctx: NavContext, region, now) -> bool:
        s = self.state
        for p in ctx.fleet:
            rid = _peer_id(p)
            if rid == s.id or not p.get("online", True):
                continue
            if reservation.active_claim(p, region.rid, now):
                return True
        return False

    def _arbitrate(self, ctx: NavContext, region, now) -> str:
        """Deterministic winner of a region among the current contenders.

        Contenders are ``self`` plus every online peer that either holds an
        active claim for the region or is physically inside it. All robots run
        this pure function over the same fleet snapshot, so they converge on the
        same holder without a central server.
        """
        s = self.state
        contenders = [{
            "robotId": s.id, "x": s.x, "y": s.y, "online": True,
            "nav": {"reservation": self._reservation or {}},
        }]
        for p in ctx.fleet:
            rid = _peer_id(p)
            if rid == s.id or not p.get("online", True):
                continue
            if reservation.active_claim(p, region.rid, now) or \
                    reservation.peer_inside(region, p):
                contenders.append(p)
        winner = reservation.select_holder(region, contenders, now)
        return winner if winner else s.id

    def _eta_to_exit(self, region, dist_ahead, current_speed) -> float:
        speed = max(current_speed, 0.3)
        span = 0.0
        if region.bbox:
            span = math.hypot(region.bbox[2] - region.bbox[0],
                              region.bbox[3] - region.bbox[1])
        return (dist_ahead / speed) + (span / max(speed, 1e-3)) + reservation.RES_GRACE_S