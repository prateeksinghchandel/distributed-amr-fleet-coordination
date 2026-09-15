"""
test_agent.py — Unit tests for FleetAgent bid computation and auction logic.
No Zenoh session required — everything runs in-process.
"""

import pytest
import math
from robot.agent import FleetAgent, select_winner, BATTERY_LOW_PENALTY, BATTERY_WARN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_robot(x=0.0, y=0.0, battery=100.0, status="IDLE",
               current_task_id=None, online=True):
    return {
        "robotId": "AMR1",
        "x": x,
        "y": y,
        "heading": 0.0,
        "status": status,
        "battery": battery,
        "currentTaskId": current_task_id,
        "blocked": False,
        "online": online,
    }


published: list = []


def make_agent(robot_dict: dict, fleet: list = None) -> FleetAgent:
    published.clear()
    fleet_map = {s["robotId"]: s for s in (fleet or [])}

    def publish_cb(topic, payload):
        published.append({"topic": topic, "payload": payload})

    agent = FleetAgent(
        robot_id=robot_dict["robotId"],
        get_robot=lambda: robot_dict,
        publish_cb=publish_cb,
        log=lambda msg: None,
        get_obstacles=lambda: [],
        get_fleet_snapshot=lambda: list(fleet_map.values()),
        disable_finalize=True,
    )
    agent.fleet = fleet_map
    return agent


# ---------------------------------------------------------------------------
# select_winner tests
# ---------------------------------------------------------------------------

class TestSelectWinner:
    def test_lowest_bid_wins(self):
        bids = [
            {"robotId": "AMR1", "bid": 10.5},
            {"robotId": "AMR2", "bid": 8.2},
            {"robotId": "AMR3", "bid": 12.0},
        ]
        assert select_winner(bids) == "AMR2"

    def test_tie_broken_by_lexicographic_id(self):
        bids = [
            {"robotId": "AMR2", "bid": 5.0},
            {"robotId": "AMR1", "bid": 5.0},
        ]
        assert select_winner(bids) == "AMR1"

    def test_none_bid_ignored(self):
        bids = [
            {"robotId": "AMR1", "bid": None},
            {"robotId": "AMR2", "bid": 7.0},
        ]
        assert select_winner(bids) == "AMR2"

    def test_all_ineligible_returns_none(self):
        bids = [
            {"robotId": "AMR1", "bid": None},
            {"robotId": "AMR2", "bid": None},
        ]
        assert select_winner(bids) is None

    def test_empty_bids_returns_none(self):
        assert select_winner([]) is None

    def test_nan_bid_ignored(self):
        bids = [
            {"robotId": "AMR1", "bid": float("nan")},
            {"robotId": "AMR2", "bid": 5.0},
        ]
        assert select_winner(bids) == "AMR2"


# ---------------------------------------------------------------------------
# compute_bid tests
# ---------------------------------------------------------------------------

class TestComputeBid:
    def test_basic_travel(self):
        robot = make_robot(x=0, y=0, battery=100)
        agent = make_agent(robot)
        result = agent.compute_bid(
            pickup={"x": 3.0, "y": 4.0},   # dist = 5.0
            dropoff={"x": 6.0, "y": 8.0},  # dist from pickup = 5.0
        )
        assert result["eligible"] is True
        assert result["costs"]["travel"] == pytest.approx(10.0, abs=0.01)

    def test_battery_cost_full(self):
        robot = make_robot(battery=100.0)
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 0, "y": 0}, {"x": 0, "y": 0})
        assert result["costs"]["battery"] == pytest.approx(0.0, abs=0.01)

    def test_battery_cost_partial(self):
        robot = make_robot(battery=80.0)
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 0, "y": 0}, {"x": 0, "y": 0})
        # battery_cost = (100 - 80) * 0.05 = 1.0
        assert result["costs"]["battery"] == pytest.approx(1.0, abs=0.01)

    def test_low_battery_penalty(self):
        robot = make_robot(battery=15.0)
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 0, "y": 0}, {"x": 0, "y": 0})
        # battery_cost = (100 - 15) * 0.05 + 30 = 4.25 + 30 = 34.25
        assert result["costs"]["battery"] == pytest.approx(34.25, abs=0.1)

    def test_offline_not_eligible(self):
        robot = make_robot(online=False)
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 0, "y": 0}, {"x": 0, "y": 0})
        assert result["eligible"] is False
        assert result["reason"] == "offline"

    def test_busy_not_eligible(self):
        robot = make_robot(status="MOVING_TO_PICKUP", current_task_id="T-001")
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 0, "y": 0}, {"x": 0, "y": 0})
        assert result["eligible"] is False
        assert result["reason"] == "busy"

    def test_completed_robot_is_eligible(self):
        robot = make_robot(status="COMPLETED", current_task_id="T-001")
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 0, "y": 0}, {"x": 5, "y": 0})
        assert result["eligible"] is True

    def test_congestion_cost(self):
        """Peer within 4m of pickup adds CONGESTION_COST=3 to bid."""
        robot = make_robot(x=0, y=0, battery=100)
        peer = {
            "robotId": "AMR2",
            "x": 2.5, "y": 0.0,  # within 4m of pickup (3,4)
            "online": True, "status": "IDLE",
            "battery": 100, "currentTaskId": None, "blocked": False, "heading": 0,
        }
        agent = make_agent(robot, fleet=[peer])
        result = agent.compute_bid(
            pickup={"x": 3.0, "y": 0.0},
            dropoff={"x": 20.0, "y": 10.0},
        )
        assert result["costs"]["congestion"] == pytest.approx(3.0, abs=0.01)

    def test_total_bid_equals_sum_of_costs(self):
        robot = make_robot(x=0, y=0, battery=80)
        agent = make_agent(robot)
        result = agent.compute_bid({"x": 3, "y": 4}, {"x": 6, "y": 8})
        costs = result["costs"]
        expected = costs["travel"] + costs["congestion"] + costs["battery"] + costs["workload"]
        assert result["bid"] == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# Auction round tests
# ---------------------------------------------------------------------------

class TestAuctionRound:
    def _make_task_new_payload(self, task_id="T-001"):
        return {
            "taskId": task_id,
            "pickup": {"x": 5.0, "y": 5.0},
            "dropoff": {"x": 2.0, "y": 10.0},
            "priority": 1,
        }

    def test_on_task_new_publishes_bid(self):
        robot = make_robot(x=0, y=0, battery=100)
        agent = make_agent(robot)
        agent.on_task_new(self._make_task_new_payload(), timestamp=0.0)
        assert any(p["topic"] == "auction/bids" for p in published)

    def test_ineligible_robot_publishes_null_bid(self):
        robot = make_robot(online=False)
        agent = make_agent(robot)
        agent.on_task_new(self._make_task_new_payload(), timestamp=0.0)
        # robot is offline so should not publish a bid
        bid_pubs = [p for p in published if p["topic"] == "auction/bids"]
        assert len(bid_pubs) == 0

    def test_duplicate_task_new_ignored(self):
        robot = make_robot(x=0, y=0, battery=100)
        agent = make_agent(robot)
        payload = self._make_task_new_payload()
        agent.on_task_new(payload, timestamp=0.0)
        count_after_first = len([p for p in published if p["topic"] == "auction/bids"])
        agent.on_task_new(payload, timestamp=0.1)
        count_after_second = len([p for p in published if p["topic"] == "auction/bids"])
        assert count_after_first == count_after_second

    def test_forget_round_removes_entry(self):
        robot = make_robot(x=0, y=0, battery=100)
        agent = make_agent(robot)
        agent.on_task_new(self._make_task_new_payload("T-XYZ"), timestamp=0.0)
        assert "T-XYZ" in agent.rounds
        agent.forget_round("T-XYZ")
        assert "T-XYZ" not in agent.rounds

    def test_on_task_resolved_removes_round(self):
        robot = make_robot(x=0, y=0)
        agent = make_agent(robot)
        agent.on_task_new(self._make_task_new_payload("T-ABC"), timestamp=0.0)
        agent.on_task_resolved({"taskId": "T-ABC"})
        assert "T-ABC" not in agent.rounds


# ---------------------------------------------------------------------------
# Telemetry tests
# ---------------------------------------------------------------------------

class TestTelemetry:
    def test_telemetry_published_after_period(self):
        robot = make_robot(x=1.0, y=2.0, battery=75.0)
        agent = make_agent(robot)
        # Simulate enough ticks to trigger telemetry (period = 0.5s)
        agent.tick(dt=0.5, now=1.0)
        telem = [p for p in published if p["topic"] == "robots/telemetry"]
        assert len(telem) >= 1
        payload = telem[0]["payload"]
        assert payload["robotId"] == "AMR1"
        assert payload["x"] == pytest.approx(1.0)
        assert payload["battery"] == pytest.approx(75.0)

    def test_on_robot_telemetry_updates_fleet(self):
        robot = make_robot()
        agent = make_agent(robot)
        agent.on_robot_telemetry({
            "robotId": "AMR2", "x": 5.0, "y": 3.0,
            "status": "IDLE", "battery": 90.0, "online": True,
            "currentTaskId": None, "blocked": False, "heading": 0.0,
        })
        assert "AMR2" in agent.fleet
        assert agent.fleet["AMR2"]["x"] == 5.0
