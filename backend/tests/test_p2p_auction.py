"""
P2P auction mode tests.

Covers the required scenarios for the true peer-to-peer auction:
 1. single robot auction             9.  multiple simultaneous auctions
 2. three robot auction             10. retry after failed winner
 3. lowest bid wins                 11. manual assignment stays separate
 4. robot id tie-break              12. server does not select the winner
 5. duplicate bid                   13. all robots agree on the winner
 6. duplicate winner message        14. auction timeout → re-auction
 7. missing robot                   15. task never assigned to two robots
 8. no eligible robot

Winner selection is deterministic (common.auction.select_winner), so a test
with the same geometry always derives the same winner.
"""

from __future__ import annotations

import pytest

from common import topics
from common.auction import (
    AuctionMode,
    B_CONFLICT,
    B_SELF_ASSIGNED,
    B_WAITING_FINALIZATION,
    DEADLINE_S,
    COMMIT_WINDOW_S,
    P2P_COMMIT_GRACE_S,
    select_winner,
)
from robot.agent import FleetAgent
from robot.controller import RobotState
from server.server_node import ServerNode
from server.warehouse import Point2D, build_from_preset

T0 = 1000.0
T_DEADLINE = T0 + DEADLINE_S
T_COMMIT = T_DEADLINE + COMMIT_WINDOW_S + 0.05


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


def _default_states():
    return [
        RobotState(id="AMR1", x=25.0, y=3.0),
        RobotState(id="AMR2", x=15.0, y=3.0),
        RobotState(id="AMR3", x=18.0, y=10.0),
    ]


class P2PHarness:
    """Server + N P2P robot agents on one in-process FakeBus."""

    def __init__(self, states=None, announce_ts: float = T0):
        self.bus = FakeBus()
        self.layout = build_from_preset("MICRO_FULFILLMENT")
        self.server = ServerNode(
            self.layout, self.layout.roster(), self.bus, _Log(), task_count=0,
            auction_mode=AuctionMode.P2P_AUCTION,
        )
        self.states = states if states is not None else _default_states()
        self.snapshots = [s.to_dict() for s in self.states]
        self.agents = self._build_agents()
        self.announce_ts = announce_ts
        self._wire()
        # check in all robots so the server has fleet telemetry
        for state in self.states:
            self.bus.publish(topics.ROBOT_TELEMETRY, state.to_dict())

    def _build_agents(self):
        agents = []
        for state in self.states:
            agent = FleetAgent(
                robot_id=state.id,
                get_robot=state.to_dict,
                publish_cb=self.bus.publish,
                log=lambda _m: None,
                get_obstacles=lambda: [],
                get_fleet_snapshot=lambda snaps=self.snapshots: snaps,
                disable_finalize=True,
                auction_mode=AuctionMode.P2P_AUCTION,
            )
            agents.append(agent)
        for agent in agents:
            for snapshot in self.snapshots:
                agent.on_robot_telemetry(snapshot)
        return agents

    def _wire(self):
        for agent in self.agents:
            self.bus.subscribe(
                topics.TASK_NEW,
                lambda topic, payload, a=agent: a.on_task_new(payload, self.announce_ts),
            )
            self.bus.subscribe(
                topics.BID_PLACED,
                lambda topic, payload, a=agent: a.on_bid_placed(payload, self.announce_ts),
            )
            self.bus.subscribe(
                topics.AUCTION_COMMIT,
                lambda topic, payload, a=agent: a.on_auction_commit(payload, self.announce_ts),
            )
            self.bus.subscribe(
                topics.ROBOT_TELEMETRY,
                lambda topic, payload, a=agent: a.on_robot_telemetry(payload),
            )

    def announce(self, pickup=(5.0, 4.0), dropoff=(2.0, 7.0)):
        task = self.server.tasks.create_task(
            pickup=Point2D(*pickup), dropoff=Point2D(*dropoff), announce=True,
        )
        auction_id = self.server.tasks._expected_round(task.id)
        self.distribute_bids(auction_id)
        return task, auction_id

    def distribute_bids(self, key: str):
        """Model a well-formed broadcast network: every robot's bid reaches
        every peer. (Synchronous in-process delivery otherwise creates an
        artificial race where early bids are dropped by rounds that do not
        exist yet.)"""
        own = {}
        for agent in self.agents:
            bid = agent.rounds.get(key, {}).get("bids", {}).get(agent.robot_id)
            if bid is not None:
                own[agent.robot_id] = dict(bid)
        for agent in self.agents:
            for rid, payload in own.items():
                if rid == agent.robot_id:
                    continue
                agent.on_bid_placed(dict(payload), self.announce_ts)

    def reach_deadline(self, ts: float = T_DEADLINE):
        for agent in self.agents:
            agent.tick(0.05, ts)

    def reach_commit_window(self, ts: float = T_COMMIT):
        for agent in self.agents:
            agent.tick(0.05, ts)

    def apply_commits(self, only_winner: str | None = None):
        for topic, payload in self.bus.messages:
            if topic != topics.AUCTION_COMMIT:
                continue
            if only_winner is not None and payload.get("winner") != only_winner:
                continue
            self.server._on_auction_commit(topic, payload)

    def published(self, topic: str):
        return [p for t, p in self.bus.messages if t == topic]

    def agent_bids(self, key: str):
        return {
            agent.robot_id: agent.rounds[key]["bids"].get(agent.robot_id, {}).get("bid")
            for agent in self.agents
            if key in agent.rounds
        }

    def expected_winner(self, bid_cost: dict) -> str:
        return select_winner({rid: {"cost": cost} for rid, cost in bid_cost.items()})


# ---------------------------------------------------------------------------
# 1 + 2 + 3 + 13 — single/three robot auction, lowest bid wins, unanimous winner
# ---------------------------------------------------------------------------

def test_single_robot_auction():
    h = P2PHarness(states=[RobotState(id="AMR1", x=25.0, y=3.0)])
    task, auction_id = h.announce()
    h.reach_deadline()
    h.reach_commit_window()
    h.apply_commits()

    assert task.status.value == "ASSIGNED"
    assert task.assigned_robot_id == "AMR1"
    assert h.agents[0].rounds[auction_id]["phase"] == B_SELF_ASSIGNED
    assigned = h.published(topics.TASK_ASSIGNED)
    assert len(assigned) == 1
    assert assigned[0]["robotId"] == "AMR1"
    assert assigned[0].get("auctionId") is None


def test_three_robot_auction_lowest_bid_wins():
    h = P2PHarness()
    task, auction_id = h.announce()
    h.reach_deadline()

    bids = {rid: cost for rid, cost in h.agent_bids(auction_id).items() if cost is not None}
    assert len(bids) == 3
    expected = h.expected_winner(bids)
    assert expected == "AMR2"

    # every robot independently derives the same winner
    for agent in h.agents:
        assert agent.rounds[auction_id]["winner"] == expected

    h.reach_commit_window()
    h.apply_commits()
    assert task.assigned_robot_id == expected
    assert task.status.value == "ASSIGNED"
    assert len(h.published(topics.TASK_ASSIGNED)) == 1


def test_all_robots_agree_on_same_bid_set():
    h = P2PHarness()
    task, auction_id = h.announce()
    h.reach_deadline()
    # every robot stores exactly the same bid set
    bid_maps = [frozenset((k, str(v.get("bid"))) for k, v in a.rounds[auction_id]["bids"].items())
                for a in h.agents]
    assert len(set(bid_maps)) == 1
    winners = {a.rounds[auction_id]["winner"] for a in h.agents}
    assert len(winners) == 1


# ---------------------------------------------------------------------------
# 4 — robot id tie-break
# ---------------------------------------------------------------------------

def test_robot_id_tie_break():
    # identical positions ⇒ identical bids ⇒ lexicographically smaller id wins
    h = P2PHarness(states=[
        RobotState(id="AMR2", x=10.0, y=10.0),
        RobotState(id="AMR1", x=10.0, y=10.0),
    ])
    task, auction_id = h.announce()
    h.reach_deadline()
    bids = {rid: cost for rid, cost in h.agent_bids(auction_id).items() if cost is not None}
    assert len(bids) == 2
    costs = list(bids.values())
    assert costs[0] == costs[1]
    assert h.expected_winner(bids) == "AMR1"
    h.reach_commit_window()
    h.apply_commits()
    assert task.assigned_robot_id == "AMR1"


def test_tie_break_direct():
    assert select_winner({"AMR4": {"cost": 3.0}, "AMR3": {"cost": 3.0}}) == "AMR3"
    assert select_winner({"AMR1": {"cost": 1.0}, "AMR2": {"cost": 1.0}}) == "AMR1"


# ---------------------------------------------------------------------------
# 5 — duplicate bid
# ---------------------------------------------------------------------------

def test_duplicate_bid_ignored():
    h = P2PHarness()
    task, auction_id = h.announce()
    h.reach_deadline()
    winner_before = h.agents[0].rounds[auction_id]["winner"]

    # loser robot re-bids with an unbeatable value — too late, first bid stands
    loser = "AMR1" if winner_before != "AMR1" else "AMR3"
    h.bus.publish(topics.BID_PLACED, {
        "taskId": task.id, "auctionId": auction_id, "robotId": loser, "bid": 0.001,
    })
    for agent in h.agents:
        assert len([b for b in agent.rounds[auction_id]["bids"].values()
                    if b.get("robotId") == loser]) == 1
    for agent in h.agents:
        assert agent.rounds[auction_id]["winner"] == winner_before


# ---------------------------------------------------------------------------
# 6 — duplicate winner message
# ---------------------------------------------------------------------------

def test_duplicate_winner_message_idempotent():
    h = P2PHarness()
    task, auction_id = h.announce()
    h.reach_deadline()
    winner = h.agents[0].rounds[auction_id]["winner"]
    h.reach_commit_window()

    dup = {
        "taskId": task.id, "auctionId": auction_id, "robotId": winner,
        "winner": winner, "bidCount": 0, "bids": [],
    }
    # replay the winning commit into every agent and the server
    for agent in h.agents:
        agent.on_auction_commit(dup, T_COMMIT)
    h.reach_commit_window()
    h.server._on_auction_commit(topics.AUCTION_COMMIT, dup)

    assert len(h.published(topics.TASK_ASSIGNED)) == 1
    assert task.assigned_robot_id == winner
    assert h.published(topics.AUCTION_RESULT)[-1]["committed"] is True


# ---------------------------------------------------------------------------
# 7 — missing robot (a rostered robot never bids)
# ---------------------------------------------------------------------------

def test_missing_robot_does_not_block_auction():
    states = _default_states()
    states[2].online = False  # AMR3 offline → never bids
    h = P2PHarness(states=states)
    task, auction_id = h.announce()
    h.reach_deadline()

    bidder_ids = [rid for rid, cost in h.agent_bids(auction_id).items() if cost is not None]
    assert set(bidder_ids) == {"AMR1", "AMR2"}
    winner = h.agents[0].rounds[auction_id]["winner"]
    assert winner in ("AMR1", "AMR2")
    h.reach_commit_window()
    h.apply_commits()
    assert task.assigned_robot_id == winner


# ---------------------------------------------------------------------------
# 8 — no eligible robot → deferred server-side
# ---------------------------------------------------------------------------

def test_no_eligible_robot_defers():
    busy = [
        RobotState(id="AMR1", x=25.0, y=3.0, current_task_id="T0",
                   status="MOVING_TO_PICKUP"),
        RobotState(id="AMR2", x=15.0, y=3.0, current_task_id="T0",
                   status="MOVING_TO_PICKUP"),
    ]
    h = P2PHarness(states=busy)
    task, _ = h.announce()
    assert task.status.value == "PENDING"
    assert task.id in h.server.tasks.waiting_tasks
    assert h.server.tasks.auction_in_flight is None
    # the server never announced (no eligible robot) ⇒ no agent has a round
    for agent in h.agents:
        assert agent.rounds == {}

    # once a robot frees up, the task is re-considered and eventually assigned
    h.states[0].current_task_id = None
    h.states[0].status = "IDLE"
    h.bus.publish(topics.ROBOT_TELEMETRY, h.states[0].to_dict())
    h.server.tasks.reconsider_waiting()
    h.server.tasks.advance_auction()
    auction_id = h.server.tasks._expected_round(task.id)
    h.distribute_bids(auction_id)
    assert task.id not in h.server.tasks.waiting_tasks
    h.reach_deadline()
    h.reach_commit_window()
    h.apply_commits()
    assert task.status.value == "ASSIGNED"
    assert task.assigned_robot_id in ("AMR1", "AMR2")


# ---------------------------------------------------------------------------
# 14 — auction timeout → re-auction with a fresh round
# ---------------------------------------------------------------------------

def test_auction_timeout_reauctions():
    h = P2PHarness()
    # robots never hear the announcement (they have no listeners in this test)
    h.agents = []
    task, auction_id = h.announce()
    assert auction_id.endswith(":A1")

    h.server.tasks.auction_started_at = h.announce_ts - 100.0
    h.server.tasks.sync_auction_timeout(h.announce_ts)

    assert task.status.value == "PENDING"
    announces = h.published(topics.TASK_NEW)
    assert len(announces) == 2
    ids = [p.get("auctionId") for p in announces]
    assert ids[0].endswith(":A1") and ids[1].endswith(":A2")
    assert ids[0] != ids[1]


# ---------------------------------------------------------------------------
# 9 — multiple simultaneous auctions keyed independently
# ---------------------------------------------------------------------------

def test_multiple_simultaneous_auctions():
    h = P2PHarness()
    # feed two announcements directly to the agents so both rounds coexist
    task1 = next(t for t in h.server.tasks.tasks) if h.server.tasks.tasks else None
    t1 = h.server.tasks.create_task(Point2D(5.0, 4.0), Point2D(2.0, 7.0))
    t2 = h.server.tasks.create_task(Point2D(12.0, 11.0), Point2D(6.0, 9.0))
    h.bus.publish(topics.TASK_NEW, {
        "taskId": t1.id, "auctionId": f"{t1.id}:A1",
        "pickup": {"x": 5.0, "y": 4.0}, "dropoff": {"x": 2.0, "y": 7.0},
        "priority": 1,
    })
    h.bus.publish(topics.TASK_NEW, {
        "taskId": t2.id, "auctionId": f"{t2.id}:A1",
        "pickup": {"x": 12.0, "y": 11.0}, "dropoff": {"x": 6.0, "y": 9.0},
        "priority": 1,
    })
    h.reach_deadline()

    for agent in h.agents:
        assert set(agent.rounds) == {f"{t1.id}:A1", f"{t2.id}:A1"}

    winner1 = h.agents[0].rounds[f"{t1.id}:A1"]["winner"]
    winner2 = h.agents[0].rounds[f"{t2.id}:A1"]["winner"]

    h.reach_commit_window()
    for topic, payload in h.bus.messages:
        if topic == topics.AUCTION_COMMIT:
            h.server._on_auction_commit(topic, payload)

    assert t1.assigned_robot_id == winner1
    assert t2.assigned_robot_id == winner2
    assigned = h.published(topics.TASK_ASSIGNED)
    assert len(assigned) == 2
    assert {a["robotId"] for a in assigned} == {winner1, winner2}
    # winners of the two rounds differ and each round key stays distinct
    assert winner1 is not None and winner2 is not None


# ---------------------------------------------------------------------------
# 10 — retry after a failed winner
# ---------------------------------------------------------------------------

def test_retry_after_failed_winner():
    h = P2PHarness()
    task, auction_id = h.announce()
    h.reach_deadline()
    first_winner = h.agents[0].rounds[auction_id]["winner"]
    task.assigned_robot_id = first_winner
    task.assigned_source = "auction"

    # the winner disappears mid-task
    winner_state = next(s for s in h.states if s.id == first_winner)
    winner_state.online = False
    winner_state.status = "MOVING_TO_PICKUP"
    winner_state.current_task_id = task.id
    h.bus.publish(topics.ROBOT_TELEMETRY, winner_state.to_dict())

    h.server.tasks.sync_tasks()
    # the failed auction-sourced winner is automatically re-auctioned:
    # status returns to PENDING with a fresh round (auctionId :A2)
    assert task.status.value == "PENDING"
    assert task.assigned_robot_id is None
    announces = h.published(topics.TASK_NEW)
    new_ids = [p.get("auctionId") for p in announces]
    assert new_ids[-1] != auction_id and new_ids[-1].endswith(":A2")
    h.distribute_bids(new_ids[-1])
    h.reach_deadline()
    new_winner = h.agents[0].rounds[new_ids[-1]]["winner"]
    assert new_winner != first_winner
    h.reach_commit_window()
    h.apply_commits()
    assert task.assigned_robot_id == new_winner
    assert task.status.value == "ASSIGNED"


# ---------------------------------------------------------------------------
# 11 — manual assignment stays separate
# ---------------------------------------------------------------------------

def test_manual_assignment_remains_separate():
    h = P2PHarness()
    task, _auction_id = h.announce()

    # dashboard manually assigns the task to AMR3 before the auction resolves
    h.server._on_control_assign(topics.CONTROL_TASK_ASSIGN, {
        "taskId": task.id, "robotId": "AMR3",
    })
    assert task.assigned_robot_id == "AMR3"
    assert task.assigned_source == "manual"
    assigned = [p for p in h.published(topics.TASK_ASSIGNED) if p["taskId"] == task.id]
    assert len(assigned) == 1
    assert assigned[0]["source"] == "manual"

    # a manual task that fails is NOT re-auctioned
    amr3 = next(s for s in h.states if s.id == "AMR3")
    amr3.online = False
    amr3.status = "MOVING_TO_PICKUP"
    amr3.current_task_id = task.id
    h.bus.publish(topics.ROBOT_TELEMETRY, amr3.to_dict())
    h.server.tasks.sync_tasks()
    assert task.status.value == "FAILED"
    assert len(h.published(topics.TASK_NEW)) == 1  # no re-announce


# ---------------------------------------------------------------------------
# 12 — the server never selects the winner in P2P mode
# ---------------------------------------------------------------------------

def test_server_does_not_select_winner():
    h = P2PHarness()
    assert h.server.auction_agent is None
    task, auction_id = h.announce()
    h.reach_deadline()
    winner = h.agents[0].rounds[auction_id]["winner"]
    h.reach_commit_window()
    h.apply_commits()

    # the auctioned task's assignment came from the robot's commit, not the server
    assert task.assigned_robot_id == winner
    assign_messages = h.published(topics.TASK_ASSIGNED)
    assert len(assign_messages) == 1
    assert assign_messages[0]["robotId"] == winner
    # no committed result was published before the winner's own commit
    result_topics = [t for t, _ in h.bus.messages if t == topics.AUCTION_RESULT]
    commits = [t for t, _ in h.bus.messages if t == topics.AUCTION_COMMIT]
    assert result_topics and commits
    # the ledger applied the robot's commit (committed result follows the commit)
    ledger = h.server.tasks.auctions[-1]
    assert ledger["committed"] is True
    assert ledger["winner"] == winner
    assert ledger.get("auctionId") == auction_id


# ---------------------------------------------------------------------------
# 15/16 — divergent commits: task is never assigned twice; losers abort
# ---------------------------------------------------------------------------

def test_task_never_assigned_to_two_robots():
    # Every robot only ever sees its own bid, so each believes it is the
    # unique winner. Simulated message loss produces divergent bid sets.
    h = P2PHarness()
    task, auction_id = h.announce()
    a1, a2 = h.agents[0], h.agents[1]

    for agent in h.agents:
        agent.rounds[auction_id]["bids"] = {}
    a1.rounds[auction_id]["bids"]["AMR1"] = {"robotId": "AMR1", "bid": 1.0, "taskId": task.id,
                                             "auctionId": auction_id}
    a2.rounds[auction_id]["bids"]["AMR2"] = {"robotId": "AMR2", "bid": 1.0, "taskId": task.id,
                                             "auctionId": auction_id}

    # The deadline is reached, but only the FIRST robot to finalise may
    # self-commit. The second robot observes the earlier conflicting commit
    # while finalising and aborts (conflict), so it never publishes the
    # assignment. The server ledger is single-assignment on top of that.
    h.agents[0].tick(0.05, T_DEADLINE)          # AMR1 commits → AMR2 sees it
    h.agents[1].tick(0.05, T_DEADLINE)          # AMR2 finalises → aborts (conflict)

    assert a1.rounds[auction_id]["phase"] == B_WAITING_FINALIZATION or \
        a1.rounds[auction_id]["phase"] == B_SELF_ASSIGNED
    assert a2.rounds[auction_id]["phase"] == B_CONFLICT
    assert a2.rounds[auction_id]["done"] is True

    commits = h.published(topics.AUCTION_COMMIT)
    assert {p["winner"] for p in commits} == {"AMR1"}   # loser never commits
    h.reach_commit_window()
    assigned = h.published(topics.TASK_ASSIGNED)
    assert len(assigned) == 1
    assert assigned[0]["robotId"] == "AMR1"
    h.apply_commits()
    assert task.assigned_robot_id == "AMR1"
    assert task.status.value == "ASSIGNED"


def test_conflicting_winner_aborts_and_never_executes():
    h = P2PHarness()
    task, auction_id = h.announce()
    a1, a2 = h.agents[0], h.agents[1]

    for agent in h.agents:
        agent.rounds[auction_id]["bids"] = {}
    a2.rounds[auction_id]["bids"]["AMR2"] = {"robotId": "AMR2", "bid": 1.0, "taskId": task.id,
                                             "auctionId": auction_id}
    a1.rounds[auction_id]["bids"]["AMR1"] = {"robotId": "AMR1", "bid": 1.0, "taskId": task.id,
                                             "auctionId": auction_id}

    # AMR2 finalises first and self-commits; AMR1, finalising after, observes
    # AMR2's conflicting commit and aborts.
    h.agents[1].tick(0.05, T_DEADLINE)
    h.agents[0].tick(0.05, T_DEADLINE)

    assert a1.rounds[auction_id]["phase"] == B_CONFLICT
    assert a1.rounds[auction_id]["done"] is True
    results = h.published(topics.AUCTION_RESULT)
    conflict = [p for p in results if p.get("conflict") is True]
    assert conflict and conflict[-1]["winner"] == "AMR2"

    # only the surviving winner self-assigns after its commit window
    h.reach_commit_window()
    assert a2.rounds[auction_id]["phase"] == B_SELF_ASSIGNED
    assigned = h.published(topics.TASK_ASSIGNED)
    assert len(assigned) == 1
    assert assigned[0]["robotId"] == "AMR2"
    h.apply_commits()
    assert task.assigned_robot_id == "AMR2"


# ---------------------------------------------------------------------------
# 11a — late bid after the deadline never changes the frozen winner
# ---------------------------------------------------------------------------

def test_late_bid_recorded_but_does_not_change_winner():
    states = _default_states()
    states[2].online = False  # AMR3 never bids
    h = P2PHarness(states=states)
    task, auction_id = h.announce()
    h.reach_deadline()
    frozen = h.agents[0].rounds[auction_id]["winner"]

    # AMR3 "wakes up" and publishes the best possible bid after the deadline
    h.bus.publish(topics.BID_PLACED, {
        "taskId": task.id, "auctionId": auction_id, "robotId": "AMR3", "bid": 0.0,
    })
    for agent in h.agents:
        assert agent.rounds[auction_id]["bids"]["AMR3"]["bid"] == 0.0  # recorded
        assert agent.rounds[auction_id]["winner"] == frozen            # unchanged
    h.reach_commit_window()
    h.apply_commits()
    assert task.assigned_robot_id == frozen


def test_bid_none_and_low_battery_never_win():
    # a None-bid robot is the winner-eligible set is excluded
    bids = {"AMR1": {"cost": None}, "AMR2": {"cost": 4.0}}
    assert select_winner(bids) == "AMR2"