import math
import pytest

from common.geometry import Rect
from common.models import RobotStatus
from robot.controller import RobotState, MotionController
from robot.planning.astar import AStarPlanner, PathNotFoundError, path_distance


def test_astar_finds_route_around_wall():
    planner = AStarPlanner(10, 10, resolution=0.25, robot_radius=0.2, safety_margin=0.05)
    wall = Rect(4, 2, 2, 6)
    path = planner.plan((1, 5), (9, 5), [wall])
    assert path[0] == (1, 5)
    assert path[-1] == (9, 5)
    assert len(path) > 2
    assert planner.segment_is_free(path[0], path[1], [wall])
    assert all(planner.segment_is_free(a, b, [wall]) for a, b in zip(path, path[1:]))
    assert path_distance(path) > 8


def test_diagonal_corner_cutting_is_forbidden():
    planner = AStarPlanner(4, 4, resolution=1.0, robot_radius=0.0, safety_margin=0.0)
    obstacles = [
        {"id": "a", "x": 1, "y": 0, "width": 1, "height": 1},
        {"id": "b", "x": 0, "y": 1, "width": 1, "height": 1},
    ]
    with pytest.raises(PathNotFoundError):
        planner.plan((0.5, 0.5), (2.5, 2.5), obstacles, smooth=False)


def test_clear_path_is_direct_and_short():
    planner = AStarPlanner(20, 20, resolution=0.25)
    path = planner.plan((1, 1), (8, 5), [])
    assert path == [(1, 1), (8, 5)]
    assert path_distance(path) == pytest.approx(math.dist((1, 1), (8, 5)))


def test_inflation_respects_robot_radius_and_margin():
    planner = AStarPlanner(10, 10, resolution=0.25, robot_radius=0.4, safety_margin=0.1)
    obstacle = {"id": "shelf", "x": 4, "y": 4, "width": 2, "height": 2}
    assert not planner.segment_is_free((3, 5), (7, 5), [obstacle])


def test_controller_follows_astar_path_without_entering_shelf():
    state = RobotState(id="AMR1", x=1, y=5, max_speed=2.0, radius=0.4)
    logs = []
    controller = MotionController(state, logs.append, resolution=0.25, safety_margin=0.1)
    obstacle = Rect(4, 2, 2, 6)
    controller.assign_task(
        "T1", {"x": 9, "y": 5}, {"x": 9, "y": 8},
        obstacles=[obstacle], bounds={"width": 10, "height": 10},
    )
    assert len(state.current_path) > 2
    for _ in range(500):
        controller.update(0.05, [obstacle], bounds={"width": 10, "height": 10})
        assert not obstacle.inflated_contains(state.x, state.y, state.radius + 0.1)
        if state.status == RobotStatus.PICKING:
            break
    assert state.status == RobotStatus.PICKING
    assert math.dist((state.x, state.y), (9, 5)) < 0.2


def test_fleet_agent_uses_astar_distance_when_world_is_available():
    from robot.agent import FleetAgent

    robot = {
        "robotId": "AMR1", "x": 1.0, "y": 5.0, "heading": 0.0,
        "status": "IDLE", "battery": 100.0, "currentTaskId": None,
        "blocked": False, "online": True, "radius": 0.4,
    }
    wall = Rect(4, 2, 2, 6)
    agent = FleetAgent(
        "AMR1", lambda: robot, lambda *_: None, lambda _: None,
        get_obstacles=lambda: [wall],
        get_world_bounds=lambda: (10.0, 10.0),
    )
    result = agent.compute_bid({"x": 9, "y": 5}, {"x": 9, "y": 8})
    assert result["eligible"]
    assert result["costs"]["travel"] > 11.0
