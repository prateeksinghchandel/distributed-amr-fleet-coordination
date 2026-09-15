"""Simple smooth waypoint follower used by the Python AMR controller."""
from __future__ import annotations
import math


class WaypointFollower:
    def __init__(self, lookahead: float = 0.2, arrival_threshold: float = 0.15):
        self.lookahead = max(0.05, lookahead)
        self.arrival_threshold = max(0.01, arrival_threshold)

    def step(self, position, waypoints, index, speed, dt):
        """Return (x, y, heading, new_index, arrived, travelled)."""
        if not waypoints or index >= len(waypoints):
            return position[0], position[1], 0.0, index, True, 0.0
        x, y = position
        i = index

        # If already at or past the current target waypoint, advance to the next
        while i < len(waypoints) - 1 and math.dist((x, y), waypoints[i]) <= self.arrival_threshold:
            i += 1

        tx, ty = waypoints[i]
        dx, dy = tx - x, ty - y
        distance = math.hypot(dx, dy)
        if distance <= self.arrival_threshold:
            is_final = (i == len(waypoints) - 1)
            return tx, ty, math.atan2(dy, dx) if distance else 0.0, i + 1, is_final, distance

        heading = math.atan2(dy, dx)
        step = min(max(0.0, speed) * max(dt, 0.0), distance)
        nx = x + dx / distance * step
        ny = y + dy / distance * step
        arrived = (i == len(waypoints) - 1 and math.hypot(tx - nx, ty - ny) <= self.arrival_threshold)
        return nx, ny, heading, i, arrived, step
