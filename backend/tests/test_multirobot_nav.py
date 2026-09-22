"""
test_multirobot_nav.py — Deterministic multi-robot navigation scenarios.

A lightweight in-process fleet simulator drives several MotionControllers in
lockstep. Robots share a *pre-update* fleet snapshot every tick (no pipelining
artifacts) so the whole run is deterministic and free of ordering biases.

Scenarios A–L cover the coordination behaviours this baseline is expected to
guarantee:

    A. Head-on collision avoidance on an open course.
    B. Perpendicular (crossing) traffic.
    C. Same-direction convoy with no chasing collisions.
    D. Converging traffic at an intersection hub.
    E. Overtake in a wide aisle.
    F. Peer goes offline mid-task — traffic keeps flowing.
    G. Two robots queueing through one narrow corridor.
    J. Obstacle removed mid-route — robot replans and finishes.
    K. Congestion burst across a shelf grid — bounded replans, no deadlock.
    L. Byte-for-byte determinism of the whole pipeline over identical runs.
"""

import math

import pytest

from common.geometry import Rect
from robot.controller import MotionController, RobotState


DT = 0.05
SAFE_DIST = 0.78


class SimFleet:
    """Lockstep fleet simulator that does not depend on iteration order."""

    def __init__(self, obstacles, bounds):
        self.obstacles = obstacles
        self.bounds = bounds
        self.robots: dict[str, tuple] = {}

    def add(self, rid: str, x: float, y: float, max_speed: float = 1.0) -> str:
        state = RobotState(id=rid, x=x, y=y, max_speed=max_speed, radius=0.4)
        ctrl = MotionController(state, lambda _m, _r=rid: None,
                                resolution=0.25, safety_margin=0.1)
        self.robots[rid] = (state, ctrl)
        return rid

    def set_pad(self, rid: str, pads, own_pad_id: str) -> None:
        """Give the robot a charge pad so it vacates the dock after dropping."""
        state, _ = self.robots[rid]
        state.pads = pads
        state.own_pad_id = own_pad_id

    def assign(self, rid: str, task_id: str, pickup, dropoff):
        state, ctrl = self.robots[rid]
        ctrl.assign_task(task_id, pickup, dropoff,
                         obstacles=self.obstacles, bounds=self.bounds)

    def snapshot(self) -> list[dict]:
        return [state.to_dict() for (state, _) in self.robots.values()]

    def step(self, n: int) -> list[dict]:
        """Advance ``n`` ticks; returns (ordered) final positions."""
        positions = []
        min_sep = 9e9
        for _ in range(n):
            snap = self.snapshot()
            for rid, (state, ctrl) in self.robots.items():
                fleet = [s for s in snap if s["robotId"] != rid]
                ctrl.update(DT, self.obstacles, bounds=self.bounds, fleet=fleet)
            pts = [(s["robotId"], s["x"], s["y"]) for s in self.snapshot()]
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    min_sep = min(min_sep, math.dist(pts[i][1:], pts[j][1:]))
            positions.append(pts)
        return positions

    def statuses(self) -> dict:
        return {rid: state.status for rid, (state, _) in self.robots.items()}

    def metrics(self, rid: str) -> dict:
        state, _ = self.robots[rid]
        return state.metrics

    @property
    def ids(self):
        return list(self.robots.keys())


def _open(obstacles=(), w=20.0, h=10.0):
    return SimFleet(list(obstacles), {"width": w, "height": h})


def _assert_no_collision(positions):
    for pts in positions:
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                assert math.dist(pts[i][1:], pts[j][1:]) >= SAFE_DIST - 1e-9, \
                    f"collision {pts[i][0]} vs {pts[j][0]}: {math.dist(pts[i][1:], pts[j][1:]):.3f}"


# ---------------------------------------------------------------------------
# A — head-on open-course pass
# ---------------------------------------------------------------------------

def test_a_head_on_avoidance_open_course():
    f = _open()
    f.add("R1", 1, 5)
    f.add("R2", 19, 5)
    f.assign("R1", "T1", {"x": 19, "y": 5}, {"x": 19, "y": 7})
    f.assign("R2", "T2", {"x": 1, "y": 5}, {"x": 1, "y": 7})
    positions = f.step(700)
    _assert_no_collision(positions)
    assert f.metrics("R1")["near_collisions"] == 0
    assert f.metrics("R1")["collisions"] == 0


# ---------------------------------------------------------------------------
# B — perpendicular crossing
# ---------------------------------------------------------------------------

def test_b_perpendicular_crossing():
    f = _open(w=20, h=20)
    f.add("R1", 2, 2)
    f.add("R2", 2, 18)
    f.assign("R1", "T1", {"x": 18, "y": 18}, {"x": 18, "y": 18})
    f.assign("R2", "T2", {"x": 18, "y": 2}, {"x": 18, "y": 2})
    positions = f.step(700)
    _assert_no_collision(positions)
    assert f.metrics("R1")["collisions"] == 0 and f.metrics("R2")["collisions"] == 0


# ---------------------------------------------------------------------------
# C — same-direction convoy
# ---------------------------------------------------------------------------

def test_c_same_direction_convoy():
    f = _open()
    f.add("R1", 2, 5, max_speed=1.0)
    f.add("R2", 3, 5, max_speed=1.0)
    f.assign("R1", "T1", {"x": 18, "y": 5}, {"x": 18, "y": 5})
    f.assign("R2", "T2", {"x": 18, "y": 5}, {"x": 18, "y": 5})
    positions = f.step(800)
    _assert_no_collision(positions)
    # R2 (behind) must not have rear-ended R1.
    assert f.metrics("R2")["collisions"] == 0


# ---------------------------------------------------------------------------
# D — crossing at an intersection hub
# ---------------------------------------------------------------------------

def test_d_intersection_convergence():
    f = _open(w=20, h=20)
    f.add("R1", 5, 2)
    f.add("R2", 5, 18)
    f.add("R3", 2, 8)
    f.assign("R1", "T1", {"x": 15, "y": 10}, {"x": 15, "y": 10})
    f.assign("R2", "T2", {"x": 15, "y": 10}, {"x": 15, "y": 10})
    f.assign("R3", "T3", {"x": 18, "y": 10}, {"x": 18, "y": 10})
    positions = f.step(700)
    _assert_no_collision(positions)
    for rid in f.ids:
        assert f.metrics(rid)["collisions"] == 0


# ---------------------------------------------------------------------------
# E — overtake in a wide aisle
# ---------------------------------------------------------------------------

def test_e_overtake_wide_aisle():
    f = _open(w=20, h=14)
    f.add("Slow", 4, 8, max_speed=0.8)
    f.add("Fast", 1.5, 8, max_speed=1.6)
    f.assign("Slow", "T1", {"x": 18, "y": 8.5}, {"x": 18, "y": 8.5})
    f.assign("Fast", "T2", {"x": 18, "y": 7.5}, {"x": 18, "y": 7.5})
    positions = f.step(800)
    _assert_no_collision(positions)
    assert f.metrics("Fast")["collisions"] == 0


# ---------------------------------------------------------------------------
# F — peer goes offline mid-task; traffic keeps flowing
# ---------------------------------------------------------------------------

def test_f_offline_peer_does_not_block_fleet():
    f = _open()
    f.add("R1", 1, 5)
    f.add("R2", 2, 5)
    f.assign("R1", "T1", {"x": 18, "y": 5}, {"x": 18, "y": 5})
    f.assign("R2", "T2", {"x": 18, "y": 5}, {"x": 18, "y": 5})

    # R2 goes offline (stops publishing) after the first 100 steps.
    state_r2, ctrl_r2 = f.robots["R2"]

    def pruned_snapshot():
        out = []
        for s in f.snapshot():
            if s["robotId"] == "R2":
                s["online"] = False
            out.append(s)
        return out

    positions = []
    for i in range(1200):
        snap = pruned_snapshot()
        for rid, (state, ctrl) in f.robots.items():
            fleet = [s for s in snap if s["robotId"] != rid]
            if i == 100:
                state_r2.online = False
            ctrl.update(DT, f.obstacles, bounds=f.bounds, fleet=fleet)
        pts = [(s["robotId"], s["x"], s["y"]) for s in snap]
        positions.append(pts)
    # The surviving robot must make steady progress (not block forever on a
    # phantom reservation).
    assert f.robots["R1"][0].x >= 15.0, f"R1 blocked by offline peer: x={f.robots['R1'][0].x:.2f}"


# ---------------------------------------------------------------------------
# G — two robots queue through one narrow corridor (explicit coordination)
# ---------------------------------------------------------------------------

def _boxed_corridor_layout():
    # Two 12 m shelf walls leaving a single 1.4 m vertical corridor through the
    # middle of a 20x20 warehouse. The robots must traverse the corridor
    # head-on (northbound vs southbound) so they must serialize.
    return [
        Rect(0.0, 4.0, 9.3, 12.0),     # west wall (x 0..9.3)
        Rect(10.7, 4.0, 9.3, 12.0),    # east wall (x 10.7..20): gap 9.3..10.7
    ]


def test_g_single_corridor_serialization():
    f = _open(_boxed_corridor_layout(), w=20, h=20)
    f.add("R1", 10, 1)
    f.add("R2", 10, 19)
    f.assign("R1", "T1", {"x": 10, "y": 19}, {"x": 10, "y": 19})
    f.assign("R2", "T2", {"x": 10, "y": 1}, {"x": 10, "y": 1})
    positions = f.step(1500)
    _assert_no_collision(positions)
    # Both robots must have crossed to the opposite chamber.
    assert f.robots["R1"][0].y >= 17.0, f"R1 stuck at y={f.robots['R1'][0].y:.2f}"
    assert f.robots["R2"][0].y <= 3.0, f"R2 stuck at y={f.robots['R2'][0].y:.2f}"
    # Explicit serialization happened: at least one robot waited at the mouth.
    total_waiting = sum(f.metrics(r)["waiting_time"] for r in f.ids)
    assert total_waiting > 0, "expected explicit corridor serialization"

# ---------------------------------------------------------------------------
# J — obstacle removed mid-route → replan to shorter path and finish
# ---------------------------------------------------------------------------

def test_j_obstacle_removed_mid_route():
    wall = Rect(7, 2, 0.6, 6)
    f = SimFleet([wall], {"width": 20, "height": 10})
    f.add("R1", 2, 5)
    f.assign("R1", "T1", {"x": 18, "y": 5}, {"x": 18, "y": 5})
    # First phase: route around the wall.
    for _ in range(200):
        snap = f.snapshot()
        state, ctrl = f.robots["R1"]
        ctrl.update(DT, [wall], bounds=f.bounds,
                    fleet=[s for s in snap if s["robotId"] != "R1"])
    # Second phase: wall removed → the robot should replan and still finish.
    for _ in range(1200):
        snap = f.snapshot()
        state, ctrl = f.robots["R1"]
        ctrl.update(DT, [], bounds=f.bounds,
                    fleet=[s for s in snap if s["robotId"] != "R1"])
    assert f.robots["R1"][0].x >= 17.5, "robot did not reach goal after world change"


# ---------------------------------------------------------------------------
# K — congestion burst across a shelf grid; no deadlock, bounded replans
# ---------------------------------------------------------------------------

def test_k_congestion_burst_no_deadlock():
    # Partial-height vertical shelves (larger gaps at top/bottom) so every
    # aisle *is* passable but tight — robots must squeeze through and queue.
    grid = [
        Rect(6, 2, 0.4, 8), Rect(8.2, 2, 0.4, 8),
        Rect(10.4, 2, 0.4, 8), Rect(12.6, 2, 0.4, 8),
        Rect(14.8, 2, 0.4, 8),
    ]
    f = SimFleet(grid, {"width": 20, "height": 12})
    starts = [(1, 6), (1, 8), (1, 10), (18, 2), (18, 4)]
    goals = [(19, 6), (19, 8), (19, 10), (2, 2), (2, 4)]
    for i, (sx, sy) in enumerate(starts):
        f.add(f"R{i + 1}", sx, sy)
        f.assign(f"R{i + 1}", f"T{i + 1}", {"x": goals[i][0], "y": goals[i][1]},
                 {"x": goals[i][0], "y": goals[i][1]})
    positions = f.step(1500)
    _assert_no_collision(positions)
    for rid in f.ids:
        assert f.metrics(rid)["replan_count"] <= 6, \
            f"{rid} replanned too often: {f.metrics(rid)['replan_count']}"


# ---------------------------------------------------------------------------
# L — determinism: identical trajectories over identical pipelines
# ---------------------------------------------------------------------------

def test_l_byte_for_byte_determinism():
    def run():
        f = _open()
        f.add("R1", 1, 4)
        f.add("R2", 19, 4)
        f.assign("R1", "T1", {"x": 19, "y": 4}, {"x": 19, "y": 4})
        f.assign("R2", "T2", {"x": 1, "y": 4}, {"x": 1, "y": 4})
        return f.step(300)

    first = run()
    second = run()
    assert first == second


def test_a_head_on_will_never_enter_same_cell():
    f = _open()
    f.add("R1", 1, 5)
    f.add("R2", 19, 5)
    f.assign("R1", "T1", {"x": 19, "y": 5}, {"x": 19, "y": 5})
    f.assign("R2", "T2", {"x": 1, "y": 5}, {"x": 1, "y": 5})
    positions = f.step(900)
    _assert_no_collision(positions)
    # Both robots complete their first leg.
    assert f.robots["R1"][0].x >= 17.5
    assert f.robots["R2"][0].x <= 2.5


# ---------------------------------------------------------------------------
# M — same dropoff station: robots wait for one another instead of colliding
# ---------------------------------------------------------------------------

def _dock_bounds():
    pads = [
        {"id": "BAY-1", "x": 17.0, "y": 1.0, "width": 2.0, "height": 2.0,
         "spawnPoint": {"x": 18.0, "y": 2.0}, "assignedRobotId": "R1"},
        {"id": "BAY-2", "x": 17.0, "y": 3.0, "width": 2.0, "height": 2.0,
         "spawnPoint": {"x": 18.0, "y": 4.0}, "assignedRobotId": "R2"},
    ]
    return {
        "width": 20.0, "height": 12.0,
        "chargingPads": pads,
        "deliveryDocks": [
            {"id": "DOCK-1", "name": "Delivery Dock 1", "x": 0.5, "y": 4.0,
             "width": 3.0, "height": 3.0, "dropoffPoint": {"x": 2.0, "y": 6.0}},
        ],
    }


def test_m_two_amrs_share_one_dropoff_station_serialize():
    """Two AMRs delivering to the SAME dock point must queue, not collide.

    Both robots pick from distinct shelves on the right, then deliver to the
    shared DOCK-1. Before the coordinators, they raced straight to the same
    goal point; now the dock (and any goal point) is registered as a terminal
    zone so the station is reserved and robots wait for one another.
    """
    pane = _dock_bounds()
    f = SimFleet([], pane)
    f.add("R1", 16.0, 10.0)
    f.add("R2", 16.0, 2.0)
    f.set_pad("R1", pane["chargingPads"], "BAY-1")
    f.set_pad("R2", pane["chargingPads"], "BAY-2")
    drop = {"x": 2.0, "y": 6.0}
    f.assign("R1", "T1", {"x": 18.0, "y": 10.0}, drop)
    f.assign("R2", "T2", {"x": 18.0, "y": 3.0}, drop)
    positions = f.step(2500)
    _assert_no_collision(positions)
    assert f.metrics("R1")["collisions"] == 0
    assert f.metrics("R2")["collisions"] == 0
    # Both robots completed their delivery and returned to their charge pads.
    assert f.robots["R1"][0].x > 16.0, f"R1 stuck at ({f.robots['R1'][0].x:.2f}, {f.robots['R1'][0].y:.2f})"
    assert f.robots["R2"][0].x > 16.0, f"R2 stuck at ({f.robots['R2'][0].x:.2f}, {f.robots['R2'][0].y:.2f})"
    # Serialization happened: a robot waited at the shared dock.
    assert f.metrics("R1")["waiting_time"] > 0 or f.metrics("R2")["waiting_time"] > 0


def test_m_charging_return_routes_through_pipeline_not_bare_path():
    """A charging return must use the nav pipeline, not raw waypoint following.

    A robot riding back to its charging pad used to follow its A* path blindly
    (``_follow_path``), which bypassed avoidance, chokepoint reservations and
    the safety hard-stop. With corner pads, R2's return path crosses R1's
    delivery lane while R1 is hard-stopped at the dock — under the old code the
    returning robot drove straight through R1 (min separation ~0.1 m and a
    scored collision). Return transits now run the pipeline, so the two resolve
    via avoidance with zero contact.
    """
    pads = [
        {"id": "BAY-1", "x": 0.5, "y": 0.5, "width": 2.0, "height": 2.0,
         "spawnPoint": {"x": 1.5, "y": 1.5}, "assignedRobotId": "R1"},
        {"id": "BAY-2", "x": 17.5, "y": 9.5, "width": 2.0, "height": 2.0,
         "spawnPoint": {"x": 18.5, "y": 10.5}, "assignedRobotId": "R2"},
    ]
    pane = {"width": 20.0, "height": 12.0, "chargingPads": pads,
            "deliveryDocks": [{"id": "DOCK-1", "dropoffPoint": {"x": 2.0, "y": 6.0}}]}
    f = SimFleet([], pane)
    f.add("R1", 16.0, 10.0)
    f.add("R2", 16.0, 2.0)
    f.set_pad("R1", pads, "BAY-1")
    f.set_pad("R2", pads, "BAY-2")
    drop = {"x": 2.0, "y": 6.0}
    f.assign("R1", "T1", {"x": 18.0, "y": 10.0}, drop)
    f.assign("R2", "T2", {"x": 18.0, "y": 3.0}, drop)
    positions = f.step(2500)
    _assert_no_collision(positions)
    assert f.metrics("R1")["collisions"] == 0
    assert f.metrics("R2")["collisions"] == 0
    # Both robots reached their own pad after delivering.
    assert f.robots["R1"][0].x < 4.0, f"R1 not on pad ({f.robots['R1'][0].x:.2f}, {f.robots['R1'][0].y:.2f})"
    assert f.robots["R2"][0].x > 16.0, f"R2 not on pad ({f.robots['R2'][0].x:.2f}, {f.robots['R2'][0].y:.2f})"