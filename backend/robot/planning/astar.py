"""Grid-based A* global planner for static warehouse obstacles.

The planner uses an 8-connected occupancy grid, inflates obstacles by the
AMR footprint plus a configurable safety margin, and then performs a
line-of-sight pass to remove unnecessary grid waypoints.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Iterable, Sequence


class PathNotFoundError(RuntimeError):
    """Raised when no collision-free path exists between start and goal."""


@dataclass(frozen=True)
class GridConfig:
    resolution: float = 0.25
    robot_radius: float = 0.4
    safety_margin: float = 0.1


class AStarPlanner:
    """A* planner over a bounded 2-D occupancy grid."""

    DIAGONAL = math.sqrt(2.0)
    MOVES = (
        (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
        (-1, -1, DIAGONAL), (-1, 1, DIAGONAL),
        (1, -1, DIAGONAL), (1, 1, DIAGONAL),
    )

    def __init__(self, width: float, height: float, *, resolution: float = 0.25,
                 robot_radius: float = 0.4, safety_margin: float = 0.1):
        if width <= 0 or height <= 0:
            raise ValueError("warehouse dimensions must be positive")
        if resolution <= 0:
            raise ValueError("resolution must be positive")
        if robot_radius < 0 or safety_margin < 0:
            raise ValueError("robot_radius and safety_margin cannot be negative")
        self.width = float(width)
        self.height = float(height)
        self.config = GridConfig(resolution, robot_radius, safety_margin)
        self.cols = max(1, math.ceil(self.width / resolution))
        self.rows = max(1, math.ceil(self.height / resolution))
        self._margin = robot_radius + safety_margin

    def plan(self, start: tuple[float, float], goal: tuple[float, float],
             obstacles: Sequence[object] = (), *, smooth: bool = True) -> list[tuple[float, float]]:
        """Return a collision-free path including start and goal."""
        self._validate_point(start, "start")
        self._validate_point(goal, "goal")
        if math.dist(start, goal) <= self.config.resolution * 0.5:
            return [goal]

        blocked = self._blocked_cells(obstacles)
        start_cell = self._cell_for_point(start)
        goal_cell = self._cell_for_point(goal)

        # Endpoints must remain usable even when the obstacle inflation touches
        # a grid cell elsewhere in the warehouse.
        if start_cell in blocked:
            raise PathNotFoundError("start lies inside an inflated obstacle")
        if goal_cell in blocked:
            raise PathNotFoundError("goal lies inside an inflated obstacle")

        cells = self._astar(start_cell, goal_cell, blocked)
        if cells is None:
            raise PathNotFoundError(f"no path from {start} to {goal}")

        raw = [start]
        raw.extend(self._point_for_cell(c) for c in cells[1:-1])
        raw.append(goal)
        if smooth:
            return self.smooth_path(raw, obstacles)
        return raw

    def distance(self, start: tuple[float, float], goal: tuple[float, float],
                 obstacles: Sequence[object] = ()) -> float:
        """Return geometric length of the planned path."""
        return path_distance(self.plan(start, goal, obstacles))

    def smooth_path(self, points: Sequence[tuple[float, float]],
                    obstacles: Sequence[object] = ()) -> list[tuple[float, float]]:
        """Greedily skip intermediate points while maintaining line of sight."""
        if len(points) <= 2:
            return list(points)
        result = [points[0]]
        anchor = 0
        while anchor < len(points) - 1:
            furthest = anchor + 1
            for candidate in range(anchor + 2, len(points)):
                if self.segment_is_free(points[anchor], points[candidate], obstacles):
                    furthest = candidate
                else:
                    break
            result.append(points[furthest])
            anchor = furthest
        return result

    def segment_is_free(self, start: tuple[float, float], goal: tuple[float, float],
                        obstacles: Sequence[object]) -> bool:
        """Check a segment against inflated rectangular obstacles."""
        margin = self._margin
        for obs in obstacles:
            x, y, w, h = _rect_values(obs)
            if _segment_intersects_rect(start, goal, x - margin, y - margin,
                                        x + w + margin, y + h + margin):
                return False
        return True

    def _astar(self, start: tuple[int, int], goal: tuple[int, int],
               blocked: set[tuple[int, int]]) -> list[tuple[int, int]] | None:
        open_heap: list[tuple[float, float, tuple[int, int]]] = []
        heapq.heappush(open_heap, (self._heuristic(start, goal), 0.0, start))
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score = {start: 0.0}
        closed: set[tuple[int, int]] = set()

        while open_heap:
            _, g_current, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            closed.add(current)
            if current == goal:
                return self._reconstruct(came_from, current)

            for dx, dy, move_cost in self.MOVES:
                nxt = (current[0] + dx, current[1] + dy)
                if not self._in_bounds(nxt) or nxt in blocked or nxt in closed:
                    continue
                # Do not allow diagonal corner cutting through two blocked cells.
                if dx and dy and ((current[0] + dx, current[1]) in blocked or
                                  (current[0], current[1] + dy) in blocked):
                    continue
                tentative = g_current + move_cost
                if tentative < g_score.get(nxt, float("inf")):
                    g_score[nxt] = tentative
                    came_from[nxt] = current
                    f = tentative + self._heuristic(nxt, goal)
                    heapq.heappush(open_heap, (f, tentative, nxt))
        return None

    @staticmethod
    def _reconstruct(came_from, current):
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def _blocked_cells(self, obstacles: Sequence[object]) -> set[tuple[int, int]]:
        blocked: set[tuple[int, int]] = set()
        margin = self._margin
        for obs in obstacles:
            x, y, w, h = _rect_values(obs)
            min_c = max(0, math.floor((x - margin) / self.config.resolution))
            max_c = min(self.cols - 1, math.floor((x + w + margin) / self.config.resolution))
            min_r = max(0, math.floor((y - margin) / self.config.resolution))
            max_r = min(self.rows - 1, math.floor((y + h + margin) / self.config.resolution))
            for row in range(min_r, max_r + 1):
                for col in range(min_c, max_c + 1):
                    cx, cy = self._point_for_cell((col, row))
                    if x - margin <= cx <= x + w + margin and y - margin <= cy <= y + h + margin:
                        blocked.add((col, row))
        return blocked

    def _cell_for_point(self, point):
        x, y = point
        return (min(self.cols - 1, max(0, math.floor(x / self.config.resolution))),
                min(self.rows - 1, max(0, math.floor(y / self.config.resolution))))

    def _point_for_cell(self, cell):
        col, row = cell
        return ((col + 0.5) * self.config.resolution,
                (row + 0.5) * self.config.resolution)

    def _validate_point(self, point, name):
        if len(point) != 2 or not all(math.isfinite(float(v)) for v in point):
            raise ValueError(f"{name} must be a finite (x, y) point")
        x, y = point
        if not (0 <= x <= self.width and 0 <= y <= self.height):
            raise ValueError(f"{name} ({x}, {y}) is outside warehouse bounds")

    def _in_bounds(self, cell):
        c, r = cell
        return 0 <= c < self.cols and 0 <= r < self.rows

    @staticmethod
    def _heuristic(a, b):
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        return max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)


def path_distance(path: Iterable[tuple[float, float]]) -> float:
    points = list(path)
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _rect_values(obs):
    if isinstance(obs, dict):
        return float(obs["x"]), float(obs["y"]), float(obs["width"]), float(obs["height"])
    return float(obs.x), float(obs.y), float(obs.width), float(obs.height)


def _segment_intersects_rect(a, b, xmin, ymin, xmax, ymax):
    """Liang-Barsky segment/AABB test, including boundary contact."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - xmin), (dx, xmax - a[0]),
                 (-dy, a[1] - ymin), (dy, ymax - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t0 <= t1
