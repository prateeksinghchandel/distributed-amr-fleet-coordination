"""
test_battery.py — Unit tests for Phase 4 AMR Battery Management & Autonomous Charging.
"""

import math
import pytest
from robot.battery import (
    BatteryState, BATTERY_FULL, BATTERY_WARN_THRESHOLD,
    BATTERY_CRITICAL_THRESHOLD, CHARGE_RATE_PER_SEC,
    TRAVEL_DRAIN_PER_METER, IDLE_DRAIN_PER_SEC,
)
from robot.controller import RobotState, MotionController
from robot.agent import FleetAgent
from common.geometry import Rect
from common.models import RobotStatus


def test_battery_discharge_and_charge():
    b = BatteryState(level=80.0)
    assert not b.is_full()
    assert not b.is_low()
    assert not b.is_critical()

    # Move 100 meters, 10 seconds
    b.discharge(distance_traveled=100.0, dt=10.0)
    assert b.level < 80.0
    assert b.level == pytest.approx(80.0 - (100 * TRAVEL_DRAIN_PER_METER + 10 * IDLE_DRAIN_PER_SEC))

    # Test charging
    b.is_charging = True
    prev = b.level
    b.charge(dt=2.0)
    assert b.level == pytest.approx(prev + CHARGE_RATE_PER_SEC * 2.0)


def test_battery_threshold_flags():
    b = BatteryState(level=BATTERY_WARN_THRESHOLD - 1.0)
    assert b.is_low()
    assert not b.is_critical()

    b.level = BATTERY_CRITICAL_THRESHOLD - 1.0
    assert b.is_critical()
    assert b.is_low()


def test_agent_rejects_tasks_when_battery_low():
    robot = {
        "robotId": "AMR1", "x": 1.0, "y": 1.0, "heading": 0.0,
        "status": "IDLE", "battery": 9.0,  # Below 10% critical
        "currentTaskId": None, "blocked": False, "online": True, "radius": 0.4
    }
    agent = FleetAgent(
        "AMR1", lambda: robot, lambda *_: None, lambda _: None,
        get_obstacles=lambda: [], get_world_bounds=lambda: (10, 10)
    )
    result = agent.compute_bid({"x": 5, "y": 5}, {"x": 8, "y": 8})
    assert not result["eligible"]
    assert result["reason"] == "low_battery"


def test_controller_autonomous_returns_to_charge():
    # Robot at (5, 5) with low battery and charging bay at (1, 1)
    state = RobotState(id="AMR1", x=5.0, y=5.0, battery=20.0, max_speed=2.0)
    state.home_charge_bay = (1.0, 1.0)
    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # Calling update when idle + low battery triggers charging return
    controller.update(0.1, obstacles=[], bounds={"width": 10, "height": 10})
    assert state.status == RobotStatus.CHARGING
    assert len(state.current_path) >= 2

    # Simulate steps until it reaches the charging bay and charges to 100%
    for _ in range(300):
        controller.update(0.1, obstacles=[], bounds={"width": 10, "height": 10})
        if state.status == RobotStatus.IDLE and state.battery >= BATTERY_FULL:
            break

    assert state.status == RobotStatus.IDLE
    assert state.battery == pytest.approx(BATTERY_FULL)
    assert math.dist((state.x, state.y), state.home_charge_bay) <= 0.35


def test_obstacle_rect_is_shared_geometry():
    """Controller obstacles use the shared Rect (no duplicate ObstacleRect)."""
    r = Rect(4, 2, 2, 6)
    assert r.inflated_contains(5.0, 5.0)
    assert not r.inflated_contains(9.0, 5.0)


def test_standby_robot_seeks_free_pad_and_vacates_at_full():
    """Standby robot seeks free pad when available and vacates pad once battery hits 100%."""
    pad = {
        "id": "BAY-1",
        "x": 8.0,
        "y": 8.0,
        "width": 2.0,
        "height": 2.0,
        "spawnPoint": {"x": 9.0, "y": 9.0},
    }
    state = RobotState(id="AMR_SB", x=1.0, y=1.0, battery=20.0, max_speed=2.0)
    state.standby_spot = (1.0, 1.0)
    state.own_pad_id = None
    state.pads = [pad]

    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # 1. When pad is occupied by another robot, standby robot stays at standby slot
    fleet_busy = [{"robotId": "AMR1", "x": 9.0, "y": 9.0, "battery": 80.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet_busy)
    assert state.status == RobotStatus.IDLE
    assert state.charge_pad is None

    # 2. When pad becomes free, standby robot moves to pad to charge
    fleet_free = [{"robotId": "AMR1", "x": 18.0, "y": 18.0, "battery": 80.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet_free)
    assert state.status == RobotStatus.CHARGING
    assert state.charge_pad == pad

    # 3. Simulate arrival and charging to 100%
    state.x, state.y = 9.0, 9.0
    state.current_path = []
    # Step until battery is full and robot transitions to IDLE/vacates
    for _ in range(140):
        controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet_free)
        if state.status == RobotStatus.IDLE and not state.charge_pad:
            break

    assert state.battery >= 99.9
    assert state.status == RobotStatus.IDLE
    # Standby robot vacates pad: charge_pad is cleared and path to standby spot is planned
    assert state.charge_pad is None
    assert len(state.current_path) >= 1
    # Route target is standby spot (1.0, 1.0)
    assert state.current_path[-1] == pytest.approx((1.0, 1.0), abs=0.4)


def test_pad_owner_yields_pad_to_needy_robot():
    """Pad owner on its pad vacates when an off-pad robot has low battery (< 25%)."""
    pad = {
        "id": "BAY-1",
        "x": 8.0,
        "y": 8.0,
        "width": 2.0,
        "height": 2.0,
        "spawnPoint": {"x": 9.0, "y": 9.0},
    }
    state = RobotState(id="AMR1", x=9.0, y=9.0, battery=90.0, max_speed=2.0)
    state.own_pad_id = "BAY-1"
    state.standby_spot = (2.0, 2.0)
    state.pads = [pad]

    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # When no needy robot exists, owner trickles on own pad
    fleet_healthy = [{"robotId": "AMR2", "x": 5.0, "y": 5.0, "battery": 70.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet_healthy)
    assert state.status == RobotStatus.CHARGING
    assert state.charge_pad == pad

    # When an off-pad robot has battery < 25%, owner yields and vacates
    fleet_needy = [{"robotId": "AMR2", "x": 5.0, "y": 5.0, "battery": 20.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet_needy)
    assert state.charge_pad is None
    assert state.status == RobotStatus.IDLE
    assert len(state.current_path) >= 1
    # Vacate target is standby spot
    assert state.current_path[-1] == pytest.approx((2.0, 2.0), abs=0.4)


def test_healthy_standby_robot_does_not_hoard_free_pad():
    """A standby robot above the charge threshold holds its slot instead of grabbing a free pad."""
    pad = {
        "id": "BAY-1",
        "x": 8.0,
        "y": 8.0,
        "width": 2.0,
        "height": 2.0,
        "spawnPoint": {"x": 9.0, "y": 9.0},
    }
    state = RobotState(id="AMR_SB", x=1.0, y=1.0, battery=80.0, max_speed=2.0)
    state.standby_spot = (1.0, 1.0)
    state.own_pad_id = None
    state.pads = [pad]

    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # Pad is free and battery is healthy (80% >= 25%) → robot stays at standby slot
    fleet = [{"robotId": "AMR1", "x": 18.0, "y": 18.0, "battery": 80.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet)
    assert state.status == RobotStatus.IDLE
    assert state.charge_pad is None


def test_needy_pad_owner_does_not_give_way():
    """An owner below the warn threshold keeps its pad and charges instead of yielding to a contender."""
    pad = {
        "id": "BAY-1",
        "x": 8.0,
        "y": 8.0,
        "width": 2.0,
        "height": 2.0,
        "spawnPoint": {"x": 9.0, "y": 9.0},
    }
    state = RobotState(id="AMR1", x=9.0, y=9.0, battery=20.0, max_speed=2.0)
    state.own_pad_id = "BAY-1"
    state.standby_spot = (2.0, 2.0)
    state.pads = [pad]
    state.status = RobotStatus.CHARGING
    state.charge_pad = pad

    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # Another robot is off-pad and needy, but we are below threshold ourselves
    fleet_needy = [{"robotId": "AMR2", "x": 5.0, "y": 5.0, "battery": 20.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet_needy)
    # Owner keeps charging on its own pad
    assert state.status == RobotStatus.CHARGING
    assert state.charge_pad == pad


def test_pad_owner_stays_away_while_its_pad_is_occupied():
    """An owner that already gave way does not reclaim its pad while another robot is on it."""
    pad = {
        "id": "BAY-1",
        "x": 8.0,
        "y": 8.0,
        "width": 2.0,
        "height": 2.0,
        "spawnPoint": {"x": 9.0, "y": 9.0},
    }
    state = RobotState(id="AMR1", x=6.5, y=9.0, battery=100.0, max_speed=2.0)
    state.own_pad_id = "BAY-1"
    state.charge_pad = None
    state.status = RobotStatus.IDLE
    state.pads = [pad]

    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # AMR2 (needy) is on our pad → owner must stay clear, not head back
    fleet = [{"robotId": "AMR2", "x": 9.0, "y": 9.0, "battery": 20.0, "online": True}]
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20}, fleet=fleet)
    assert state.status == RobotStatus.IDLE
    assert state.charge_pad is None
    assert len(state.current_path) == 0 or state.current_path[-1] != pytest.approx((9.0, 9.0), abs=0.1)


def test_resting_and_charging_at_pad_centroid_vs_corner():
    """Effective charging occurs only within PAD_OCCUPANCY of pad centroid, not bounding box corner."""
    pad = {
        "id": "BAY-1",
        "x": 10.0,
        "y": 5.0,
        "width": 3.0,
        "height": 2.0,
        "spawnPoint": {"x": 11.5, "y": 6.0},
    }
    state = RobotState(id="AMR1", x=10.0, y=5.0, battery=50.0, max_speed=2.0)  # at top-left corner
    state.own_pad_id = "BAY-1"
    state.charge_pad = pad
    state.pads = [pad]
    state.status = RobotStatus.CHARGING

    controller = MotionController(state, lambda _: None, resolution=0.25, safety_margin=0.05)

    # Robot at corner (10.0, 5.0) is > 0.6m away from centroid (11.5, 6.0)
    initial_battery = state.battery
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20})
    # Since it's outside PAD_OCCUPANCY of centroid, it does NOT charge
    assert state.battery == initial_battery

    # Move robot to centroid (11.5, 6.0)
    state.x, state.y = 11.5, 6.0
    controller.update(0.1, obstacles=[], bounds={"width": 20, "height": 20})
    # Now it charges effectively
    assert state.battery > initial_battery
    assert state.battery == pytest.approx(initial_battery + CHARGE_RATE_PER_SEC * 0.1)