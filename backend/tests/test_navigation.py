"""
test_navigation.py — Unit tests for the algorithmic navigation components.

Covers the replaceable stages individually (planner validation, velocity-obstacle
avoidance, trajectory constraints, safety hard-stop, deadlock arbitration,
chokepoint reservations) plus a deterministic regression for the whole pipeline.
"""

import math

import pytest

from common.geometry import Rect
from common.models import RobotStatus
from robot.controller import MotionController, RobotState
from robot.navigation.regionmap import RegionMap, CORRIDOR_CLEARANCE_M
from robot.navigation import reservation
from robot.navigation.avoidance import AlgorithmicCollisionAvoidance
from robot.navigation.planner import AlgorithmicPathPlanner
from robot.navigation.safety import AlgorithmicSafetyController
from robot.navigation.trajectory import AlgorithmicTrajectoryController
from robot.navigation.deadlock import AlgorithmicDeadlockResolver
from robot.navigation.chokepoint import AlgorithmicChokepointCoordinator
from robot.navigation.pipeline import AlgorithmicNavPipeline


def _ctx(state, obstacles=(), bounds=None, fleet=()):
    from robot.navigation.interfaces import NavContext
    return NavContext(state, list(fleet), list(obstacles), bounds or {"width": 30, "height": 20})


def _peer(rid, x, y, heading=0.0, speed=0.0, online=True, nav=None):
    return {"robotId": rid, "x": x, "y": y, "heading": heading, "speed": speed,
            "online": online, "radius": 0.4, "nav": nav or {}}


# ---------------------------------------------------------------------------
# RegionMap
# ---------------------------------------------------------------------------

def test_regionmap_detects_corridor_between_shelves():
    # Two shelves 0.8 m apart -> single-lane corridor between them.
    a = Rect(x=2.0, y=2.0, width=0.4, height=2.0)
    b = Rect(x=3.2, y=2.0, width=0.4, height=2.0)
    rmap = RegionMap(8.0, 8.0, [a, b], resolution=0.5)
    kinds = {r.kind for r in rmap.regions}
    assert "corridor" in kinds


def test_regionmap_wide_open_floor_has_no_corridors():
    rmap = RegionMap(20.0, 20.0, [], resolution=0.5)
    assert all(r.kind != "corridor" for r in rmap.regions)


def test_region_contains_and_intersects():
    rmap = RegionMap(12.0, 12.0, [Rect(4, 2, 0.4, 6), Rect(5.6, 2, 0.4, 6)], resolution=0.5)
    corridors = [r for r in rmap.regions if r.kind == "corridor"]
    assert corridors
    c = corridors[0]
    assert c.contains(4.5, 5.0, pad=0.2)
    assert c.intersects(4.0, 5.0, 7.0, 5.0)


# ---------------------------------------------------------------------------
# Reservation arbitration (pure / deterministic)
# ---------------------------------------------------------------------------

def _mkregion(xmin=0.0, ymin=0.0, xmax=2.0, ymax=2.0):
    from robot.navigation.regionmap import Region
    r = Region(rid="X", kind="corridor", cells=set())
    r.bbox = (xmin, ymin, xmax, ymax)
    return r


def test_select_holder_prefers_inside_robot():
    region = _mkregion()
    robot = _peer("AMR1", 1.0, 1.0)  # inside
    peer = _peer("AMR2", 9.0, 9.0)   # outside, claims region
    peer["nav"] = {"reservation": {"regionId": "X", "claimedAt": 0.1,
                                   "expectedExit": 100.0, "priority": 5,
                                   "distanceToEntry": 0.5}}
    winner = reservation.select_holder(region, [peer, robot], now=10.0)
    assert winner == "AMR1"


def test_select_holder_prefers_earlier_claim():
    region = _mkregion()
    p1 = _peer("AMR1", 9.0, 9.0)
    p1["nav"] = {"reservation": {"regionId": "X", "claimedAt": 0.1,
                                 "expectedExit": 100.0, "priority": 1,
                                 "distanceToEntry": 1.0}}
    p2 = _peer("AMR2", 9.0, 9.0)
    p2["nav"] = {"reservation": {"regionId": "X", "claimedAt": 0.2,
                                 "expectedExit": 100.0, "priority": 1,
                                 "distanceToEntry": 1.0}}
    assert reservation.select_holder(region, [p1, p2], now=10.0) == "AMR1"


def test_offline_claim_is_ignored():
    region = _mkregion()
    p1 = _peer("AMR1", 9.0, 9.0, online=False)
    p1["nav"] = {"reservation": {"regionId": "X", "claimedAt": 0.1,
                                 "expectedExit": 100.0, "priority": 5,
                                 "distanceToEntry": 1.0}}
    assert reservation.select_holder(region, [p1], now=10.0) is None
    assert reservation.active_claim(p1, "X", now=10.0) is None


def test_claim_expires_after_exit_plus_grace():
    p = _peer("AMR1", 9.0, 9.0)
    p["nav"] = {"reservation": {"regionId": "X", "claimedAt": 0.0,
                                "expectedExit": 1.0, "priority": 1,
                                "distanceToEntry": 0.0}}
    assert reservation.active_claim(p, "X", now=2.0) is not None
    assert reservation.active_claim(p, "X", now=2.6) is None


# ---------------------------------------------------------------------------
# Path planner validation
# ---------------------------------------------------------------------------

def test_planner_flags_blocked_path():
    state = RobotState(id="AMR1", x=0.0, y=0.0)
    planner = AlgorithmicPathPlanner(state, lambda _: None, resolution=0.25, safety_margin=0.1)
    wall = Rect(x=0.9, y=0.9, width=1.0, height=1.0)
    path = planner.plan((0.0, 0.0), (4.0, 4.0), [wall], {"width": 10, "height": 10})
    reason = planner.validate(path, [wall], [], {"width": 10, "height": 10})
    # No obstacles lie on the planned route (it went around them).
    assert reason is None
    # A path that now bisects the wall must be flagged.
    bad = [(0.0, 0.0), (4.0, 4.0)]
    assert planner.validate(bad, [wall], [], {"width": 10, "height": 10}) is not None


# ---------------------------------------------------------------------------
# Collision avoidance
# ---------------------------------------------------------------------------

def test_avoidance_normal_when_no_peer():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    av = AlgorithmicCollisionAvoidance(state, lambda _: None, safety_margin=0.1)
    ctx = _ctx(state)
    out = av.compute(ctx, (10.0, 0.0), 2.0, 0.0, now=0.0)
    assert out["state"] == "normal"
    assert not out["intervention"]
    assert abs(out["vx"] - 2.0) < 1e-6


def test_avoidance_intervenes_when_peer_head_on():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    peer = _peer("AMR2", 3.0, 0.0, heading=math.pi, speed=2.0)
    av = AlgorithmicCollisionAvoidance(state, lambda _: None, safety_margin=0.1)
    ctx = _ctx(state, fleet=[peer])
    out = av.compute(ctx, (10.0, 0.0), 2.0, 0.0, now=0.0)
    assert out["state"] == "avoiding"
    assert abs(out["vy"]) > 1e-3  # displaced laterally, not head-on


def test_avoidance_returns_preferred_after_peer_passes():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    peer = _peer("AMR2", 10.0, 0.0, heading=math.pi, speed=0.1)
    av = AlgorithmicCollisionAvoidance(state, lambda _: None, safety_margin=0.1)
    ctx = _ctx(state, fleet=[peer])
    out = av.compute(ctx, (10.0, 0.0), 2.0, 0.0, now=0.0)
    # Far away and barely moving — no reason to deviate.
    assert out["state"] == "normal"
    assert abs(out["vx"] - 2.0) < 1e-6


# ---------------------------------------------------------------------------
# Trajectory controller kinematic limits
# ---------------------------------------------------------------------------

def test_trajectory_limits_acceleration():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    tc = AlgorithmicTrajectoryController(state, lambda _: None)
    vx, vy, speed, heading = tc.step(_ctx(state), 2.0, 0.0, dt=0.05)
    # Max 2.0 m/s^2 for 0.05 s -> at most +0.1 m/s from rest.
    assert speed <= 0.1 + 1e-9


def test_trajectory_reaches_desired_speed():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    tc = AlgorithmicTrajectoryController(state, lambda _: None)
    for _ in range(200):
        vx, vy, speed, heading = tc.step(_ctx(state), 2.0, 0.0, dt=0.05)
        state.vx = vx
        state.vy = vy
    assert speed == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------------------
# Safety hard stop
# ---------------------------------------------------------------------------

def test_safety_stops_on_imminent_contact():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    peer = _peer("AMR2", 0.5, 0.0, heading=math.pi, speed=2.0)
    sc = AlgorithmicSafetyController(state, lambda _: None)
    out = sc.compute(_ctx(state, fleet=[peer]), 2.0, 0.0, now=0.0)
    assert out["stop"] is True
    assert out["state"] == "hard_stop"


def test_safety_allows_motion_when_clear():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, radius=0.4)
    sc = AlgorithmicSafetyController(state, lambda _: None)
    out = sc.compute(_ctx(state, fleet=[]), 2.0, 0.0, now=0.0)
    assert out["stop"] is False


# ---------------------------------------------------------------------------
# Deadlock resolution
# ---------------------------------------------------------------------------

def test_deadlock_escalates_after_wait():
    state = RobotState(id="AMR1", x=0.0, y=0.0, max_speed=2.0, heading=1.0)
    dr = AlgorithmicDeadlockResolver(state, lambda _: None)
    ctx = _ctx(state)
    state.current_path = [(0.0, 0.0), (5.0, 5.0)]
    state.path_index = 0
    out = None
    for i in range(200):
        out = dr.update(ctx, "chokepoint_wait", "wait_reserved", dt=0.05,
                        now=i * 0.05)
        if out["deadlock"]:
            break
    assert out is not None and out["deadlock"]


# ---------------------------------------------------------------------------
# Chokepoint coordination
# ---------------------------------------------------------------------------

def test_chokepoint_waits_for_occupied_region():
    state = RobotState(id="AMR1", x=0.5, y=0.5, max_speed=2.0, radius=0.4)
    shelves = [Rect(x=3.0, y=0.0, width=0.4, height=3.0),
               Rect(x=4.6, y=0.0, width=0.4, height=3.0)]
    cc = AlgorithmicChokepointCoordinator(state, lambda _: None)
    path = [(0.5, 0.5), (4.2, 1.5)]
    ctx = _ctx(state, obstacles=shelves)
    # Peer physically inside the corridor ahead.
    peer = _peer("AMR2", 4.2, 1.5, online=True)
    ctx.fleet = [peer]
    out = cc.tick(ctx, path, 0, now=0.0)
    assert out["state"] == "chokepoint_wait"
    assert out["reservation"] is None


def test_chokepoint_claims_when_clear():
    state = RobotState(id="AMR1", x=0.5, y=0.5, max_speed=2.0, radius=0.4)
    shelves = [Rect(x=3.0, y=0.0, width=0.4, height=3.0),
               Rect(x=4.6, y=0.0, width=0.4, height=3.0)]
    cc = AlgorithmicChokepointCoordinator(state, lambda _: None)
    path = [(0.5, 0.5), (4.2, 1.5)]
    ctx = _ctx(state, obstacles=shelves)
    ctx.fleet = []
    out = cc.tick(ctx, path, 0, now=0.0)
    assert out["state"] == "chokepoint_entering"
    assert out["reservation"] is not None
    assert out["reservation"]["robotId"] == "AMR1"


def test_peer_claim_blocks_entry():
    shelves = [Rect(x=3.0, y=0.0, width=0.4, height=3.0),
               Rect(x=4.6, y=0.0, width=0.4, height=3.0)]
    path = [(0.5, 0.5), (4.2, 1.5)]
    state = RobotState(id="AMR1", x=0.5, y=0.5, max_speed=2.0, radius=0.4)
    cc = AlgorithmicChokepointCoordinator(state, lambda _: None)
    region = cc._ensure_map(_ctx(state, obstacles=shelves))
    region = cc.map.first_region_on_path(path, 0)
    assert region is not None
    ctx = _ctx(state, obstacles=shelves)
    peer = _peer("AMR2", 1.0, 0.5)
    peer["nav"] = {"reservation": {"regionId": region.rid, "claimedAt": 0.0,
                                   "expectedExit": 100.0, "priority": 1,
                                   "distanceToEntry": 1.0}}
    ctx.fleet = [peer]
    out = cc.tick(ctx, path, 0, now=5.0)
    assert out["state"] == "chokepoint_wait"


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

def _run_task(state, controller, obstacles, bounds, dt=0.05, max_ticks=2000,
              fleet=()):
    for _ in range(max_ticks):
        controller.update(dt, obstacles, bounds=bounds, fleet=fleet)
        if state.status == RobotStatus.PICKING:
            return True
    return False


def test_pipeline_completes_single_robot_task():
    state = RobotState(id="AMR1", x=1.0, y=5.0, max_speed=2.0, radius=0.4)
    controller = MotionController(state, lambda m: None, resolution=0.25, safety_margin=0.1)
    obstacle = Rect(x=4, y=2, width=2, height=6)
    controller.assign_task(
        "T1", {"x": 9, "y": 5}, {"x": 9, "y": 8},
        obstacles=[obstacle], bounds={"width": 10, "height": 10},
    )
    assert _run_task(state, controller, [obstacle], {"width": 10, "height": 10},
                     max_ticks=1200)
    assert math.dist((state.x, state.y), (9, 5)) < 0.3
    assert state.metrics["distance"] > 0


def test_pipeline_replans_when_destination_blocked_after_start():
    state = RobotState(id="AMR1", x=1.0, y=1.0, max_speed=2.0, radius=0.4)
    controller = MotionController(state, lambda m: None, resolution=0.25, safety_margin=0.1)
    # Agenda: assign a far task, then place a wall in the middle of the route.
    controller.assign_task(
        "T2", {"x": 18.0, "y": 1.0}, {"x": 18.0, "y": 1.0},
        obstacles=[], bounds={"width": 20, "height": 10},
    )
    wall = Rect(x=9, y=-1, width=0.5, height=3)  # blocks y=1, passable above
    done = _run_task(state, controller, [wall], {"width": 20, "height": 10},
                     max_ticks=2000, fleet=())
    assert done
    # The wall forces at least one route recalculation around it.
    assert state.metrics["replan_count"] > 0 or state.nav_state == "replanning"


def test_two_robots_avoid_each_other_without_collision():
    b = {"width": 20, "height": 10}
    s1 = RobotState(id="AMR1", x=1.0, y=5.0, max_speed=2.0, radius=0.4)
    s2 = RobotState(id="AMR2", x=19.0, y=5.0, max_speed=2.0, radius=0.4)
    c1 = MotionController(s1, lambda _: None)
    c2 = MotionController(s2, lambda _: None)
    c1.assign_task("A", {"x": 18.0, "y": 5.0}, {"x": 18.0, "y": 8.0},
                   obstacles=[], bounds=b)
    c2.assign_task("B", {"x": 2.0, "y": 5.0}, {"x": 2.0, "y": 8.0},
                   obstacles=[], bounds=b)
    min_dist = 9e9
    finished = False
    for _ in range(900):
        c1.update(0.05, [], bounds=b, fleet=[s2.to_dict()])
        c2.update(0.05, [], bounds=b, fleet=[s1.to_dict()])
        min_dist = min(min_dist, math.dist((s1.x, s1.y), (s2.x, s2.y)))
        if s1.status in (RobotStatus.PICKING, RobotStatus.MOVING_TO_PICKUP) and \
           s2.status in (RobotStatus.PICKING, RobotStatus.MOVING_TO_DROPOFF):
            if s1.status == RobotStatus.PICKING and s2.status == RobotStatus.PICKING:
                finished = True
                break
    assert min_dist >= 0.8, f"robots came within {min_dist:.3f} m"
    assert finished or s1.status == RobotStatus.PICKING or s2.status == RobotStatus.PICKING