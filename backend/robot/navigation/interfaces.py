"""
interfaces.py — Replaceable contracts for the robot navigation pipeline.

The pipeline is::

    Task -> Global Path Planning -> Path Validation -> Local Avoidance
          -> Chokepoint Coordination -> Safety Validation
          -> Trajectory Controller -> Robot

Each interface below corresponds to one stage. The algorithmic baseline ships
with matching ``Algorithmic*`` implementations; future AI modes may replace any
single component as long as the contract is preserved. Nothing about these
interfaces requires a central server — every stage consumes only local state,
the shared fleet snapshot, obstacles and the warehouse bounds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from common.geometry import Rect


# ---------------------------------------------------------------------------
# Static context passed to every stage each tick
# ---------------------------------------------------------------------------

class NavContext:
    """Immutable-per-tick inputs shared by all pipeline stages."""

    def __init__(self, robot, fleet: Sequence[dict], obstacles: Sequence[object],
                 bounds: Optional[dict]):
        self.robot = robot            # RobotState
        self.fleet = list(fleet)      # peer telemetry snapshots
        self.obstacles = list(obstacles)
        self.bounds = bounds or {}


# ---------------------------------------------------------------------------
# Global path planning
# ---------------------------------------------------------------------------

class PathPlanner(ABC):
    """Produces and validates collision-free routes from A to B."""

    @abstractmethod
    def plan(self, start, goal, obstacles: Sequence, bounds: Optional[dict]) -> list:
        """Return an ordered list of (x, y) waypoints."""

    @abstractmethod
    def validate(self, path: list, obstacles: Sequence, fleet: Sequence,
                 bounds: Optional[dict]) -> Optional[str]:
        """Return a machine-readable reason when the stored path is invalid.

        Returns ``None`` when the path may still be followed safely.
        """

    @abstractmethod
    def should_replan(self, reason: str, await_count: int) -> bool:
        """Decide whether ``reason`` justifies a new global plan right now."""


# ---------------------------------------------------------------------------
# Local collision avoidance (velocity-obstacle layer)
# ---------------------------------------------------------------------------

class CollisionAvoidance(ABC):
    """Converts path-following desire into a safe immediate velocity."""

    @abstractmethod
    def compute(self, ctx: NavContext, target, preferred_vx, preferred_vy,
                now: float) -> dict:
        """Return ``{vx, vy, state, reason, intervention, near_collision}``."""


# ---------------------------------------------------------------------------
# Chokepoint / corridor coordination
# ---------------------------------------------------------------------------

class ChokepointCoordinator(ABC):
    """Explicit coordination for narrow / high-conflict regions."""

    @abstractmethod
    def tick(self, ctx: NavContext, path: list, path_index: int, now: float) -> dict:
        """Return ``{state, reason, regionId, reservation}`` for the current tick."""


# ---------------------------------------------------------------------------
# Safety validation (imminent-collision guard)
# ---------------------------------------------------------------------------

class SafetyController(ABC):
    """Highest-priority physical safety layer. Overrides every other stage."""

    @abstractmethod
    def compute(self, ctx: NavContext, desired_vx, desired_vy, now: float) -> dict:
        """Return ``{vx, vy, state, reason, stop}`` — stop True forces a halt."""


# ---------------------------------------------------------------------------
# Trajectory / motion controller (MPC-equivalent)
# ---------------------------------------------------------------------------

class TrajectoryController(ABC):
    """Turns a desired velocity into an executable, constrained control command."""

    @abstractmethod
    def step(self, ctx: NavContext, desired_vx, desired_vy, dt: float) -> tuple:
        """Return ``(vx, vy, speed, heading)`` after applying kinematic limits."""


# ---------------------------------------------------------------------------
# Deadlock detection / resolution
# ---------------------------------------------------------------------------

class DeadlockResolver(ABC):
    """Detects waiting loops and stalled robots and resolves them."""

    @abstractmethod
    def update(self, ctx: NavContext, nav_state: str, nav_reason: Optional[str],
               dt: float, now: float) -> dict:
        """Feed per-tick state; return ``{deadlock, state, reason, action}``."""

    @abstractmethod
    def on_resolved(self, recovery_time: float) -> None:
        """Notify the resolver that a deadlock was recovered."""