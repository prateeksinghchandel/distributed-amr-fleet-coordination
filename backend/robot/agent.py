"""
agent.py — FleetAgent: auction bidding logic for an individual Python AMR.

Mirrors FleetAgent.js:
  - Subscribes to TASK_NEW, BID_PLACED, AUCTION_COMMIT, ROBOT_TELEMETRY,
    TASK_ASSIGNED, TASK_CANCELLED
  - Computes multi-factor bids (travel + congestion + battery + workload)
  - SERVER_AUCTION (default): finalization is DISABLED on robot nodes
    (coordinatorCommit only; disableFinalize=True). The coordinator agent is
    the sole finalizer.
  - P2P_AUCTION: robots exchange bids and each robot runs
    common.auction.select_winner at the deadline. The winner publishes an
    AUCTION_COMMIT, waits a commit window for conflicting commits, then
    self-publishes TASK_ASSIGNED and starts execution.
  - Publishes own telemetry every TELEMETRY_PERIOD seconds
"""

from __future__ import annotations
import math
import time
from typing import Optional, Callable

from common import topics
from common.auction import (
    AuctionMode,
    B_ABORTED,
    B_COMMITTED,
    B_CONFLICT,
    B_ROUND,
    B_SELF_ASSIGNED,
    B_WAITING_FINALIZATION,
    COMMIT_WINDOW_S,
    select_winner as select_winner_shared,
)
from robot.battery import BATTERY_CRITICAL_THRESHOLD
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
# Bid-penalty threshold (< BATTERY_WARN ⇒ +BATTERY_LOW_PENALTY). This is
# deliberately distinct from the physical return-to-charge threshold
# (robot/battery.py BATTERY_WARN_THRESHOLD) and must mirror FleetAgent.js.
BATTERY_WARN = 20.0
BATTERY_LOW_PENALTY = 30.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def select_winner(bids: list[dict]) -> Optional[str]:
    """Select lowest-bid robot. Tie-break: lexicographically smaller robotId.

    Delegates to the shared deterministic protocol function in
    common/auction.py so server and robots always agree.
    """
    return select_winner_shared({
        b.get("robotId", ""): {"cost": b.get("bid")}
        for b in bids
    })


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
    eligible_filter : Optional[callable(robot_id) → bool]
                  Only for the coordinator finalizer (SERVER_AUCTION): narrows
                  the candidate set with the authoritative availability ledger
                  before winner selection. Busy robots can never win a round
                  even if their telemetry lags.
    auction_mode : str       — AuctionMode.SERVER_AUCTION (default) or P2P_AUCTION
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
        eligible_filter: Optional[Callable[[str], bool]] = None,
        auction_mode: str = AuctionMode.SERVER_AUCTION,
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
        self.eligible_filter = eligible_filter
        self.auction_mode = AuctionMode.normalize(auction_mode)

        # fleet: robotId → latest telemetry dict
        self.fleet: dict[str, dict] = {
            snap["robotId"]: snap for snap in get_fleet_snapshot()
        }

        # auction rounds: auctionKey → round dict (auctionId when present, else taskId)
        self.rounds: dict[str, dict] = {}

        self._telemetry_timer = TELEMETRY_PERIOD
        self._active = True

    # ------------------------------------------------------------------
    # Round keying
    # ------------------------------------------------------------------

    @staticmethod
    def _auction_key(payload: dict) -> str:
        """Uniquely identify an auction round, preferring the explicit auctionId."""
        return payload.get("auctionId") or payload.get("taskId") or ""

    # ------------------------------------------------------------------
    # Message handlers  (called from message loop / subscription callbacks)
    # ------------------------------------------------------------------

    def on_task_new(self, payload: dict, timestamp: float) -> None:
        task_id = payload.get("taskId", "")
        auction_id = payload.get("auctionId")
        key = auction_id or task_id
        if not key:
            return

        existing = self.rounds.get(key)
        if existing is not None and not existing["done"]:
            return

        pickup = payload.get("pickup", {})
        dropoff = payload.get("dropoff", {})
        p2p = self.auction_mode == AuctionMode.P2P_AUCTION

        roster_size = max(1, len(self._get_fleet_snapshot()))
        self.rounds[key] = {
            "taskId": task_id,
            "auctionId": auction_id,
            "bids": {},
            "rosterSize": roster_size,
            "deadline": timestamp + DEADLINE,
            "done": False,
            "p2p": p2p,
            "phase": B_ROUND,
            "winner": None,
            "self_won": False,
            "external_winner": None,
            "commit_at": None,
            "subject": task_id,
        }

        robot = self._get_robot()
        if not robot.get("online", True):
            return

        result = self.compute_bid(pickup, dropoff)
        if result["eligible"]:
            bid_entry = {"bid": result["bid"], "costs": result["costs"], "reason": None,
                         "taskId": task_id}
        else:
            bid_entry = {"bid": None, "costs": None, "reason": result["reason"],
                         "taskId": task_id}

        bid_payload = {
            "taskId": task_id,
            "robotId": self.robot_id,
            **bid_entry,
        }
        if p2p:
            bid_payload["auctionId"] = key
        self._publish(topics.BID_PLACED, bid_payload)

        # Own bid is part of the local bid set so the deadline-time
        # select_winner sees it even if our echo is not redelivered.
        if p2p:
            self.rounds[key]["bids"][self.robot_id] = bid_payload

        if result["eligible"]:
            self._log(f"[AUCTION] {task_id} bid from {self.robot_id}: {result['bid']:.2f}")
        else:
            self._log(f"[AUCTION] {task_id} {self.robot_id} not bidding ({result['reason']})")

    def on_bid_placed(self, payload: dict, timestamp: float) -> None:
        key = self._auction_key(payload)
        round_ = self.rounds.get(key)
        if not round_ or round_["done"]:
            return
        robot_id = payload.get("robotId", "")
        if not robot_id:
            return
        # Duplicate bids from the same robot are ignored; the first bid wins.
        if robot_id not in round_["bids"]:
            round_["bids"][robot_id] = payload
        if not round_["p2p"]:
            self._try_finalize(key, timestamp)

    def on_auction_commit(self, payload: dict, timestamp: float) -> None:
        """Peer AUCTION_COMMIT handling — P2P_AUCTION only (server-mode ignores)."""
        key = payload.get("auctionId") or payload.get("taskId", "")
        round_ = self.rounds.get(key)
        if not round_ or not round_["p2p"] or round_["done"]:
            return
        source = payload.get("robotId", "")
        winner = payload.get("winner")
        if not source or not winner:
            return
        if source == self.robot_id:
            return  # our own echo — no-op

        if winner != self.robot_id:
            round_["winner"] = winner
            round_["external_winner"] = winner
            if round_["self_won"]:
                # We already committed believing we won, but a peer claims a
                # different winner. Divergent view ⇒ conflict: abort and never
                # execute.
                round_["done"] = True
                round_["phase"] = B_CONFLICT
                self._publish(topics.AUCTION_RESULT, {
                    "taskId": round_["subject"],
                    "auctionId": round_["auctionId"],
                    "winner": winner,
                    "bids": list(round_["bids"].values()),
                    "committed": False,
                    "conflict": True,
                })
                self._log(
                    f"[AUCTION] {round_['subject']} CONFLICT — {source} commits "
                    f"{winner}, we computed {self.robot_id}; aborting"
                )
            else:
                # Not finalised yet: remember the observed winner. When we
                # finalise, if we disagree we abort without ever committing.
                round_["phase"] = B_COMMITTED
            return

        # winner == self but committed by a peer: a peer claiming we won is a
        # protocol violation (only the winner self-commits) — ignore.
        self._log(
            f"[AUCTION] {round_['subject']} unexpected commit from {source} "
            f"claiming {winner}; ignoring"
        )

    def on_robot_telemetry(self, payload: dict) -> None:
        robot_id = payload.get("robotId", "")
        if robot_id:
            self.fleet[robot_id] = payload

    def on_task_resolved(self, payload: dict) -> None:
        self._forget_task_rounds(payload.get("taskId", ""))

    def forget_round(self, task_id: str) -> None:
        self._forget_task_rounds(task_id)

    def _forget_task_rounds(self, task_id: str) -> None:
        """Drop rounds belonging to a task (matched by taskId, any keying)."""
        for key in [k for k, r in self.rounds.items() if r["subject"] == task_id]:
            self.rounds.pop(key, None)

    # ------------------------------------------------------------------
    # Bidding computation
    # ------------------------------------------------------------------

    def compute_bid(self, pickup: dict, dropoff: dict) -> dict:
        robot = self._get_robot()

        # Eligibility check
        if not robot.get("online", True):
            return {"eligible": False, "reason": "offline", "bid": None, "costs": None}
        if robot.get("battery", 100.0) <= BATTERY_CRITICAL_THRESHOLD:
            return {"eligible": False, "reason": "low_battery", "bid": None, "costs": None}
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
        if self.eligible_filter is not None:
            # Narrow the candidate set to robots the authoritative ledger says
            # are available (one active assignment per robot invariant).
            available_bids = [b for b in bids if self.eligible_filter(b.get("robotId"))]
            if len(available_bids) != len(bids):
                self._log(
                    f"[AUCTION] {task_id} filtered {len(bids) - len(available_bids)} "
                    f"ineligible bid(s) (ledger busy)"
                )
            bids = available_bids
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
            "auctionId": round_.get("auctionId"),
            "winner": winner,
            "bids": bids,
            "committed": committed,
        })

    # ------------------------------------------------------------------
    # P2P finalization — every robot runs select_winner at the deadline
    # ------------------------------------------------------------------

    def _finalize_p2p(self, key: str, now: float) -> None:
        round_ = self.rounds.get(key)
        if not round_ or round_["done"] or round_.get("frozen", False):
            return
        round_["frozen"] = True
        bids = list(round_["bids"].values())
        winner = select_winner(bids)
        round_["winner"] = winner

        if winner is None:
            round_["phase"] = B_ABORTED
            round_["done"] = True
            self._log(f"[AUCTION] {round_['subject']} no eligible robot — no commit")
            self._publish(topics.AUCTION_RESULT, {
                "taskId": round_["subject"],
                "auctionId": round_["auctionId"],
                "winner": None,
                "bids": bids,
                "committed": False,
            })
            return

        # If a peer already claimed a different winner before we finalised, our
        # view is divergent — abort without ever committing (never execute).
        external = round_.get("external_winner")
        if winner == self.robot_id and external is not None and external != self.robot_id:
            round_["done"] = True
            round_["phase"] = B_CONFLICT
            self._publish(topics.AUCTION_RESULT, {
                "taskId": round_["subject"],
                "auctionId": round_["auctionId"],
                "winner": external,
                "bids": bids,
                "committed": False,
                "conflict": True,
            })
            self._log(
                f"[AUCTION] {round_['subject']} CONFLICT at finalize — peer "
                f"committed {external}, we computed {self.robot_id}; aborting"
            )
            return

        round_["phase"] = B_WAITING_FINALIZATION
        if winner == self.robot_id:
            if self._self_blocked():
                # We'd win this round but already hold (or have committed to) a
                # different task. Abort WITHOUT committing so the server ledger
                # times the round out and re-auctions the task cleanly. This
                # closes the P2P race where consecutive rounds could otherwise
                # land two tasks on the same robot.
                round_["done"] = True
                round_["phase"] = B_ABORTED
                self._log(
                    f"[AUCTION] {round_['subject']} {self.robot_id} would win but "
                    f"is already busy — aborting without commit"
                )
                self._publish(topics.AUCTION_RESULT, {
                    "taskId": round_["subject"],
                    "auctionId": round_["auctionId"],
                    "winner": None,
                    "bids": bids,
                    "committed": False,
                })
                return
            round_["self_won"] = True
            round_["commit_at"] = now + COMMIT_WINDOW_S
            commit_payload = {
                "taskId": round_["subject"],
                "auctionId": round_["auctionId"],
                "robotId": self.robot_id,
                "winner": self.robot_id,
                "bidCount": len(bids),
                "bids": bids,
            }
            self._publish(topics.AUCTION_COMMIT, commit_payload)
            self._log(
                f"[AUCTION] {round_['subject']} {self.robot_id} wins "
                f"(bid {self._own_bid(bids)}) → AUCTION_COMMIT"
            )
        else:
            self._log(f"[AUCTION] {round_['subject']} winner = {winner} (awaiting commit)")

    def _self_blocked(self) -> bool:
        """Robot-side busy check for P2P self-commits.

        A robot must never commit for a second task while it already executes
        one or is awaiting assignment of an earlier self-win.
        """
        robot = self._get_robot()
        if robot.get("currentTaskId") and robot.get("status") != "COMPLETED":
            return True
        for _, r in self.rounds.items():
            if r.get("self_won") and not r.get("done"):
                return True
        return False

    def _own_bid(self, bids: list[dict]) -> str:
        for b in bids:
            if b.get("robotId") == self.robot_id:
                return f"{b.get('bid')}"
        return "-"

    def _self_assign_p2p(self, key: str, now: float) -> None:
        """Winner self-publishes its assignment after the commit window."""
        round_ = self.rounds.get(key)
        if not round_ or round_["done"] or not round_.get("self_won"):
            return
        if now < round_["commit_at"]:
            return
        round_["done"] = True
        round_["phase"] = B_SELF_ASSIGNED
        bids = list(round_["bids"].values())
        self._publish(topics.TASK_ASSIGNED, {
            "taskId": round_["subject"],
            "robotId": self.robot_id,
            "source": "auction",
        })
        self._publish(topics.AUCTION_RESULT, {
            "taskId": round_["subject"],
            "auctionId": round_["auctionId"],
            "winner": self.robot_id,
            "bids": bids,
            "committed": True,
            "conflict": None,
        })
        self._log(f"[AUCTION] {round_['subject']} committed to {self.robot_id} (P2P self-assign)")

    # ------------------------------------------------------------------
    # Periodic tick (call from main loop)
    # ------------------------------------------------------------------

    def tick(self, dt: float, now: float) -> None:
        self._telemetry_timer -= dt
        if self._telemetry_timer <= 0:
            self._telemetry_timer += TELEMETRY_PERIOD
            self._publish_telemetry()

        if self.auction_mode == AuctionMode.P2P_AUCTION:
            if self.rounds:
                for key in list(self.rounds):
                    round_ = self.rounds.get(key)
                    if not round_ or round_["done"]:
                        continue
                    if not round_.get("frozen") and now >= round_["deadline"]:
                        self._finalize_p2p(key, now)
                    if round_.get("self_won"):
                        self._self_assign_p2p(key, now)
            return

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
