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
from robot.battery import BATTERY_CRITICAL_THRESHOLD
from server.warehouse import WarehouseLayout, Point2D


MAX_AUCTION_RETRIES = 3
MAX_AUCTION_HISTORY = 20
MAX_RANDOM_TASKS = 100
ASSIGN_WATCHDOG_S = 8.0   # assigned but never picked up ⇒ recover (fake telemetry /
                          # dead robot window). Generous: real pickups happen in ~0.2 s.

# Statuses that keep a robot busy in the authoritative ledger (one active
# assignment per robot). COMPLETED/CANCELLED/FAILED are terminal and free it.
ACTIVE_STATUSES = frozenset({
    TaskStatus.ASSIGNED, TaskStatus.PICKING_UP, TaskStatus.DELIVERING,
})


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
    assigned_at: Optional[float] = None     # when the task was last committed
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

        # Lightweight fleet/task metrics published in world/state.
        self.metrics: dict = {
            "counters": {
                "created": 0, "completed": 0, "failed": 0, "cancelled": 0,
                "retried": 0, "auctions": 0, "auction_failures": 0,
                "recovered": 0,
            },
            "timing": {"completion_total_s": 0.0, "completion_count": 0},
        }

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
        self.metrics["counters"]["created"] += 1
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
    # Availability (authoritative scheduling gate)
    # ------------------------------------------------------------------

    def is_robot_available(self, robot_id: str) -> bool:
        """True when ``robot_id`` may take a NEW task.

        Combines the authoritative task ledger (one active assignment per
        robot) with a sanity check of the latest telemetry. A robot holding
        an active (ASSIGNED/PICKING_UP/DELIVERING) task in the ledger is busy
        even if its telemetry briefly lags; a robot whose last task is
        COMPLETED/CANCELLED/FAILED is free again.
        """
        for t in self.tasks:
            if (t.assigned_robot_id == robot_id and t.status in ACTIVE_STATUSES):
                return False
        robot = self.fleet.get(robot_id)
        if robot is None:
            return False
        if not robot.get("online", True):
            return False
        if robot.get("battery", 100.0) <= BATTERY_CRITICAL_THRESHOLD:
            return False
        if robot.get("currentTaskId") and robot.get("status") != RobotStatus.COMPLETED:
            return False
        return True

    def _any_eligible_robot(self) -> bool:
        return any(self.is_robot_available(rid) for rid in self.fleet)

    def advance_auction(self) -> None:
        if self.auction_in_flight:
            return
        if not self.auction_enabled:
            return
        while self.auction_queue:
            task_id = self.auction_queue.pop(0)
            task = self._find(task_id)
            if not task or not TaskStatus.available_to_claim(task.status):
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
            task.status = TaskStatus.AUCTIONING
            self.metrics["counters"]["auctions"] += 1
            self._log(f"[AUCTION] Task {task_id} announced to fleet (round A{attempt})")
            self._publish_announce(task, auction_id=auction_id)
            return

    def reconsider_waiting(self) -> None:
        if not self.waiting_tasks or not self._any_eligible_robot():
            return
        for task_id in list(self.waiting_tasks):
            task = self._find(task_id)
            if task and TaskStatus.available_to_claim(task.status):
                task.status = TaskStatus.PENDING
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
        The commit is validated against the authoritative ledger so a busy
        robot can never accumulate a second active assignment.
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
        if not TaskStatus.available_to_claim(task.status):
            self._log(f"[AUCTION] commit for {task_id} ignored: task is {task.status}")
            return False
        if not self.is_robot_available(winner):
            self._log(f"[AUCTION] commit for {task_id} ignored: {winner} is not available")
            self.metrics["counters"]["auction_failures"] += 1
            return False
        if auction_id:
            self._committed_rounds.add(auction_id)
            if len(self._committed_rounds) > MAX_AUCTION_HISTORY:
                self._committed_rounds.discard(next(iter(self._committed_rounds)))

        task.assigned_robot_id = winner
        task.assigned_source = "auction"
        task.assigned_at = time.time()
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
        if not TaskStatus.available_to_claim(task.status):
            self._log(f"Cannot assign {task_id}: task is {task.status}")
            return False
        if not self.is_robot_available(robot_id):
            robot = self.fleet.get(robot_id)
            if robot is None:
                self._log(f"Cannot assign {task_id}: robot {robot_id} not in fleet view")
            else:
                self._log(f"Cannot assign {task_id}: {robot_id} is not available")
            return False

        task.assigned_robot_id = robot_id
        task.assigned_source = source
        task.assigned_at = time.time()
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
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
            return
        task.status = TaskStatus.CANCELLED
        task.assigned_robot_id = None
        task.assigned_source = None
        self.metrics["counters"]["cancelled"] += 1
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

    def cancel_tasks_for_robot(self, robot_id: str) -> int:
        """Cancel all tasks assigned to a removed robot. Returns count cancelled."""
        count = 0
        for task in self.tasks:
            if task.assigned_robot_id == robot_id and task.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                task.status = TaskStatus.CANCELLED
                task.assigned_robot_id = None
                task.assigned_source = None
                task.assigned_at = None
                count += 1
        self.auction_queue = [t for t in self.auction_queue if t not in
                              {task.id for task in self.tasks if task.assigned_robot_id == robot_id}]
        if count > 0:
            self._log(f"Cancelled {count} task(s) for removed robot {robot_id}")
        return count

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

        already_committed = task.status in (
            TaskStatus.ASSIGNED, TaskStatus.PICKING_UP, TaskStatus.DELIVERING,
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
        )
        self._record_auction(task_id, winner, bids, committed or already_committed,
                             auction_id=auction_id, conflict=conflict)

        if not already_committed:
            if winner is None:
                task.status = TaskStatus.PENDING
                self.waiting_tasks.add(task_id)
                self._log(f"[AUCTION] Task {task_id} deferred (no eligible robot)")
            else:
                self._retry_or_defer(task_id)
        self.advance_auction()

    def _retry_or_defer(self, task_id: str) -> None:
        """Re-queue an unresolved round for another auction attempt (or defer)."""
        if self.auction_in_flight == task_id:
            self.auction_in_flight = None
            self.auction_started_at = None
        task = self._find(task_id)
        if not task or not TaskStatus.available_to_claim(task.status):
            return
        retries = self.auction_retries.get(task_id, 0) + 1
        if retries <= MAX_AUCTION_RETRIES:
            self.auction_retries[task_id] = retries
            task.status = TaskStatus.PENDING
            self.metrics["counters"]["retried"] += 1
            self.auction_queue.insert(0, task_id)
            self._log(f"[AUCTION] Task {task_id} re-queued (attempt {retries})")
        else:
            self.auction_retries.pop(task_id, None)
            task.status = TaskStatus.PENDING
            self.waiting_tasks.add(task_id)
            self.metrics["counters"]["auction_failures"] += 1
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
        """Update task statuses from fleet telemetry + assignment watchdog."""
        now = time.time()
        for task in self.tasks:
            if not task.assigned_robot_id:
                continue
            robot = self.fleet.get(task.assigned_robot_id)
            if not robot or not robot.get("online", True):
                # Assigned robot vanished or went offline before finishing.
                if task.status in ACTIVE_STATUSES:
                    self._recover_assignment(
                        task,
                        reason="robot offline/missing",
                        robot_online=bool(robot),
                    )
                continue
            if task.status not in ACTIVE_STATUSES:
                continue
            if robot.get("currentTaskId") != task.id:
                # Robot is alive but never picked up the assignment (or already
                # moved on) — the assignment watchdog recovers it. Manual
                # assignments are never silently re-auctioned.
                if (task.status == TaskStatus.ASSIGNED
                        and task.assigned_at is not None
                        and now - task.assigned_at > ASSIGN_WATCHDOG_S):
                    self._recover_assignment(task, reason="assigned but never picked up")
                continue

            status = robot.get("status", "IDLE")
            if status == RobotStatus.COMPLETED:
                if task.status != TaskStatus.COMPLETED:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = now
                    self.metrics["counters"]["completed"] += 1
                    self.metrics["timing"]["completion_total_s"] += now - task.created_at
                    self.metrics["timing"]["completion_count"] += 1
                    self._log(f"Task {task.id} completed by {task.assigned_robot_id}")
            elif status == RobotStatus.FAILED:
                if task.status != TaskStatus.FAILED:
                    task.status = TaskStatus.FAILED
                    self.metrics["counters"]["failed"] += 1
                    self._log(f"Task {task.id} failed ({task.assigned_robot_id})")
                    # A lost auction winner must not strand the task: re-auction
                    # it with a fresh round. Manual assignments stay untouched.
                    if task.assigned_source == "auction":
                        self._requeue_failed_winner(task.id)
            elif status == RobotStatus.PICKING:
                task.status = TaskStatus.PICKING_UP
            elif status in (RobotStatus.MOVING_TO_DROPOFF, RobotStatus.DROPPING):
                task.status = TaskStatus.DELIVERING

    def _recover_assignment(self, task: TaskRecord, reason: str,
                            robot_online: bool = True) -> None:
        """Free a stalling assignment.

        Auction-assigned tasks are re-auctioned so they can never park in
        ASSIGNED forever. Manual assignments are marked FAILED for the operator
        instead (never silently reassigned).
        """
        if task.assigned_source != "auction":
            task.status = TaskStatus.FAILED
            self.metrics["counters"]["recovered"] += 1
            self._log(f"Task {task.id} marked FAILED ({reason}; manual assignment)")
            return
        task.status = TaskStatus.PENDING
        task.assigned_robot_id = None
        task.assigned_source = None
        task.assigned_at = None
        self.waiting_tasks.discard(task.id)
        self.metrics["counters"]["recovered"] += 1
        self._log(f"Task {task.id} recovered ({reason}) — re-auctioning")
        self.auction_queue.insert(0, task.id)
        self.advance_auction()

    def _requeue_failed_winner(self, task_id: str) -> None:
        """Re-auction a task whose assigned (auction) robot has disappeared."""
        task = self._find(task_id)
        if not task:
            return
        task.status = TaskStatus.PENDING
        task.assigned_robot_id = None
        task.assigned_source = None
        task.assigned_at = None
        self.result_seen.discard(task_id)   # legacy taskId-keyed entries only
        retries = self.auction_retries.get(task_id, 0) + 1
        if retries > MAX_AUCTION_RETRIES:
            self.auction_retries.pop(task_id, None)
            self.waiting_tasks.add(task_id)
            self._log(f"[AUCTION] Task {task_id} deferred (failed winner, retries used up)")
            return
        self.auction_retries[task_id] = retries
        if self._any_eligible_robot():
            self.metrics["counters"]["retried"] += 1
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
        if task and TaskStatus.available_to_claim(task.status):
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

    # ------------------------------------------------------------------
    # Authoritative world state views (consumed by the dashboard)
    # ------------------------------------------------------------------

    def world_tasks(self) -> list[dict]:
        """Full task ledger for world/state — the dashboard mirrors this."""
        out = []
        for t in self.tasks:
            out.append({
                "id": t.id,
                "taskId": t.id,
                "status": t.status.value,
                "assignedRobotId": t.assigned_robot_id,
                "assignedSource": t.assigned_source,
                "pickup": {"x": t.pickup.x, "y": t.pickup.y},
                "dropoff": {"x": t.dropoff.x, "y": t.dropoff.y},
                "priority": t.priority,
                "createdAt": t.created_at,
                "assignedAt": t.assigned_at,
                "completedAt": t.completed_at,
            })
        return out

    def task_stats(self) -> dict:
        counts = {
            TaskStatus.PENDING: 0, TaskStatus.AUCTIONING: 0,
            TaskStatus.ASSIGNED: 0, TaskStatus.PICKING_UP: 0,
            TaskStatus.DELIVERING: 0, TaskStatus.COMPLETED: 0,
            TaskStatus.CANCELLED: 0, TaskStatus.FAILED: 0,
        }
        for t in self.tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        c = self.metrics["counters"]
        return {
            "total": len(self.tasks),
            "pending": counts[TaskStatus.PENDING],
            "auctioning": counts[TaskStatus.AUCTIONING],
            "active": counts[TaskStatus.ASSIGNED] + counts[TaskStatus.PICKING_UP]
                      + counts[TaskStatus.DELIVERING],
            "completed": counts[TaskStatus.COMPLETED],
            "failed": counts[TaskStatus.FAILED],
            "cancelled": counts[TaskStatus.CANCELLED],
            "waiting": len(self.waiting_tasks),
            "inFlight": self.auction_in_flight,
            "counters": c,
            "avgCompletionS": round(
                self.metrics["timing"]["completion_total_s"] /
                self.metrics["timing"]["completion_count"], 3)
                if self.metrics["timing"]["completion_count"] else 0.0,
        }

    def robot_stats(self) -> list[dict]:
        """Per-robot scheduling view: e.g. whether it may take a new task."""
        out = []
        for rid, robot in self.fleet.items():
            busy_ledger = any(
                t.assigned_robot_id == rid and t.status in ACTIVE_STATUSES
                for t in self.tasks
            )
            out.append({
                "robotId": rid,
                "status": robot.get("status", "IDLE"),
                "currentTaskId": robot.get("currentTaskId"),
                "battery": robot.get("battery", 100.0),
                "online": robot.get("online", True),
                "ledgerBusy": busy_ledger,
                "available": self.is_robot_available(rid),
            })
        return out

    def metrics_dict(self) -> dict:
        return {
            "counters": dict(self.metrics["counters"]),
            "avgCompletionS": self.task_stats().get("avgCompletionS", 0.0),
            "active": self.task_stats().get("active", 0),
            "waiting": len(self.waiting_tasks),
        }

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
