"""
controller.py — Motion controller and robot state machine for a Python AMR.

Mirrors Robot.js update() / state machine logic:
  IDLE → MOVING_TO_PICKUP → PICKING → MOVING_TO_DROPOFF → DROPPING → COMPLETED

Battery drains during movement and charges at rest.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# State constants (mirror ROBOT_STATUS in Robot.js)
# ---------------------------------------------------------------------------

class Status:
    IDLE = "IDLE"
    MOVING_TO_PICKUP = "MOVING_TO_PICKUP"
    PICKING = "PICKING"
    MOVING_TO_DROPOFF = "MOVING_TO_DROPOFF"
    DROPPING = "DROPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CHARGING = "CHARGING"


# ---------------------------------------------------------------------------
# Obstacle AABB (thin wrapper around obstacle dicts from world/state)
# ---------------------------------------------------------------------------

@dataclass
class ObstacleRect:
    id: str
    x: float
    y: float
    width: float
    height: float

    def inflated_contains(self, px: float, py: float, margin: float = 0.35) -> bool:
        return (self.x - margin <= px <= self.x + self.width + margin and
                self.y - margin <= py <= self.y + self.height + margin)


# ---------------------------------------------------------------------------
# RobotState — mutable robot data
# ---------------------------------------------------------------------------

@dataclass
class RobotState:
    id: str
    x: float
    y: float
    heading: float = 0.0
    speed: float = 0.0
    max_speed: float = 2.0          # m/s
    radius: float = 0.4             # m
    battery: float = 100.0          # %
    status: str = Status.IDLE
    online: bool = True
    blocked: bool = False
    current_task_id: Optional[str] = None
    pickup: Optional[dict] = None   # {x, y}
    dropoff: Optional[dict] = None  # {x, y}
    _action_timer: float = field(default=0.0, repr=False)

    def to_dict(self) -> dict:
        return {
            "robotId": self.id,
            "x": self.x,
            "y": self.y,
            "heading": self.heading,
            "status": self.status,
            "battery": self.battery,
            "currentTaskId": self.current_task_id,
            "blocked": self.blocked,
            "online": self.online,
        }


# ---------------------------------------------------------------------------
# MotionController
# ---------------------------------------------------------------------------

ARRIVE_THRESHOLD = 0.15   # m — considered "arrived"
PICK_DURATION = 1.0       # s
DROP_DURATION = 0.8       # s
BATTERY_DRAIN = 0.02      # % per metre travelled
BATTERY_CHARGE = 0.5      # % per second while IDLE/COMPLETED


class MotionController:
    """
    Updates a RobotState each simulation tick.

    Accepts a list of ObstacleRect objects for basic collision avoidance
    (steering around obstacles is Phase 3 — A*; here we do straight-line).
    """

    def __init__(self, state: RobotState, log):
        self.state = state
        self._log = log

    def assign_task(self, task_id: str, pickup: dict, dropoff: dict) -> None:
        s = self.state
        s.current_task_id = task_id
        s.pickup = pickup
        s.dropoff = dropoff
        s.status = Status.MOVING_TO_PICKUP
        s.blocked = False
        self._log(f"{s.id} assigned task {task_id} — moving to pickup")

    def cancel_task(self) -> None:
        s = self.state
        s.current_task_id = None
        s.pickup = None
        s.dropoff = None
        s.status = Status.IDLE
        self._log(f"{s.id} task cancelled")

    def update(self, dt: float, obstacles: list[ObstacleRect]) -> None:
        s = self.state
        if not s.online:
            return

        if s.status == Status.IDLE or s.status == Status.COMPLETED:
            # Charge battery when idle
            s.battery = min(100.0, s.battery + BATTERY_CHARGE * dt)
            s.speed = 0.0
            return

        if s.status == Status.MOVING_TO_PICKUP and s.pickup:
            arrived = self._move_towards(s.pickup["x"], s.pickup["y"], dt, obstacles)
            if arrived:
                s.status = Status.PICKING
                s._action_timer = PICK_DURATION
                self._log(f"{s.id} arrived at pickup — picking")

        elif s.status == Status.PICKING:
            s._action_timer -= dt
            s.speed = 0.0
            if s._action_timer <= 0:
                s.status = Status.MOVING_TO_DROPOFF
                self._log(f"{s.id} picked up — moving to dropoff")

        elif s.status == Status.MOVING_TO_DROPOFF and s.dropoff:
            arrived = self._move_towards(s.dropoff["x"], s.dropoff["y"], dt, obstacles)
            if arrived:
                s.status = Status.DROPPING
                s._action_timer = DROP_DURATION
                self._log(f"{s.id} arrived at dropoff — dropping")

        elif s.status == Status.DROPPING:
            s._action_timer -= dt
            s.speed = 0.0
            if s._action_timer <= 0:
                s.status = Status.COMPLETED
                self._log(f"{s.id} task {s.current_task_id} COMPLETED")

    def _move_towards(self, tx: float, ty: float, dt: float,
                       obstacles: list[ObstacleRect]) -> bool:
        s = self.state
        dx, dy = tx - s.x, ty - s.y
        dist = math.hypot(dx, dy)
        if dist <= ARRIVE_THRESHOLD:
            s.x, s.y = tx, ty
            s.speed = 0.0
            s.blocked = False
            return True

        # Heading
        s.heading = math.atan2(dy, dx)

        # Check if next step is blocked
        step = min(s.max_speed * dt, dist)
        nx = s.x + (dx / dist) * step
        ny = s.y + (dy / dist) * step

        blocked = any(obs.inflated_contains(nx, ny, s.radius) for obs in obstacles)
        s.blocked = blocked

        if not blocked:
            metres = step
            s.x, s.y = nx, ny
            s.speed = step / dt if dt > 0 else 0.0
            s.battery = max(0.0, s.battery - BATTERY_DRAIN * metres)
        else:
            s.speed = 0.0
            # Phase 3 will replace this with A* path; for now just stay put

        return False
