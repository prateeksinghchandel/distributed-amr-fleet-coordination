"""
agent.py — FleetAgent: auction bidding logic for an individual Python AMR.

Mirrors FleetAgent.js exactly:
  - Subscribes to TASK_NEW, BID_PLACED, ROBOT_TELEMETRY, TASK_ASSIGNED, TASK_CANCELLED
  - Computes multi-factor bids (travel + congestion + battery + workload)
  - Finalization is DISABLED on robot nodes (coordinatorCommit only; disableFinalize=True)
  - Publishes own telemetry every TELEMETRY_PERIOD seconds
"""

from __future__ import annotations
import math
import time
from typing import Optional, Callable

from common import topics
from robot.planning.astar import AStarPlanner, PathNotFoundError, path_distance


# ---------------------------------------------------------------------------
# Auction constants (mirror AUCTION_CONSTANTS in FleetAgent.js)
# ---------------------------------------------------------------------------

ANNOUNCE_DELAY = 0.1
BID_TRANSMIT_DELAY = 0.3
DEADLINE = 1.0
TELEMETRY_PERIOD = 0.5
TELEMETRY_LAG = 0.05
BLOCKED_PENALTY = 2.5
CONGESTION_RADIUS = 4.0
CONGESTION_COST = 3.0
BATTERY_WEIGHT = 0.05
BATTERY_WARN = 20.0
BATTERY_LOW_PENALTY = 30.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def select_winner(bids: list[dict]) -> Optional[str]:
    """Select lowest-bid robot. Tie-break: lexicographically smaller robotId."""
    best: Optional[str] = None
    best_bid = float("inf")
    for b in bids:
        bid_val = b.get("bid")
        if bid_val is None or not math.isfinite(float(bid_val)):
            continue
        bid_val = float(bid_val)
        robot_id = b.get("robotId", "")
        if bid_val < best_bid or (bid_val == best_bid and (best is None or robot_id < best)):
            best = robot_id
            best_bid = bid_val
    return best


# ---------------------------------------------------------------------------
# FleetAgent
# ---------------------------------------------------------------------------

class FleetAgent:
    """
    Auction bidding agent for one robot.

    Parameters
    ----------
    robot_id : str
    get_robot : callable() → dict with keys x, y, heading, status, battery,
                                              currentTaskId, blocked, online
    publish_cb : callable(topic: str, payload: dict)
    log : callable(str)
    get_obstacles : callable() → list[Rect-like objects with inflated_contains(x,y,margin)]
    get_fleet_snapshot : callable() → list[dict]  (peer telemetry snapshots)
    disable_finalize : bool  — True for robot nodes (coordinator finalizes)
    """

    def __init__(
        self,
        robot_id: str,
        get_robot: Callable[[], dict],
        publish_cb: Callable,
        log: Callable[[str], None],
        get_obstacles: Callable[[], list] = lambda: [],
        get_fleet_snapshot: Callable[[], list] = lambda: [],
        get_world_bounds: Callable[[], tuple[float, float] | None] = lambda: None,
        disable_finalize: bool = True,
        coordinator_commit: Optional[Callable[[str, str, list[dict]], bool]] = None,
    ):
        self.robot_id = robot_id
        self._get_robot = get_robot
        self._publish = publish_cb
        self._log = log
        self._get_obstacles = get_obstacles
        self._get_fleet_snapshot = get_fleet_snapshot
        self._get_world_bounds = get_world_bounds
        self.disable_finalize = disable_finalize
        self._coordinator_commit = coordinator_commit

        # fleet: robotId → latest telemetry dict
        self.fleet: dict[str, dict] = {
            snap["robotId"]: snap for snap in get_fleet_snapshot()
        }

        # auction rounds: taskId → round dict
        self.rounds: dict[str, dict] = {}

        self._telemetry_timer = TELEMETRY_PERIOD
        self._active = True

    # ------------------------------------------------------------------
    # Message handlers  (called from message loop / subscription callbacks)
    # ------------------------------------------------------------------

    def on_task_new(self, payload: dict, timestamp: float) -> None:
        task_id = payload.get("taskId", "")
        pickup = payload.get("pickup", {})
        dropoff = payload.get("dropoff", {})

        if task_id in self.rounds:
            return

        roster_size = max(1, len(self._get_fleet_snapshot()))
        self.rounds[task_id] = {
            "taskId": task_id,
            "bids": {},
            "rosterSize": roster_size,
            "deadline": timestamp + DEADLINE,
            "done": False,
        }

        robot = self._get_robot()
        if not robot.get("online", True):
            return

        result = self.compute_bid(pickup, dropoff)
        if result["eligible"]:
            bid_entry = {"bid": result["bid"], "costs": result["costs"], "reason": None}
        else:
            bid_entry = {"bid": None, "costs": None, "reason": result["reason"]}

        self._publish(topics.BID_PLACED, {
            "taskId": task_id,
            "robotId": self.robot_id,
            **bid_entry,
        })

        if result["eligible"]:
            self._log(f"[AUCTION] {task_id} bid from {self.robot_id}: {result['bid']:.2f}")
        else:
            self._log(f"[AUCTION] {task_id} {self.robot_id} not bidding ({result['reason']})")

    def on_bid_placed(self, payload: dict, timestamp: float) -> None:
        task_id = payload.get("taskId", "")
        round_ = self.rounds.get(task_id)
        if not round_ or round_["done"]:
            return
        robot_id = payload.get("robotId", "")
        if robot_id not in round_["bids"]:
            round_["bids"][robot_id] = payload
        self._try_finalize(task_id, timestamp)

    def on_robot_telemetry(self, payload: dict) -> None:
        robot_id = payload.get("robotId", "")
        if robot_id:
            self.fleet[robot_id] = payload

    def on_task_resolved(self, payload: dict) -> None:
        self.rounds.pop(payload.get("taskId", ""), None)

    def forget_round(self, task_id: str) -> None:
        self.rounds.pop(task_id, None)

    # ------------------------------------------------------------------
    # Bidding computation
    # ------------------------------------------------------------------

    def compute_bid(self, pickup: dict, dropoff: dict) -> dict:
        robot = self._get_robot()

        # Eligibility check
        if not robot.get("online", True):
            return {"eligible": False, "reason": "offline", "bid": None, "costs": None}
        current_task = robot.get("currentTaskId")
        status = robot.get("status", "IDLE")
        if current_task and status != "COMPLETED":
            return {"eligible": False, "reason": "busy", "bid": None, "costs": None}

        rx, ry = robot.get("x", 0.0), robot.get("y", 0.0)
        px, py = pickup.get("x", 0.0), pickup.get("y", 0.0)
        dx, dy = dropoff.get("x", 0.0), dropoff.get("y", 0.0)

        # Travel cost: use the same global A* planner as the motion controller
        # whenever world bounds are available. This keeps auction costs aligned
        # with the path the AMR will actually drive.
        bounds = self._get_world_bounds()
        if bounds:
            try:
                planner = AStarPlanner(
                    bounds[0], bounds[1], robot_radius=float(robot.get("radius", 0.4)),
                    safety_margin=0.1, resolution=0.25,
                )
                pickup_path = planner.plan((rx, ry), (px, py), self._get_obstacles())
                delivery_path = planner.plan((px, py), (dx, dy), self._get_obstacles())
                travel = path_distance(pickup_path) + path_distance(delivery_path)
            except PathNotFoundError:
                return {"eligible": False, "reason": "no_path", "bid": None, "costs": None}
        else:
            travel = _dist(rx, ry, px, py)
            if self._straight_blocked(rx, ry, px, py):
                travel *= BLOCKED_PENALTY
            travel += _dist(px, py, dx, dy)
        travel = round(travel * 100) / 100

        # Congestion cost
        congestion = 0.0
        for peer in self.fleet.values():
            if not peer.get("online", True):
                continue
            if peer.get("robotId") == self.robot_id:
                continue
            peer_x, peer_y = peer.get("x", 0.0), peer.get("y", 0.0)
            if (_dist(peer_x, peer_y, px, py) < CONGESTION_RADIUS or
                    _dist(peer_x, peer_y, dx, dy) < CONGESTION_RADIUS):
                congestion += CONGESTION_COST

        # Battery cost
        battery = robot.get("battery", 100.0)
        battery_cost = (100 - battery) * BATTERY_WEIGHT
        if battery < BATTERY_WARN:
            battery_cost += BATTERY_LOW_PENALTY
        battery_cost = round(battery_cost * 100) / 100

        workload = 0.0
        total_bid = round((travel + congestion + battery_cost + workload) * 100) / 100

        return {
            "eligible": True,
            "bid": total_bid,
            "reason": None,
            "costs": {
                "travel": travel,
                "congestion": congestion,
                "battery": battery_cost,
                "workload": workload,
            },
        }

    def _straight_blocked(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        obstacles = self._get_obstacles()
        if not obstacles:
            return False
        d = _dist(x1, y1, x2, y2)
        steps = max(2, math.ceil(d / 0.4))
        for obs in obstacles:
            for i in range(1, steps):
                t = i / steps
                px = x1 + (x2 - x1) * t
                py = y1 + (y2 - y1) * t
                if hasattr(obs, "inflated_contains") and obs.inflated_contains(px, py, 0.35):
                    return True
        return False

    # ------------------------------------------------------------------
    # Finalization (only if coordinator commit role enabled)
    # ------------------------------------------------------------------

    def _try_finalize(self, task_id: str, now: float) -> None:
        round_ = self.rounds.get(task_id)
        if not round_ or round_["done"]:
            return
        if self.disable_finalize:
            return
        all_responded = len(round_["bids"]) >= round_["rosterSize"]
        deadline_passed = now >= round_["deadline"]
        if all_responded or deadline_passed:
            self._finalize(task_id)

    def _finalize(self, task_id: str) -> None:
        round_ = self.rounds.pop(task_id, None)
        if not round_ or round_["done"]:
            return
        round_["done"] = True
        bids = list(round_["bids"].values())
        winner = select_winner(bids)
        committed = False
        if winner and self._coordinator_commit is not None:
            try:
                committed = bool(self._coordinator_commit(task_id, winner, bids))
            except Exception as exc:
                self._log(f"[AUCTION] coordinator commit error for {task_id}: {exc}")
                committed = False
        self._publish(topics.AUCTION_RESULT, {
            "taskId": task_id,
            "winner": winner,
            "bids": bids,
            "committed": committed,
        })

    # ------------------------------------------------------------------
    # Periodic tick (call from main loop)
    # ------------------------------------------------------------------

    def tick(self, dt: float, now: float) -> None:
        self._telemetry_timer -= dt
        if self._telemetry_timer <= 0:
            self._telemetry_timer += TELEMETRY_PERIOD
            self._publish_telemetry()

        if self.disable_finalize:
            return
        for task_id, round_ in list(self.rounds.items()):
            if not round_["done"] and now >= round_["deadline"]:
                self._finalize(task_id)

    def _publish_telemetry(self) -> None:
        robot = self._get_robot()
        self._publish(topics.ROBOT_TELEMETRY, {
            "robotId": self.robot_id,
            "x": robot.get("x", 0.0),
            "y": robot.get("y", 0.0),
            "heading": robot.get("heading", 0.0),
            "status": robot.get("status", "IDLE"),
            "battery": robot.get("battery", 100.0),
            "currentTaskId": robot.get("currentTaskId"),
            "blocked": robot.get("blocked", False),
            "online": robot.get("online", True),
        })
