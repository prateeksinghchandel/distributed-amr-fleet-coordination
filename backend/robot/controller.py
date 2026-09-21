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
    BATTERY_FULL, BATTERY_WARN_THRESHOLD, CHARGE_REQUEST_THRESHOLD,
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
    standby_spot: Optional[tuple[float, float]] = None
    own_pad_id: Optional[str] = None
    charge_pad: Optional[dict] = None
    pads: list[dict] = field(default_factory=list)
    yielded_pad: Optional[str] = None
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
            "ownPadId": self.own_pad_id,
        }


# ---------------------------------------------------------------------------
# MotionController with A* Navigation
# ---------------------------------------------------------------------------

ARRIVE_THRESHOLD = 0.15   # m — considered "arrived"
PICK_DURATION = 1.0       # s
DROP_DURATION = 0.8       # s
COMPLETED_HOLD = 1.0      # s — keep COMPLETED observable (telemetry publishes every 0.5s)


PAD_OCCUPANCY = 0.6   # m — diameter around pad centre considered occupied

def _pad_centroid(pad: dict) -> tuple[float, float]:
    """Return pad centre coordinates (spawnPoint centre, falling back to rect centroid)."""
    sp = pad.get("spawnPoint")
    if sp and isinstance(sp, dict) and "x" in sp and "y" in sp:
        return (float(sp["x"]), float(sp["y"]))
    w = float(pad.get("width", 0.0))
    h = float(pad.get("height", 0.0))
    return (float(pad.get("x", 0.0)) + w / 2.0, float(pad.get("y", 0.0)) + h / 2.0)

def _is_on_pad(x: float, y: float, pad: dict) -> bool:
    c = _pad_centroid(pad)
    return math.dist((x, y), c) <= PAD_OCCUPANCY

def _pad_occupied_by(pad: dict, fleet: Sequence[dict], exclude_id: Optional[str] = None) -> Optional[dict]:
    c = _pad_centroid(pad)
    for r in fleet:
        rid = r.get("robotId") or r.get("id")
        if rid == exclude_id or not r.get("online", True):
            continue
        rx = float(r.get("x", 0.0))
        ry = float(r.get("y", 0.0))
        if math.dist((rx, ry), c) <= PAD_OCCUPANCY:
            return r
    # Also check coordinator-broadcasted occupancy from world state
    occupied_by = pad.get("occupiedBy")
    if occupied_by and occupied_by != exclude_id:
        return {"robotId": occupied_by}
    return None


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

    def update(self, dt: float, obstacles: list[Rect], bounds: Optional[dict] = None, fleet: Sequence[dict] = ()) -> None:
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

        # Autonomous Return-to-Charge Lifecycle & Turnover:
        if not s.current_task_id:
            own_pad = None
            if s.own_pad_id and s.pads:
                own_pad = next((p for p in s.pads if p.get("id") == s.own_pad_id), None)

            if own_pad is not None:
                centroid = _pad_centroid(own_pad)
                # A peer needs a free station when it has no pad of its own
                # (ownPadId unset = overflow/standby robot), is below the charge
                # request threshold and is currently off any pad. Pad owners
                # returning to their own pad never trigger give-way.
                needy_off_pad = any(
                    not r.get("ownPadId")
                    and float(r.get("battery", 100.0)) < CHARGE_REQUEST_THRESHOLD
                    and not any(_is_on_pad(float(r.get("x", 0.0)), float(r.get("y", 0.0)), p) for p in s.pads)
                    for r in fleet
                    if (r.get("robotId") or r.get("id")) != s.id and r.get("online", True)
                )
                occupant = _pad_occupied_by(own_pad, fleet, exclude_id=s.id)
                # An owner that already gave way stays clear as long as its pad is
                # still needed (occupied by the standby robot, or a standby robot
                # still needs a station), regardless of its own battery level.
                already_yielded = s.yielded_pad == s.own_pad_id and s.own_pad_id is not None
                should_yield_now = (occupant is not None) or (
                    needy_off_pad and s.battery >= CHARGE_REQUEST_THRESHOLD and not already_yielded
                )
                stay_clear = already_yielded and (occupant is not None or needy_off_pad)
                if should_yield_now or stay_clear:
                    if s.yielded_pad != s.own_pad_id:
                        s.yielded_pad = s.own_pad_id
                    if s.status != RobotStatus.IDLE:
                        s.status = RobotStatus.IDLE
                    s.charge_pad = None
                    vacate_target = s.standby_spot or (max(1.0, centroid[0] - 2.5), centroid[1])
                    if math.dist((s.x, s.y), vacate_target) > 0.3 and not s.current_path:
                        s.current_path = self.plan_route(vacate_target[0], vacate_target[1], obstacles, bounds)
                        s.path_index = 0
                        self._log(f"{s.id} giving way on pad {s.own_pad_id} — vacating to ({vacate_target[0]:.1f}, {vacate_target[1]:.1f})")
                elif s.status != RobotStatus.CHARGING:
                    # No contender and pad free — claim / trickle-park on own pad centre
                    s.yielded_pad = None
                    c = centroid
                    if math.dist((s.x, s.y), c) > 0.3:
                        s.status = RobotStatus.CHARGING
                        s.charge_pad = own_pad
                        s.current_path = self.plan_route(c[0], c[1], obstacles, bounds)
                        s.path_index = 0
                        self._log(f"{s.id} returning to own pad {s.own_pad_id} ({c[0]:.1f}, {c[1]:.1f})")
                    else:
                        s.status = RobotStatus.CHARGING
                        s.charge_pad = own_pad
                        s.current_path = []
                        s.path_index = 0

            # Standby (overflow) robot: seek a free pad only when it actually needs
            # charging (< CHARGE_REQUEST_THRESHOLD), otherwise hold at standby slot.
            elif s.standby_spot is not None and s.own_pad_id is None and s.status != RobotStatus.CHARGING:
                if s.battery < CHARGE_REQUEST_THRESHOLD:
                    # Needs charge — seek lowest-index free pad
                    free_pad = next((p for p in s.pads if _pad_occupied_by(p, fleet, exclude_id=s.id) is None), None)
                    if free_pad is not None:
                        c = _pad_centroid(free_pad)
                        s.status = RobotStatus.CHARGING
                        s.charge_pad = free_pad
                        s.current_path = self.plan_route(c[0], c[1], obstacles, bounds)
                        s.path_index = 0
                        self._log(f"{s.id} needs charge — seeking free pad {free_pad['id']} ({c[0]:.1f}, {c[1]:.1f})")
                    else:
                        # All pads busy — owners will give way; wait at standby slot
                        c = s.standby_spot
                        if math.dist((s.x, s.y), c) > 0.3 and not s.current_path:
                            s.status = RobotStatus.IDLE
                            s.current_path = self.plan_route(c[0], c[1], obstacles, bounds)
                            s.path_index = 0
                            self._log(f"{s.id} needs charge — all pads busy, waiting at standby slot ({c[0]:.1f}, {c[1]:.1f})")
                else:
                    # Healthy — hold at standby slot, do not hoard pads
                    c = s.standby_spot
                    s.charge_pad = None
                    if math.dist((s.x, s.y), c) > 0.3 and not s.current_path:
                        s.status = RobotStatus.IDLE
                        s.current_path = self.plan_route(c[0], c[1], obstacles, bounds)
                        s.path_index = 0
                        self._log(f"{s.id} holding standby slot ({c[0]:.1f}, {c[1]:.1f})")

            # Legacy fallback: home_charge_bay
            elif s.home_charge_bay and not s.own_pad_id and not s.standby_spot:
                if (s.status == RobotStatus.IDLE or s.status == RobotStatus.COMPLETED or
                        (s.battery < BATTERY_WARN_THRESHOLD and s.status != RobotStatus.CHARGING)):
                    if math.dist((s.x, s.y), s.home_charge_bay) > 0.3:
                        s.status = RobotStatus.CHARGING
                        s.current_path = self.plan_route(s.home_charge_bay[0], s.home_charge_bay[1], obstacles, bounds)
                        s.path_index = 0
                        self._log(f"{s.id} returning to charging bay ({s.home_charge_bay[0]:.1f}, {s.home_charge_bay[1]:.1f})")

        if s.status == RobotStatus.CHARGING:
            arrived = self._follow_path(dt)
            centre = None
            if s.charge_pad:
                centre = _pad_centroid(s.charge_pad)
            elif s.home_charge_bay:
                centre = s.home_charge_bay

            if centre:
                dist_to_centre = math.dist((s.x, s.y), centre)
                if arrived or dist_to_centre <= 0.35:
                    s.speed = 0.0
                    if dist_to_centre <= PAD_OCCUPANCY:
                        s.battery = min(BATTERY_FULL, s.battery + CHARGE_RATE_PER_SEC * dt)
                    if s.battery >= BATTERY_FULL:
                        s.status = RobotStatus.IDLE
                        if s.own_pad_id:
                            self._log(f"{s.id} fully charged (100%) — resting on pad")
                        else:
                            # Overflow robot vacates immediately at 100%
                            pad_name = s.charge_pad.get("id") if s.charge_pad else "pad"
                            s.charge_pad = None
                            if s.standby_spot:
                                s.current_path = self.plan_route(s.standby_spot[0], s.standby_spot[1], obstacles, bounds)
                                s.path_index = 0
                                self._log(f"{s.id} fully charged (100%) — vacating {pad_name} for standby slot")
                            else:
                                self._log(f"{s.id} fully charged (100%) — vacating {pad_name}")
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
