"""
pipeline.py — The deterministic algorithmic navigation pipeline.

Orchestrates the replaceable stages into a single per-tick update::

    Global path validation & replan
        -> Local collision avoidance (velocity sampling)
        -> Chokepoint coordination (reservations)
        -> Deadlock detection / resolution
        -> Safety (hard-stop)
        -> Trajectory control (kinematic limits)
        -> State integration

The pipeline owns navigation-level metrics and exposes them on the robot state
so the telemetry and dashboard layers can report them without new topics.
"""

from __future__ import annotations
import math
from typing import Optional, Sequence

from robot.battery import TRAVEL_DRAIN_PER_METER
from robot.navigation.interfaces import NavContext
from robot.navigation.planner import AlgorithmicPathPlanner, REPLAN_COOLDOWN_S
from robot.navigation.avoidance import AlgorithmicCollisionAvoidance
from robot.navigation.chokepoint import AlgorithmicChokepointCoordinator
from robot.navigation.trajectory import AlgorithmicTrajectoryController
from robot.navigation.safety import AlgorithmicSafetyController
from robot.navigation.deadlock import AlgorithmicDeadlockResolver, PROGRESS_EPS_M

ARRIVE_THRESHOLD = 0.15
LOOKAHEAD_DIST = 0.6
STUCK_S = 2.0
AVOIDANCE_STALL_S = 2.0
DESIRED_SPEED_RAMP = 0.6   # meters over which approach speed ramps down


class AlgorithmicNavPipeline:
    """Per-robot navigation pipeline with replaceable stages."""

    def __init__(self, state, log, resolution: float = 0.25, safety_margin: float = 0.1):
        self.state = state
        self._log = log
        self.resolution = resolution
        self.safety_margin = safety_margin

        self.planner = AlgorithmicPathPlanner(state, log, resolution, safety_margin)
        self.avoidance = AlgorithmicCollisionAvoidance(state, log, safety_margin)
        self.chokepoint = AlgorithmicChokepointCoordinator(state, log, resolution=0.5)
        self.deadlock = AlgorithmicDeadlockResolver(state, log)
        self.safety = AlgorithmicSafetyController(state, log)
        self.trajectory = AlgorithmicTrajectoryController(state, log)

        self._last_replan = -float("inf")
        self._obstacles_token = None
        self._stuck_since = None
        self._avoid_stall_since = None
        self._goal = (0.0, 0.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, obstacles: list, bounds: Optional[dict]) -> None:
        """Detect world-geometry changes that invalidate stored paths.

        The stored path is re-validated against the new obstacle set on the next
        tick by ``planner.validate``; no immediate replan is forced here because
        a geometry tweak rarely blocks the current route.
        """
        w = bounds.get("width", 30.0) if bounds else 30.0
        h = bounds.get("height", 20.0) if bounds else 20.0
        self._obstacles_token = f"{round(w, 2)}x{round(h, 2)}:{len(obstacles)}"

    def set_goal(self, goal_x: float, goal_y: float) -> None:
        self._goal = (goal_x, goal_y)

    def step(self, dt: float, obstacles: list, bounds: Optional[dict],
             fleet: Sequence, now: float, goal_override: Optional[tuple] = None) -> dict:
        """Advance the pipeline one tick and apply motion to the robot state."""
        s = self.state
        self.refresh(obstacles, bounds)
        if goal_override is not None:
            self._goal = goal_override
        goal = tuple(self._goal) if not isinstance(self._goal, tuple) else tuple(self._goal)
        ctx = NavContext(s, fleet, obstacles, bounds)

        path = list(s.current_path or [])
        idx = max(0, int(s.path_index))

        # 1. Path validation / replanning decisions.
        if path:
            reason = self.planner.validate(path, obstacles, fleet, bounds)
            if reason is not None and self._can_replan(now):
                self._replan(ctx, goal, now, reason)
                path = list(s.current_path or [])
                idx = max(0, int(s.path_index))
                if path:
                    s.nav_state = "replanning"
                    s.nav_reason = reason
                    return self._settle(dt, 0.0, 0.0)

        if not path:
            s.nav_state = "normal"
            s.nav_reason = None
            return self._settle(dt, 0.0, 0.0, arrived=True)

        # 2. Waypoint targeting (pure-pursuit lookahead).
        idx = self._advance_index(s.x, s.y, path, idx)
        target = path[idx]
        dist_t = math.hypot(target[0] - s.x, target[1] - s.y)
        if idx == len(path) - 1 and dist_t <= ARRIVE_THRESHOLD:
            s.nav_state = "normal"
            s.nav_reason = None
            s.path_index = idx
            return self._settle(dt, 0.0, 0.0, arrived=True)

        desired_speed = self._desired_speed(target, dist_t)
        pref_vx = (target[0] - s.x) / max(dist_t, 1e-6) * desired_speed
        pref_vy = (target[1] - s.y) / max(dist_t, 1e-6) * desired_speed

        # 3. Local avoidance.
        avoid = self.avoidance.compute(ctx, target, pref_vx, pref_vy, now)
        vx, vy = avoid["vx"], avoid["vy"]
        s.nav_state = avoid["state"]
        s.nav_reason = avoid["reason"]
        self._metrics["near_collisions"] += 1 if avoid["near_collision"] else 0
        if avoid["intervention"]:
            self._metrics["interventions"] += 1

        # 4. Chokepoint coordination after avoidance (reservations gate first).
        chokepoint_result = self.chokepoint.tick(ctx, path, idx, now)
        s.reservation = chokepoint_result.get("reservation")
        if chokepoint_result.get("state") in ("chokepoint_wait",):
            vx = 0.0
            vy = 0.0
            s.nav_state = "chokepoint_wait"
            s.nav_reason = chokepoint_result["reason"]
        elif chokepoint_result.get("state") == "chokepoint_entering":
            if s.nav_state == "normal":
                s.nav_state = "chokepoint_entering"

        # 5. Deadlock detection / resolution.
        dl = self.deadlock.update(ctx, s.nav_state, s.nav_reason, dt, now)
        if dl["deadlock"]:
            if dl.get("action") == "backup":
                vx, vy = dl["vx"], dl["vy"]
                s.nav_state = "deadlock_backup"
                s.nav_reason = dl["reason"]
            else:
                s.nav_state = "deadlock"
                s.nav_reason = dl["reason"]
                if self._can_replan(now):
                    self._replan(ctx, goal, now, "deadlock")
                    path = list(s.current_path or [])
                    idx = max(0, int(s.path_index))
                    if path:
                        s.nav_state = "replanning"
                        return self._settle(dt, 0.0, 0.0)
                vx, vy = 0.0, 0.0
        elif dl.get("state") == "stalled":
            if s.nav_state == "normal":
                s.nav_state = "stalled"
            s.nav_reason = dl["reason"]

        # 6. Safety layer (authoritative).
        safe = self.safety.compute(ctx, vx, vy, now)
        if safe["stop"]:
            s.nav_state = "hard_stop"
            s.nav_reason = safe["reason"]
            self._metrics["safety_stops"] += 1
            self._metrics["near_collisions"] += 1
            vx, vy = 0.0, 0.0

        # 7. Trajectory control + integration.
        tx, ty, speed, heading = self.trajectory.step(ctx, vx, vy, dt)
        travelled = math.hypot(tx, ty) * dt
        s.x += tx * dt
        s.y += ty * dt
        s.heading = heading
        s.speed = math.hypot(tx, ty)
        s.vx = tx
        s.vy = ty
        s.battery = max(0.0, s.battery - TRAVEL_DRAIN_PER_METER * travelled)
        s.path_index = idx

        # Progress-based counters.
        self._metrics["distance"] += travelled
        if travelled < PROGRESS_EPS_M and desired_speed > 0.05:
            if s.nav_state in ("avoiding", "chokepoint_wait", "hard_stop",
                               "stalled", "deadlock", "deadlock_backup"):
                self._metrics["waiting_time"] += dt
                if self._avoid_stall_since is None:
                    self._avoid_stall_since = now
                elif s.nav_state == "avoiding" and now - self._avoid_stall_since >= AVOIDANCE_STALL_S:
                    if self._can_replan(now):
                        self._replan(ctx, goal, now, "avoidance_failure")
                        path = list(s.current_path or [])
                        s.path_index = max(0, int(s.path_index))
                        if path:
                            s.nav_state = "replanning"
                            s.nav_reason = "avoidance_failure"
                            return self._settle(dt, 0.0, 0.0)
            else:
                self._avoid_stall_since = None
                if self._stuck_since is None:
                    self._stuck_since = now
                elif now - self._stuck_since >= STUCK_S:
                    if self._can_replan(now):
                        self._replan(ctx, goal, now, "stuck")
                        path = list(s.current_path or [])
                        s.path_index = max(0, int(s.path_index))
                        if path:
                            s.nav_state = "replanning"
                            s.nav_reason = "stuck"
                            return self._settle(dt, 0.0, 0.0)
        else:
            self._stuck_since = None
            self._avoid_stall_since = None

        result = self._settle(dt, tx, ty, arrived=False)
        return result

    # ------------------------------------------------------------------
    # Replanning
    # ------------------------------------------------------------------

    def _can_replan(self, now: float) -> bool:
        return now - self._last_replan >= REPLAN_COOLDOWN_S

    def _replan(self, ctx: NavContext, goal, now: float, reason: str) -> None:
        s = self.state
        try:
            new_path = self.planner.plan((s.x, s.y), goal, ctx.obstacles, ctx.bounds)
        except Exception as exc:  # defensive: never crash the robot loop
            self._log(f"{s.id} replan failed ({reason}): {exc}")
            return
        if not new_path:
            self._log(f"{s.id} replan ({reason}) produced no path")
            return
        if s.current_path and [list(p) for p in new_path] == [list(p) for p in s.current_path]:
            # Nothing would change — don't burn a cooldown or a counter on a
            # meaningless replan (prevents replan churn under congestion).
            return
        self._last_replan = now
        self._metrics["replan_count"] += 1
        s.nav_reason = reason
        s.nav_state = "replanning"
        s.current_path = new_path
        s.path_index = 0
        self._log(
            f"{s.id} replanning ({reason}): {len(new_path)} waypoints "
            f"towards ({goal[0]:.1f}, {goal[1]:.1f})"
        )
        self.deadlock.on_resolved(self._metrics["waiting_time"])

    # ------------------------------------------------------------------
    # Integration helpers
    # ------------------------------------------------------------------

    def _advance_index(self, x: float, y: float, path: list, idx: int) -> int:
        while idx < len(path) - 1 and math.hypot(path[idx][0] - x, path[idx][1] - y) <= ARRIVE_THRESHOLD:
            idx += 1
        return idx

    def _desired_speed(self, target, dist_t: float) -> float:
        s = self.state
        if dist_t < DESIRED_SPEED_RAMP:
            return max(0.0, min(s.max_speed, s.max_speed * dist_t / DESIRED_SPEED_RAMP))
        return s.max_speed

    def _settle(self, dt: float, vx: float, vy: float, arrived: bool = False) -> dict:
        s = self.state
        return {
            "arrived": arrived,
            "vx": vx,
            "vy": vy,
            "speed": s.speed,
            "navState": s.nav_state,
            "navReason": s.nav_reason,
            "reservation": s.reservation,
            "metrics": dict(self._metrics),
        }

    @property
    def _metrics(self) -> dict:
        return self.state.metrics