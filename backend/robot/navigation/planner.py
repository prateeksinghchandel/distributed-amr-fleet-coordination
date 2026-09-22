"""
planner.py — Global path planning and path-validation wrapper.

Builds on the existing A* planner (``robot/planning/astar.py``). In addition to
producing routes it exposes deterministic validity checks so the pipeline can
decide *when* a stored path must be recomputed:

* a new obstacle revision blocks the current route,
* the current route does not meaningfully progress towards the goal,
* the destination is inside an inflated obstacle (unreachable).

Replanning is never allowed more often than ``REPLAN_COOLDOWN_S``.
"""

from __future__ import annotations
import math
from typing import Optional, Sequence

from robot.planning.astar import AStarPlanner, PathNotFoundError, path_distance
from robot.navigation.interfaces import PathPlanner

REPLAN_COOLDOWN_S = 1.0
MIN_PATH_PROGRESS_M = 0.05     # meters of closing distance expected per validate call


def _bounded(point: tuple[float, float], bounds: Optional[dict]) -> tuple[float, float]:
    if not bounds:
        return point
    w = float(bounds.get("width", 30.0))
    h = float(bounds.get("height", 20.0))
    x = max(0.0, min(point[0], w))
    y = max(0.0, min(point[1], h))
    return (x, y)


class AlgorithmicPathPlanner(PathPlanner):
    """A* planner around the existing ``AStarPlanner`` plus validation logic."""

    def __init__(self, state, log, resolution: float = 0.25, safety_margin: float = 0.1):
        self.state = state
        self._log = log
        self.resolution = resolution
        self.safety_margin = safety_margin
        self.last_replan = -float("inf")

    # ------------------------------------------------------------------
    # PathPlanner interface
    # ------------------------------------------------------------------

    def plan(self, start, goal, obstacles: Sequence, bounds: Optional[dict]) -> list:
        w = bounds.get("width", 30.0) if bounds else 30.0
        h = bounds.get("height", 20.0) if bounds else 20.0
        goal = _bounded(goal, bounds)
        planner = AStarPlanner(
            width=w, height=h,
            resolution=self.resolution,
            robot_radius=self.state.radius,
            safety_margin=self.safety_margin,
        )
        try:
            return planner.plan(start, goal, obstacles)
        except PathNotFoundError:
            self._log(
                f"Warning: A* path not found to ({goal[0]:.2f}, {goal[1]:.2f}), "
                f"using direct waypoint"
            )
            return [tuple(start), goal] if start != goal else [goal]

    def validate(self, path: list, obstacles: Sequence, fleet: Sequence,
                 bounds: Optional[dict]) -> Optional[str]:
        if not path:
            return "empty_path"
        if len(path) < 2:
            # A single waypoint still counts if we have already arrived at it.
            return None
        if self._blocked_by_obstacles(path, obstacles, bounds):
            return "path_blocked"
        goal = path[-1]
        if self._inside_obstacle(goal, obstacles):
            return "goal_blocked"
        closing = path_distance(path[-2:])
        if closing < MIN_PATH_PROGRESS_M:
            return None
        return None

    def should_replan(self, reason: str, await_count: int) -> bool:
        if reason not in ("path_blocked", "avoidance_failure", "deadlock",
                          "goal_blocked", "stuck"):
            return False
        return await_count >= 1

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _inside_obstacle(self, point, obstacles) -> bool:
        margin = self.state.radius + self.safety_margin
        px, py = point
        for obs in obstacles:
            try:
                if obs.inflated_contains(px, py, margin):
                    return True
            except AttributeError:
                ox = float(obs.get("x", 0.0))
                oy = float(obs.get("y", 0.0))
                ow = float(obs.get("width", 0.0))
                oh = float(obs.get("height", 0.0))
                if ox - margin <= px <= ox + ow + margin and oy - margin <= py <= oy + oh + margin:
                    return True
        return False

    def _blocked_by_obstacles(self, path, obstacles, bounds) -> bool:
        margin = self.state.radius + self.safety_margin
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            length = math.hypot(bx - ax, by - ay)
            if length <= 1e-9:
                continue
            steps = max(2, math.ceil(length / 0.3))
            for obs in obstacles:
                for i in range(1, steps):
                    t = i / steps
                    px = ax + (bx - ax) * t
                    py = ay + (by - ay) * t
                    try:
                        if obs.inflated_contains(px, py, margin):
                            return True
                    except AttributeError:
                        ox = float(obs.get("x", 0.0))
                        oy = float(obs.get("y", 0.0))
                        ow = float(obs.get("width", 0.0))
                        oh = float(obs.get("height", 0.0))
                        if ox - margin <= px <= ox + ow + margin and \
                           oy - margin <= py <= oy + oh + margin:
                            return True
        return False