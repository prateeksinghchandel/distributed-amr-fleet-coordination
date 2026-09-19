"""
controller.py — Motion controller and robot state machine for a Python AMR with A* navigation.

State Machine:
  IDLE → MOVING_TO_PICKUP → PICKING → MOVING_TO_DROPOFF → DROPPING → COMPLETED

Navigation:
  - Uses AStarPlanner to find global collision-free paths around shelf racks and obstacles.
  - Uses WaypointFollower for smooth waypoint tracking.
  - Battery drains during movement and charges at rest.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from common.geometry import Rect
from common.models import RobotStatus
from robot.battery import (
    BATTERY_FULL, BATTERY_WARN_THRESHOLD,
    CHARGE_RATE_PER_SEC, IDLE_DRAIN_PER_SEC, TRAVEL_DRAIN_PER_METER,
)
from robot.planning.astar import AStarPlanner, PathNotFoundError
from robot.planning.waypoint_follower import WaypointFollower


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
    status: RobotStatus = RobotStatus.IDLE
    online: bool = True
    blocked: bool = False
    current_task_id: Optional[str] = None
    pickup: Optional[dict] = None   # {x, y}
    dropoff: Optional[dict] = None  # {x, y}
    current_path: list[tuple[float, float]] = field(default_factory=list)
    path_index: int = 0
    home_charge_bay: Optional[tuple[float, float]] = None
    _action_timer: float = field(default=0.0, repr=False)
    _completed_timer: float = field(default=0.0, repr=False)

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
# MotionController with A* Navigation
# ---------------------------------------------------------------------------

ARRIVE_THRESHOLD = 0.15   # m — considered "arrived"
PICK_DURATION = 1.0       # s
DROP_DURATION = 0.8       # s
COMPLETED_HOLD = 1.0      # s — keep COMPLETED observable (telemetry publishes every 0.5s)


class MotionController:
    """
    Updates a RobotState each simulation tick using A* path planning and waypoint following.
    """

    def __init__(self, state: RobotState, log, resolution: float = 0.25, safety_margin: float = 0.1):
        self.state = state
        self._log = log
        self.resolution = resolution
        self.safety_margin = safety_margin
        self.follower = WaypointFollower(lookahead=0.6, arrival_threshold=ARRIVE_THRESHOLD)

    def plan_route(self, goal_x: float, goal_y: float, obstacles: Sequence[object] = (),
                   bounds: Optional[dict] = None) -> list[tuple[float, float]]:
        w = bounds.get("width", 30.0) if bounds else 30.0
        h = bounds.get("height", 20.0) if bounds else 20.0
        planner = AStarPlanner(
            width=w, height=h,
            resolution=self.resolution,
            robot_radius=self.state.radius,
            safety_margin=self.safety_margin
        )
        try:
            return planner.plan((self.state.x, self.state.y), (goal_x, goal_y), obstacles)
        except PathNotFoundError:
            self._log(f"Warning: A* path not found to ({goal_x:.2f}, {goal_y:.2f}), using direct waypoint")
            return [(self.state.x, self.state.y), (goal_x, goal_y)]

    def assign_task(self, task_id: str, pickup: dict, dropoff: dict,
                    obstacles: Sequence[object] = (), bounds: Optional[dict] = None) -> None:
        s = self.state
        s.current_task_id = task_id
        s.pickup = pickup
        s.dropoff = dropoff
        s.status = RobotStatus.MOVING_TO_PICKUP
        s.blocked = False
        s._completed_timer = 0.0
        s.current_path = self.plan_route(pickup["x"], pickup["y"], obstacles, bounds)
        s.path_index = 0
        self._log(f"{s.id} assigned task {task_id} — planned {len(s.current_path)} waypoints to pickup")

    def cancel_task(self) -> None:
        s = self.state
        s.current_task_id = None
        s.pickup = None
        s.dropoff = None
        s._completed_timer = 0.0
        s.current_path = []
        s.path_index = 0
        s.status = RobotStatus.IDLE
        s.speed = 0.0
        self._log(f"{s.id} task cancelled")

    def update(self, dt: float, obstacles: list[Rect], bounds: Optional[dict] = None) -> None:
        s = self.state
        if not s.online:
            return

        # Completed dwell: hold COMPLETED for one telemetry window so the
        # coordinator and dashboards can observe the completion — the robot
        # keeps current_task_id set so the server ledger can sync the task to
        # COMPLETED — before the robot returns to charge / idle.
        if s.status == RobotStatus.COMPLETED:
            if s._completed_timer > 0:
                s._completed_timer -= dt
                s.speed = 0.0
                s.battery = max(0.0, s.battery - IDLE_DRAIN_PER_SEC * dt)
                return
            # Hold expired: the completion has been observable for a full
            # telemetry window. Free the robot for new assignments.
            s.current_task_id = None
            s.pickup = None
            s.dropoff = None
            s._completed_timer = 0.0
            s.status = RobotStatus.IDLE
            self._log(f"{s.id} completion hold expired — available for new tasks")

        # Autonomous Return-to-Charge Lifecycle:
        # If robot is low battery (< WARN) and not busy delivering, or IDLE with
        # charge < 100% and a charge pad is known:
        if (s.status == RobotStatus.IDLE or s.status == RobotStatus.COMPLETED or
                (s.battery < BATTERY_WARN_THRESHOLD and s.status != RobotStatus.CHARGING and not s.current_task_id)):
            if s.home_charge_bay and math.dist((s.x, s.y), s.home_charge_bay) > 0.3:
                s.status = RobotStatus.CHARGING
                s.current_path = self.plan_route(s.home_charge_bay[0], s.home_charge_bay[1], obstacles, bounds)
                s.path_index = 0
                self._log(f"{s.id} returning to charging bay ({s.home_charge_bay[0]:.1f}, {s.home_charge_bay[1]:.1f})")

        if s.status == RobotStatus.CHARGING:
            arrived = self._follow_path(dt)
            if arrived or (s.home_charge_bay and math.dist((s.x, s.y), s.home_charge_bay) <= 0.3):
                s.speed = 0.0
                s.battery = min(BATTERY_FULL, s.battery + CHARGE_RATE_PER_SEC * dt)
                if s.battery >= BATTERY_FULL:
                    s.status = RobotStatus.IDLE
                    self._log(f"{s.id} fully charged (100%)")
            return

        if s.status == RobotStatus.IDLE or s.status == RobotStatus.COMPLETED:
            # Idle standby drain
            s.battery = max(0.0, s.battery - IDLE_DRAIN_PER_SEC * dt)
            s.speed = 0.0
            return

        if s.status == RobotStatus.MOVING_TO_PICKUP:
            arrived = self._follow_path(dt)
            if arrived:
                s.status = RobotStatus.PICKING
                s._action_timer = PICK_DURATION
                s.speed = 0.0
                s.current_path = []
                s.path_index = 0
                self._log(f"{s.id} arrived at pickup — picking")

        elif s.status == RobotStatus.PICKING:
            s._action_timer -= dt
            s.speed = 0.0
            if s._action_timer <= 0 and s.dropoff:
                s.status = RobotStatus.MOVING_TO_DROPOFF
                s.current_path = self.plan_route(s.dropoff["x"], s.dropoff["y"], obstacles, bounds)
                s.path_index = 0
                self._log(f"{s.id} picked up — planned {len(s.current_path)} waypoints to dropoff")

        elif s.status == RobotStatus.MOVING_TO_DROPOFF:
            arrived = self._follow_path(dt)
            if arrived:
                s.status = RobotStatus.DROPPING
                s._action_timer = DROP_DURATION
                s.speed = 0.0
                s.current_path = []
                s.path_index = 0
                self._log(f"{s.id} arrived at dropoff — dropping")

        elif s.status == RobotStatus.DROPPING:
            s._action_timer -= dt
            s.speed = 0.0
            if s._action_timer <= 0:
                s.status = RobotStatus.COMPLETED
                s._completed_timer = COMPLETED_HOLD
                self._log(f"{s.id} task {s.current_task_id} COMPLETED")

    def _follow_path(self, dt: float) -> bool:
        s = self.state
        if not s.current_path:
            return True

        nx, ny, heading, new_idx, arrived, travelled = self.follower.step(
            (s.x, s.y), s.current_path, s.path_index, s.max_speed, dt
        )
        s.x = nx
        s.y = ny
        s.heading = heading
        s.path_index = new_idx
        s.speed = travelled / dt if dt > 0 else 0.0
        s.battery = max(0.0, s.battery - TRAVEL_DRAIN_PER_METER * travelled)
        return arrived
