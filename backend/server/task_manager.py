"""
task_manager.py — Task lifecycle: creation, auction queuing, assignment, completion sync.

Mirrors FleetCoordinator.js task management logic in pure Python.

Auction modes
-------------
* SERVER_AUCTION (default) — the server runs the FleetAgent finalizer: it
  selects the winner from collected bids and publishes the assignment.
* P2P_AUCTION — the server is a passive task ledger. Robots exchange bids and
  each robot selects the winner at the deadline. The winning robot publishes an
  AUCTION_COMMIT which this manager applies idempotently (a task can never be
  assigned twice). The server never publishes TASK_ASSIGNED or AUCTION_RESULT
  for auctioned tasks in P2P mode.
"""

from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Callable
from common.auction import AuctionMode, DEADLINE_S, P2P_COMMIT_GRACE_S
from common.models import RobotStatus, TaskStatus, Point
from server.warehouse import WarehouseLayout, Point2D


MAX_AUCTION_RETRIES = 3
MAX_AUCTION_HISTORY = 20
MAX_RANDOM_TASKS = 100


@dataclass
class TaskRecord:
    """Internal task record."""
    id: str
    pickup: Point2D
    dropoff: Point2D
    priority: int = 1
    status: TaskStatus = TaskStatus.PENDING
    assigned_robot_id: Optional[str] = None
    assigned_source: Optional[str] = None   # "auction" | "manual"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def to_announce_dict(self) -> dict:
        return {
            "taskId": self.id,
            "pickup": {"x": self.pickup.x, "y": self.pickup.y},
            "dropoff": {"x": self.dropoff.x, "y": self.dropoff.y},
            "priority": self.priority,
        }

    def to_assign_dict(self, robot_id: str, source: str = "auction") -> dict:
        return {
            "taskId": self.id,
            "robotId": robot_id,
            "source": source,
        }

    def to_cancel_dict(self) -> dict:
        return {"taskId": self.id}


def _new_task_id() -> str:
    return f"T-{uuid.uuid4().hex[:6].upper()}"


class TaskManager:
    """
    Manages task lifecycle and auction queue.

    publish_cb: async or sync callable(topic: str, payload: dict) → None
    fleet:      dict[robot_id, telemetry_dict] — maintained externally
    log:        callable(str)
    """

    def __init__(
        self,
        layout: WarehouseLayout,
        publish_cb: Callable,
        log: Callable[[str], None],
        auction_mode: str = AuctionMode.SERVER_AUCTION,
    ):
        self.layout = layout
        self._publish = publish_cb
        self._log = log
        self.auction_mode = AuctionMode.normalize(auction_mode)

        self.tasks: list[TaskRecord] = []
        self.auction_queue: list[str] = []
        self.auction_in_flight: Optional[str] = None
        self.auction_retries: dict[str, int] = {}
        self.waiting_tasks: set[str] = set()
        self.result_seen: set[str] = set()
        self.auctions: list[dict] = []          # history for inspection
        self.auction_enabled: bool = True

        # P2P ledger helpers
        self.auction_attempt: dict[str, int] = {}       # taskId → announce count
        self.auction_started_at: Optional[float] = None # time of current round announce
        self._committed_rounds: set[str] = set()        # auctionIds already applied

        # fleet view injected from TelemetryManager
        self.fleet: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------

    def create_task(
        self,
        pickup: Point2D,
        dropoff: Point2D,
        priority: int = 1,
        announce: bool = False,
    ) -> Optional[TaskRecord]:
        """Create a task and optionally queue it for auction."""
        task = TaskRecord(
            id=_new_task_id(),
            pickup=pickup,
            dropoff=dropoff,
            priority=priority,
        )
        self.tasks.append(task)
        self._log(f"Task {task.id} created "
                  f"({pickup.x:.1f},{pickup.y:.1f}) → ({dropoff.x:.1f},{dropoff.y:.1f})")
        if announce:
            self.auction_queue.append(task.id)
            self.advance_auction()
        return task

    def generate_random_tasks(self, count: int) -> int:
        """Generate tasks from random shelf picks → delivery docks."""
        target = max(0, min(count, MAX_RANDOM_TASKS))
        created = 0
        attempts = 0
        while created < target and attempts < 2000:
            attempts += 1
            pickup = self.layout.random_pick_point()
            dropoff = self.layout.random_delivery_point()
            if pickup and dropoff:
                self.create_task(pickup, dropoff, announce=True)
                created += 1
        self._log(f"Random generation: {created} task(s) created")
        return created

    # ------------------------------------------------------------------
    # Auction queue management
    # ------------------------------------------------------------------

    def _any_eligible_robot(self) -> bool:
        for r in self.fleet.values():
            if r.get("online", True) and not r.get("currentTaskId"):
                return True
            if r.get("online", True) and r.get("status") == RobotStatus.COMPLETED:
                return True
        return False

    def advance_auction(self) -> None:
        if self.auction_in_flight:
            return
        if not self.auction_enabled:
            return
        while self.auction_queue:
            task_id = self.auction_queue.pop(0)
            task = self._find(task_id)
            if not task or task.status != TaskStatus.PENDING:
                continue
            if not self._any_eligible_robot() and self.fleet:
                self.waiting_tasks.add(task_id)
                self._log(f"[AUCTION] Task {task_id} deferred (no eligible robot)")
                continue
            self.auction_in_flight = task_id
            attempt = self.auction_attempt.get(task_id, 0) + 1
            self.auction_attempt[task_id] = attempt
            self.auction_started_at = time.time()
            auction_id = f"{task_id}:A{attempt}" if self.auction_mode == AuctionMode.P2P_AUCTION else None
            self._log(f"[AUCTION] Task {task_id} announced to fleet (round A{attempt})")
            self._publish_announce(task, auction_id=auction_id)
            return

    def reconsider_waiting(self) -> None:
        if not self.waiting_tasks or not self._any_eligible_robot():
            return
        for task_id in list(self.waiting_tasks):
            task = self._find(task_id)
            if task and task.status == TaskStatus.PENDING:
                self.auction_queue.append(task_id)
        self.waiting_tasks.clear()

    def _publish_announce(self, task: TaskRecord, auction_id: Optional[str] = None) -> None:
        from common import topics
        payload = task.to_announce_dict()
        if auction_id:
            payload["auctionId"] = auction_id
        self._publish(topics.TASK_NEW, payload)

    # ------------------------------------------------------------------
    # P2P ledger
    # ------------------------------------------------------------------

    def _expected_round(self, task_id: str) -> Optional[str]:
        """auctionId of the current in-flight round, or None when not announced."""
        if self.auction_in_flight != task_id:
            return None
        attempt = self.auction_attempt.get(task_id, 0)
        return f"{task_id}:A{attempt}" if attempt else None

    def apply_auction_commit(self, task_id: str, auction_id: str, winner: str,
                             bids: list[dict]) -> bool:
        """Apply a robot-published P2P commit to the task ledger (idempotent).

        The server never re-selects the winner: it only records the first valid
        commit and never publishes TASK_ASSIGNED/AUCTION_RESULT for the round.
        """
        task = self._find(task_id)
        if not task or task.status == TaskStatus.CANCELLED:
            return False
        if auction_id:
            if auction_id in self._committed_rounds:
                self._log(f"[AUCTION] duplicate commit for {auction_id} ignored")
                return False
            expected = self._expected_round(task_id)
            if expected and auction_id != expected:
                self._log(f"[AUCTION] stale commit {auction_id} (expected {expected}) ignored")
                return False
        if task.status != TaskStatus.PENDING:
            self._log(f"[AUCTION] commit for {task_id} ignored: task is {task.status}")
            return False
        if auction_id:
            self._committed_rounds.add(auction_id)
            if len(self._committed_rounds) > MAX_AUCTION_HISTORY:
                self._committed_rounds.discard(next(iter(self._committed_rounds)))

        task.assigned_robot_id = winner
        task.assigned_source = "auction"
        task.status = TaskStatus.ASSIGNED
        self.auction_queue = [t for t in self.auction_queue if t != task_id]
        self.auction_retries.pop(task_id, None)
        self.waiting_tasks.discard(task_id)
        if self.auction_in_flight == task_id:
            self.auction_in_flight = None
            self.auction_started_at = None
            self.advance_auction()
        self._record_auction(task_id, winner, bids, True, auction_id=auction_id)
        self._log(f"[AUCTION] P2P commit applied: {task_id} → {winner} (ledger)")
        return True

    # ------------------------------------------------------------------
    # Assignment and cancellation
    # ------------------------------------------------------------------

    def assign_task(self, task_id: str, robot_id: str, source: str = "auction") -> bool:
        """Commit a task to a robot. Returns True on success."""
        task = self._find(task_id)
        if not task:
            self._log(f"Cannot assign: task {task_id} not found")
            return False
        if task.status != TaskStatus.PENDING:
            self._log(f"Cannot assign {task_id}: task is {task.status}")
            return False
        robot = self.fleet.get(robot_id)
        if robot is None:
            self._log(f"Cannot assign {task_id}: robot {robot_id} not in fleet view")
            return False
        if not robot.get("online", True):
            self._log(f"Cannot assign {task_id}: {robot_id} is offline")
            return False
        if robot.get("currentTaskId") and robot.get("status") != RobotStatus.COMPLETED:
            self._log(f"Cannot assign {task_id}: {robot_id} is busy")
            return False

        task.assigned_robot_id = robot_id
        task.assigned_source = source
        task.status = TaskStatus.ASSIGNED
        self.auction_queue = [t for t in self.auction_queue if t != task_id]
        self.auction_retries.pop(task_id, None)
        self.waiting_tasks.discard(task_id)
        self.auction_attempt.pop(task_id, None)
        if self.auction_in_flight == task_id:
            self.auction_in_flight = None
            self.auction_started_at = None
            self.advance_auction()

        from common import topics
        sender = robot_id if source == "auction" else "server"
        self._publish(topics.TASK_ASSIGNED, task.to_assign_dict(robot_id, source))
        self._log(f"{robot_id} started task {task_id}" +
                  (" (via auction)" if source == "auction" else ""))
        return True

    def cancel_task(self, task_id: str) -> None:
        task = self._find(task_id)
        if not task:
            return
        task.status = TaskStatus.CANCELLED
        task.assigned_robot_id = None
        task.assigned_source = None
        self.auction_queue = [t for t in self.auction_queue if t != task_id]
        self.auction_retries.pop(task_id, None)
        self.waiting_tasks.discard(task_id)
        self.auction_attempt.pop(task_id, None)
        if self.auction_in_flight == task_id:
            self.auction_in_flight = None
            self.auction_started_at = None
            self.advance_auction()
        from common import topics
        self._publish(topics.TASK_CANCELLED, task.to_cancel_dict())
        self._log(f"Task {task_id} cancelled")

    # ------------------------------------------------------------------
    # Auction result handler (called by server_node on AUCTION_RESULT)
    # ------------------------------------------------------------------

    def handle_auction_result(self, payload: dict) -> None:
        task_id = payload.get("taskId", "")
        auction_id = payload.get("auctionId")
        winner = payload.get("winner")
        bids = payload.get("bids", [])
        committed = payload.get("committed", False)
        conflict = payload.get("conflict")
        result_key = auction_id or task_id

        task = self._find(task_id)
        if not task or task.status == TaskStatus.CANCELLED:
            return
        if result_key in self.result_seen:
            return
        self.result_seen.add(result_key)

        if self.auction_in_flight == task_id:
            self.auction_in_flight = None
            self.auction_started_at = None

        already_committed = task.status != TaskStatus.PENDING
        self._record_auction(task_id, winner, bids, committed or already_committed,
                             auction_id=auction_id, conflict=conflict)

        if not already_committed:
            if winner is None:
                self.waiting_tasks.add(task_id)
                self._log(f"[AUCTION] Task {task_id} deferred (no eligible robot)")
            else:
                self._retry_or_defer(task_id)
        self.advance_auction()

    def _retry_or_defer(self, task_id: str) -> None:
        """Re-queue an unresolved round for another auction attempt (or defer)."""
        task = self._find(task_id)
        if not task or task.status != TaskStatus.PENDING:
            return
        retries = self.auction_retries.get(task_id, 0) + 1
        if retries <= MAX_AUCTION_RETRIES:
            self.auction_retries[task_id] = retries
            self.auction_queue.insert(0, task_id)
            self._log(f"[AUCTION] Task {task_id} re-queued (attempt {retries})")
        else:
            self.auction_retries.pop(task_id, None)
            self.waiting_tasks.add(task_id)
            self._log(f"[AUCTION] Task {task_id} deferred (commit failed {retries}×)")
        self.advance_auction()

    def _record_auction(self, task_id: str, winner, bids: list, committed: bool,
                        auction_id: Optional[str] = None, conflict: Optional[bool] = None) -> None:
        entry = {
            "taskId": task_id,
            "winner": winner,
            "bids": [{"robotId": b.get("robotId"), "bid": b.get("bid"),
                       "costs": b.get("costs"), "reason": b.get("reason")}
                      for b in bids],
            "committed": committed,
        }
        if auction_id:
            entry["auctionId"] = auction_id
        if conflict is not None:
            entry["conflict"] = conflict
        self.auctions.append(entry)
        if len(self.auctions) > MAX_AUCTION_HISTORY:
            self.auctions.pop(0)

    # ------------------------------------------------------------------
    # Telemetry sync (called on each step)
    # ------------------------------------------------------------------

    def sync_tasks(self) -> None:
        """Update task statuses based on current fleet telemetry."""
        for task in self.tasks:
            if not task.assigned_robot_id:
                continue
            robot = self.fleet.get(task.assigned_robot_id)
            if not robot or robot.get("currentTaskId") != task.id:
                continue
            status = robot.get("status", "IDLE")
            if status == RobotStatus.COMPLETED:
                if task.status != TaskStatus.COMPLETED:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    self._log(f"Task {task.id} completed by {task.assigned_robot_id}")
            elif status == RobotStatus.FAILED or not robot.get("online", True):
                if task.status != TaskStatus.FAILED:
                    task.status = TaskStatus.FAILED
                    self._log(f"Task {task.id} failed ({task.assigned_robot_id})")
                    # A lost auction winner must not strand the task: re-auction
                    # it with a fresh round. Manual assignments stay untouched.
                    if task.assigned_source == "auction":
                        self._requeue_failed_winner(task.id)
            elif status == RobotStatus.PICKING:
                task.status = TaskStatus.PICKING_UP
            elif status in (RobotStatus.MOVING_TO_DROPOFF, RobotStatus.DROPPING):
                task.status = TaskStatus.DELIVERING

    def _requeue_failed_winner(self, task_id: str) -> None:
        """Re-auction a task whose assigned (auction) robot has disappeared."""
        task = self._find(task_id)
        if not task:
            return
        task.status = TaskStatus.PENDING
        task.assigned_robot_id = None
        task.assigned_source = None
        self.result_seen.discard(task_id)   # legacy taskId-keyed entries only
        retries = self.auction_retries.get(task_id, 0) + 1
        if retries > MAX_AUCTION_RETRIES:
            self.auction_retries.pop(task_id, None)
            self.waiting_tasks.add(task_id)
            self._log(f"[AUCTION] Task {task_id} deferred (failed winner, retries used up)")
            return
        self.auction_retries[task_id] = retries
        if self._any_eligible_robot():
            self.auction_queue.insert(0, task_id)
            self._log(f"[AUCTION] Task {task_id} re-auctioned after failed winner "
                      f"(attempt {retries})")
        else:
            self.waiting_tasks.add(task_id)
            self._log(f"[AUCTION] Task {task_id} deferred (no eligible robot after failure)")
        self.advance_auction()

    def sync_auction_timeout(self, now: float) -> None:
        """P2P only: re-queue a round for which no commit ever arrived."""
        if self.auction_mode != AuctionMode.P2P_AUCTION:
            return
        if not self.auction_in_flight or self.auction_started_at is None:
            return
        if now - self.auction_started_at < DEADLINE_S + P2P_COMMIT_GRACE_S:
            return
        task_id = self.auction_in_flight
        task = self._find(task_id)
        self.auction_in_flight = None
        self.auction_started_at = None
        if task and task.status == TaskStatus.PENDING:
            self._log(f"[AUCTION] P2P round timeout for {task_id} — no commit received")
            self._retry_or_defer(task_id)
        else:
            self.advance_auction()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find(self, task_id: str) -> Optional[TaskRecord]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def set_auction_enabled(self, enabled: bool) -> None:
        self.auction_enabled = bool(enabled)
        if not self.auction_enabled:
            self.auction_in_flight = None
            self.auction_started_at = None
            self.auction_queue.clear()
            self.auction_retries.clear()
            self.waiting_tasks.clear()
            self.auction_attempt.clear()
        self._log(f"[AUCTION] auto-assign via auction {'enabled' if self.auction_enabled else 'disabled'}")

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.PENDING)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self.tasks if t.status in (
            TaskStatus.ASSIGNED, TaskStatus.PICKING_UP, TaskStatus.DELIVERING))
