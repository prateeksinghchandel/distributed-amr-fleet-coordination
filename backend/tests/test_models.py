"""
test_models.py — Pydantic schema validation tests.
"""

import pytest
from pydantic import ValidationError
from common.models import (
    Point, TaskNewPayload, TaskAssignedPayload, TaskCancelledPayload,
    BidPlacedPayload, BidCosts, AuctionResultPayload,
    TelemetryPayload, WorldStatePayload, ObstacleSchema, RosterEntry,
    RobotStatus, TaskStatus, Task, ZenohEnvelope,
)


class TestPoint:
    def test_valid(self):
        p = Point(x=1.5, y=3.0)
        assert p.x == 1.5 and p.y == 3.0

    def test_int_coerced(self):
        p = Point(x=2, y=7)
        assert isinstance(p.x, float)


class TestTaskNewPayload:
    def test_valid(self):
        p = TaskNewPayload(
            taskId="T-001",
            pickup={"x": 5.0, "y": 3.0},
            dropoff={"x": 2.0, "y": 10.0},
        )
        assert p.taskId == "T-001"
        assert p.pickup.x == 5.0
        assert p.priority == 1  # default

    def test_missing_taskId_raises(self):
        with pytest.raises(ValidationError):
            TaskNewPayload(pickup={"x": 1, "y": 1}, dropoff={"x": 2, "y": 2})


class TestBidPlacedPayload:
    def test_eligible_bid(self):
        p = BidPlacedPayload(
            taskId="T-001",
            robotId="AMR1",
            bid=12.5,
            costs=BidCosts(travel=10.0, congestion=0.0, battery=2.5, workload=0.0),
        )
        assert p.bid == 12.5
        assert p.costs.travel == 10.0

    def test_ineligible_bid(self):
        """bid=None means the robot did not bid (ineligible)."""
        p = BidPlacedPayload(taskId="T-001", robotId="AMR2", bid=None, reason="busy")
        assert p.bid is None
        assert p.reason == "busy"

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            BidPlacedPayload(robotId="AMR1")


class TestAuctionResultPayload:
    def test_winner(self):
        p = AuctionResultPayload(taskId="T-001", winner="AMR1", committed=True, bids=[])
        assert p.winner == "AMR1"
        assert p.committed is True

    def test_no_winner(self):
        p = AuctionResultPayload(taskId="T-001", winner=None, committed=False)
        assert p.winner is None

    def test_bids_default_empty(self):
        p = AuctionResultPayload(taskId="T-001")
        assert p.bids == []


class TestTelemetryPayload:
    def test_valid_defaults(self):
        p = TelemetryPayload(robotId="AMR1", x=5.0, y=10.0)
        assert p.status == RobotStatus.IDLE
        assert p.battery == 100.0
        assert p.online is True
        assert p.blocked is False

    def test_battery_clamped_above_100(self):
        p = TelemetryPayload(robotId="AMR1", x=0, y=0, battery=150.0)
        assert p.battery == 100.0

    def test_battery_clamped_below_0(self):
        p = TelemetryPayload(robotId="AMR1", x=0, y=0, battery=-5.0)
        assert p.battery == 0.0

    def test_robot_status_enum(self):
        p = TelemetryPayload(robotId="AMR1", x=0, y=0, status="PICKING")
        assert p.status == RobotStatus.PICKING

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            TelemetryPayload(robotId="AMR1", x=0, y=0, status="FLYING")


class TestWorldStatePayload:
    def test_valid(self):
        p = WorldStatePayload(
            width=30.0, height=20.0,
            obstacles=[{"id": "SH-1", "x": 10, "y": 5, "width": 3.5, "height": 1.6}],
            roster=[{"id": "AMR1", "x": 28.0, "y": 5.0}],
        )
        assert p.width == 30.0
        assert len(p.obstacles) == 1
        assert len(p.roster) == 1
        assert p.obstacles[0].id == "SH-1"

    def test_empty_obstacles_and_roster(self):
        p = WorldStatePayload(width=30, height=20)
        assert p.obstacles == []
        assert p.roster == []


class TestTaskStatusEnum:
    def test_all_statuses_exist(self):
        for s in ["PENDING", "ASSIGNED", "PICKING_UP", "DELIVERING",
                  "COMPLETED", "CANCELLED", "FAILED"]:
            assert TaskStatus(s).value == s


class TestRobotStatusEnum:
    def test_all_statuses_exist(self):
        for s in ["IDLE", "MOVING_TO_PICKUP", "PICKING", "MOVING_TO_DROPOFF",
                  "DROPPING", "COMPLETED", "FAILED", "CHARGING"]:
            assert RobotStatus(s).value == s


class TestZenohEnvelope:
    def test_roundtrip(self):
        env = ZenohEnvelope(
            origin="server",
            type="tasks/new",
            payload={"taskId": "T-001", "pickup": {"x": 1, "y": 2}},
        )
        assert env.origin == "server"
        assert env.type == "tasks/new"
        assert env.payload["taskId"] == "T-001"


class TestTask:
    def test_defaults(self):
        t = Task(
            id="T-ABC",
            pickup=Point(x=5.0, y=3.0),
            dropoff=Point(x=2.0, y=10.0),
        )
        assert t.status == TaskStatus.PENDING
        assert t.priority == 1
        assert t.assigned_robot_id is None
