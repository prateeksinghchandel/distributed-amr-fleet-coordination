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