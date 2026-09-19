"""
models.py — Pydantic schemas for all Zenoh message payloads.

Every class maps 1-to-1 with the JSON payload published/received on a Zenoh topic.
Field names must match exactly what the JS nodes emit.
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations (mirror JS ROBOT_STATUS and TASK_STATUS)
# ---------------------------------------------------------------------------

class RobotStatus(str, Enum):
    IDLE = "IDLE"
    MOVING_TO_PICKUP = "MOVING_TO_PICKUP"
    PICKING = "PICKING"
    MOVING_TO_DROPOFF = "MOVING_TO_DROPOFF"
    DROPPING = "DROPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CHARGING = "CHARGING"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    AUCTIONING = "AUCTIONING"   # announced, waiting for a round to resolve
    ASSIGNED = "ASSIGNED"
    PICKING_UP = "PICKING_UP"
    DELIVERING = "DELIVERING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    @classmethod
    def available_to_claim(cls, status: "TaskStatus") -> bool:
        """Statuses a task may be claimed from (pending or in an open round)."""
        return status in (cls.PENDING, cls.AUCTIONING)


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------

class Point(BaseModel):
    x: float
    y: float


class ObstacleSchema(BaseModel):
    id: str
    x: float
    y: float
    width: float
    height: float


class RosterEntry(BaseModel):
    id: str
    x: float
    y: float


class BidCosts(BaseModel):
    travel: float
    congestion: float
    battery: float
    workload: float


# ---------------------------------------------------------------------------
# Topic payload schemas
# ---------------------------------------------------------------------------

class TaskNewPayload(BaseModel):
    """topics.TASK_NEW — coordinator → fleet."""
    taskId: str
    auctionId: Optional[str] = None   # explicit auction round id (P2P_AUCTION)
    pickup: Point
    dropoff: Point
    priority: Optional[int] = 1


class TaskAssignedPayload(BaseModel):
    """topics.TASK_ASSIGNED — coordinator → winner."""
    taskId: str
    robotId: str
    source: str = "auction"


class TaskCancelledPayload(BaseModel):
    """topics.TASK_CANCELLED — coordinator → fleet."""
    taskId: str


class BidPlacedPayload(BaseModel):
    """topics.BID_PLACED — robot → fleet (coordinator or peers)."""
    taskId: str
    auctionId: Optional[str] = None    # auction round id when present
    robotId: str
    bid: Optional[float] = None        # None means ineligible
    costs: Optional[BidCosts] = None
    reason: Optional[str] = None       # ineligibility reason


class AuctionCommitPayload(BaseModel):
    """topics.AUCTION_COMMIT — winner → fleet (P2P_AUCTION only)."""
    taskId: str
    auctionId: str
    robotId: str                    # the committing winner robot
    winner: str
    bidCount: int = 0
    bids: list[BidPlacedPayload] = Field(default_factory=list)


class AuctionResultPayload(BaseModel):
    """topics.AUCTION_RESULT — winner/coordinator → fleet."""
    taskId: str
    auctionId: Optional[str] = None    # the committed round
    winner: Optional[str] = None
    bids: list[BidPlacedPayload] = Field(default_factory=list)
    committed: bool = False
    conflict: Optional[bool] = None    # True ⇒ divergent winner aborted


class TelemetryPayload(BaseModel):
    """topics.ROBOT_TELEMETRY — robot → coordinator (0.5 s period)."""
    robotId: str
    x: float
    y: float
    heading: float = 0.0
    status: RobotStatus = RobotStatus.IDLE
    battery: float = 100.0
    currentTaskId: Optional[str] = None
    blocked: bool = False
    online: bool = True

    @field_validator("battery")
    @classmethod
    def clamp_battery(cls, v: float) -> float:
        return max(0.0, min(100.0, v))


class WorldStatePayload(BaseModel):
    """topics.WORLD_STATE — coordinator → fleet (1 s heartbeat)."""
    width: float
    height: float
    obstacles: list[ObstacleSchema] = Field(default_factory=list)
    chargingPads: list[dict] = Field(default_factory=list)
    deliveryDocks: list[dict] = Field(default_factory=list)
    roster: list[RosterEntry] = Field(default_factory=list)
    auctionMode: Optional[str] = None
    # Authoritative task ledger + aggregate stats (dashboard consumes these
    # instead of inferring task state from telemetry).
    tasks: list[dict] = Field(default_factory=list)
    taskStats: Optional[dict] = None
    robotStats: Optional[dict] = None
    metrics: Optional[dict] = None


# ---------------------------------------------------------------------------
# Envelope wrapper (matches MessageBus.js { origin, type, payload })
# ---------------------------------------------------------------------------

class ZenohEnvelope(BaseModel):
    """Top-level JSON wrapper for every Zenoh message."""
    origin: str        # sender id, e.g. "server" or "AMR1"
    type: str          # topic string, e.g. "tasks/new"
    payload: dict      # raw payload dict — callers parse into specific model
