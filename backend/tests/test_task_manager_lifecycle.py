"""
test_task_manager_lifecycle.py — Unit tests for the improved task lifecycle,
auction scheduling and fleet load-balancing rules.

Covers:
  * AUCTIONING lifecycle (announce → claim → ASSIGNED; requeue → PENDING)
  * ledger-based availability (one active assignment per robot)
  * eligible-filter winner selection (busy robots never win a round)
  * assignment watchdog (stalled auction → re-auction, manual → FAILED)
  * completed robots freed for new tasks (COMpleted hold carries the task id)
  * all-busy defer + re-announce when a robot frees up
  * metrics counters
"""

from __future__ import annotations

from common.auction import AuctionMode
from common.models import TaskStatus, RobotStatus
from server.server_node import ServerNode
from server.task_manager import TaskManager, ASSIGN_WATCHDOG_S
from server.warehouse import Point2D, build_from_preset
from robot.agent import FleetAgent
from robot.controller import RobotState


class FakeBus:
    def __init__(self):
        self.handlers = {}
        self.messages = []

    def subscribe(self, topic, callback):
        self.handlers.setdefault(topic, []).append(callback)
        return lambda: None

    def publish(self, topic, payload):
        self.messages.append((topic, payload))
        for callback in list(self.handlers.get(topic, [])):
            callback(topic, payload)


class _Log:
    def info(self, *_a, **_k):
        pass

    def debug(self, *_a, **_k):
        pass

    def warning(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass


def _robot(rid, x, y, status="IDLE", task_id=None, battery=100.0, online=True):
    return {
        "robotId": rid, "x": x, "y": y, "heading": 0.0,
        "status": status, "battery": battery,
        "currentTaskId": task_id, "blocked": False, "online": online,
    }


def make_manager(states, mode=AuctionMode.SERVER_AUCTION):
    layout = build_from_preset("MICRO_FULFILLMENT")
    bus = FakeBus()
    mgr = TaskManager(layout, bus.publish, log=lambda _m: None, auction_mode=mode)
    mgr.fleet = {s["robotId"]: s for s in states}
    return mgr, bus


# ---------------------------------------------------------------------------
# AUCTIONING lifecycle
# ---------------------------------------------------------------------------

def test_announce_sets_auctioning_then_claim_assigns():
    mgr, _ = make_manager([_robot("AMR1", 10.0, 5.0), _robot("AMR2", 20.0, 5.0)])
    task = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=True)
    assert task.status is TaskStatus.AUCTIONING
    assert mgr.auction_in_flight == task.id

    # a round resolution claims the task from {PENDING, AUCTIONING}
    assert mgr.assign_task(task.id, "AMR1") is True
    assert task.status is TaskStatus.ASSIGNED
    assert task.assigned_robot_id == "AMR1"
    assert task.assigned_at is not None

    stats = mgr.task_stats()
    assert stats["auctioning"] == 0
    assert stats["active"] == 1


def test_retry_resets_auctioning_to_pending_before_requeue():
    mgr, bus = make_manager([_robot("AMR1", 10.0, 5.0)])
    task = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=True)
    assert task.status is TaskStatus.AUCTIONING

    # P2P-style: no commit arrives, so the manager requeues the round
    mgr._retry_or_defer(task.id)
    assert task.status is TaskStatus.AUCTIONING   # re-announced immediately
    assert mgr.auction_attempt[task.id] == 2
    assert mgr.metrics["counters"]["retried"] == 1


# ---------------------------------------------------------------------------
# Ledger-based availability (one active assignment per robot)
# ---------------------------------------------------------------------------

def test_ledger_overrides_lagging_telemetry():
    states = [_robot("AMR1", 10.0, 5.0), _robot("AMR2", 20.0, 5.0)]
    mgr, _ = make_manager(states)
    t1 = mgr.create_task(Point2D(15.0, 4.0), Point2D(16.0, 4.0), announce=False)
    assert mgr.assign_task(t1.id, "AMR2") is True

    # AMR2's telemetry still looks free, but the authoritative ledger says busy
    assert mgr.is_robot_available("AMR2") is False
    assert mgr.is_robot_available("AMR1") is True

    # a second task can never be assigned to the same robot
    t2 = mgr.create_task(Point2D(24.0, 3.0), Point2D(24.0, 5.0), announce=False)
    assert mgr.assign_task(t2.id, "AMR2") is False
    assert mgr.assign_task(t2.id, "AMR1") is True


def test_p2p_commit_to_busy_robot_rejected():
    mgr, _ = make_manager(
        [_robot("AMR1", 10.0, 5.0), _robot("AMR2", 20.0, 5.0)],
        mode=AuctionMode.P2P_AUCTION,
    )
    other = mgr.create_task(Point2D(15.0, 4.0), Point2D(16.0, 4.0), announce=False)
    assert mgr.assign_task(other.id, "AMR2") is True

    task = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=True)
    assert task.status is TaskStatus.AUCTIONING

    # AMR2 (already active) tries to commit the new round — the ledger rejects it
    assert mgr.apply_auction_commit(task.id, f"{task.id}:A1", "AMR2", []) is False
    assert task.assigned_robot_id is None
    assert task.status is TaskStatus.AUCTIONING
    assert mgr.metrics["counters"]["auction_failures"] == 1


# ---------------------------------------------------------------------------
# SERVER finalization excludes ledger-busy robots from winning
# ---------------------------------------------------------------------------

def test_server_finalize_never_selects_ledger_busy_robot():
    from common import topics
    bus = FakeBus()
    layout = build_from_preset("MICRO_FULFILLMENT")
    server = ServerNode(layout, layout.roster(), bus, _Log(), task_count=0)
    states = [
        RobotState(id="AMR1", x=25.0, y=3.0),
        RobotState(id="AMR2", x=15.0, y=3.0),
        RobotState(id="AMR3", x=18.0, y=10.0),
    ]
    snapshots = [s.to_dict() for s in states]
    agents = []
    for state in states:
        agent = FleetAgent(
            robot_id=state.id, get_robot=state.to_dict, publish_cb=bus.publish,
            log=lambda _m: None, get_obstacles=lambda: [],
            get_fleet_snapshot=lambda snaps=snapshots: snaps,
            disable_finalize=True,
        )
        agents.append(agent)
    for agent in agents:
        for snapshot in snapshots:
            agent.on_robot_telemetry(snapshot)
        bus.subscribe(topics.TASK_NEW, lambda t, p, a=agent: a.on_task_new(p, 0.0))
        bus.subscribe(topics.ROBOT_TELEMETRY, lambda t, p, a=agent: a.on_robot_telemetry(p))
    for state in states:
        bus.publish(topics.ROBOT_TELEMETRY, state.to_dict())

    # task 1 sits on AMR2's doorstep — AMR2 must win it.
    t1 = server.tasks.create_task(Point2D(15.0, 4.0), Point2D(16.0, 4.0), announce=True)
    assert t1.assigned_robot_id == "AMR2"

    # task 2 also favours AMR2 (closest), but AMR2 is busy in the ledger and its
    # telemetry lags. The coordinator must fall through to the next-best winner.
    t2 = server.tasks.create_task(Point2D(24.0, 3.0), Point2D(24.0, 5.0), announce=True)
    assert t2.assigned_robot_id == "AMR1"
    assert t2.assigned_robot_id != "AMR2"
    # AMR2 still holds exactly one ledger assignment
    ledger = [(t.assigned_robot_id, t.status.value) for t in server.tasks.tasks]
    assert ledger.count(("AMR2", "ASSIGNED")) == 1


# ---------------------------------------------------------------------------
# Assignment watchdog
# ---------------------------------------------------------------------------

def test_watchdog_reauctions_stalled_auction_task():
    mgr, _ = make_manager([_robot("AMR1", 10.0, 5.0)])
    task = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=True)
    assert mgr.assign_task(task.id, "AMR1") is True
    assert task.status is TaskStatus.ASSIGNED

    # the robot never picks the task up; fake telemetry stays stale/idle
    task.assigned_at -= ASSIGN_WATCHDOG_S + 1.0
    mgr.sync_tasks()

    assert task.assigned_robot_id is None
    assert task.status is TaskStatus.AUCTIONING       # fresh round re-announced
    assert mgr.auction_in_flight == task.id
    assert mgr.metrics["counters"]["recovered"] == 1


def test_watchdog_marks_stalled_manual_task_failed():
    mgr, _ = make_manager([_robot("AMR1", 10.0, 5.0)])
    task = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=False)
    assert mgr.assign_task(task.id, "AMR1", source="manual") is True

    task.assigned_at -= ASSIGN_WATCHDOG_S + 1.0
    mgr.sync_tasks()

    # manual assignments are never silently re-auctioned
    assert task.status is TaskStatus.FAILED
    assert task.assigned_robot_id == "AMR1"
    assert mgr.metrics["counters"]["recovered"] == 1


# ---------------------------------------------------------------------------
# Completed robot freed for new tasks / load balancing
# ---------------------------------------------------------------------------

def test_completed_robot_freed_after_sync_and_can_take_new_task():
    mgr, _ = make_manager([])
    mgr.fleet = {"AMR1": _robot("AMR1", 10.0, 5.0)}
    a = mgr.create_task(Point2D(3.0, 3.0), Point2D(20.0, 15.0), announce=False)
    assert mgr.assign_task(a.id, "AMR1") is True
    assert mgr.is_robot_available("AMR1") is False

    # AMR1 finishes. During the COMPLETED hold the telemetry carries the task
    # id (controller keeps it until the hold expires), so the server ledger
    # can observe and record the completion.
    mgr.fleet["AMR1"] = _robot("AMR1", 20.0, 15.0, status="COMPLETED", task_id=a.id)
    mgr.sync_tasks()
    assert a.status is TaskStatus.COMPLETED
    assert a.completed_at is not None
    assert mgr.metrics["counters"]["completed"] == 1
    assert mgr.metrics["timing"]["completion_count"] == 1
    assert mgr.is_robot_available("AMR1") is True

    # the just-completed robot is immediately reusable — but only one at a time
    b = mgr.create_task(Point2D(4.0, 4.0), Point2D(10.0, 10.0), announce=False)
    assert mgr.assign_task(b.id, "AMR1") is True


def test_all_busy_defers_waiting_then_announces_when_freed():
    states = [
        _robot("AMR1", 10.0, 5.0, status="MOVING_TO_PICKUP", task_id="T0"),
        _robot("AMR2", 20.0, 5.0, status="MOVING_TO_PICKUP", task_id="T0"),
    ]
    mgr, _ = make_manager(states)
    task = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=True)
    assert task.status is TaskStatus.PENDING
    assert task.id in mgr.waiting_tasks
    assert mgr.auction_in_flight is None
    assert mgr.metrics["counters"]["auctions"] == 0

    # AMR1 frees up → the waiting task is reconsidered and announced
    mgr.fleet["AMR1"]["currentTaskId"] = None
    mgr.fleet["AMR1"]["status"] = "IDLE"
    mgr.reconsider_waiting()
    mgr.advance_auction()

    assert task.id not in mgr.waiting_tasks
    assert task.status is TaskStatus.AUCTIONING
    assert mgr.auction_in_flight == task.id
    assert mgr.metrics["counters"]["auctions"] == 1


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_metrics_counters_and_world_views():
    mgr, _ = make_manager([])
    mgr.fleet = {"AMR1": _robot("AMR1", 10.0, 5.0)}
    a = mgr.create_task(Point2D(3.0, 3.0), Point2D(20.0, 15.0), announce=True)
    assert mgr.task_stats()["auctioning"] == 1
    assert mgr.metrics["counters"]["created"] == 1
    assert mgr.metrics["counters"]["auctions"] == 1

    assert mgr.assign_task(a.id, "AMR1") is True
    a.created_at -= 2.0   # wall-clock is too fast for timing assertions
    mgr.fleet["AMR1"] = _robot("AMR1", 20.0, 15.0, status="COMPLETED", task_id=a.id)
    mgr.sync_tasks()
    assert mgr.metrics["counters"]["completed"] == 1
    assert mgr.task_stats()["completed"] == 1
    assert mgr.task_stats()["avgCompletionS"] > 0.0
    assert mgr.metrics_dict()["avgCompletionS"] > 0.0

    # world views expose the authoritative ledger for the dashboard
    world = mgr.world_tasks()
    assert len(world) == 1
    assert world[0]["status"] == "COMPLETED"
    assert world[0]["assignedRobotId"] == "AMR1"
    robots = mgr.robot_stats()
    assert robots[0]["available"] is True
    assert robots[0]["ledgerBusy"] is False

    # cancel + fail counters
    b = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=True)
    mgr.cancel_task(b.id)
    assert mgr.metrics["counters"]["cancelled"] == 1
    c = mgr.create_task(Point2D(5.0, 5.0), Point2D(25.0, 5.0), announce=False)
    mgr.assign_task(c.id, "AMR1")
    mgr.fleet["AMR1"] = _robot("AMR1", 5.0, 5.0, status="FAILED", task_id=c.id)
    mgr.sync_tasks()
    assert mgr.metrics["counters"]["failed"] == 1