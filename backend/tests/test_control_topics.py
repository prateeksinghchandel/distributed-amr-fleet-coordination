"""
test_control_topics.py — Dashboard → Coordinator command path.

The React dashboard publishes commands on the control/* topics. The server
subscribes to them and turns each command into a real TaskManager operation.
These tests drive the handlers through a fake bus, exactly like a dashboard
would over Zenoh.
"""

from common import topics
from robot.controller import RobotState
from server.server_node import ServerNode
from server.warehouse import Point2D, build_from_preset


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
    def info(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _make_server():
    bus = FakeBus()
    layout = build_from_preset("MICRO_FULFILLMENT")
    server = ServerNode(layout, layout.roster(), bus, _Log(), task_count=0)
    return bus, layout, server


def _checkin(bus, layout, robot_id):
    """Send one telemetry frame so the coordinator's fleet view knows a robot."""
    x, y = 10.0, 3.0
    for entry in layout.roster():
        if entry["id"] == robot_id:
            x, y = entry["x"], entry["y"]
            break
    bus.publish(topics.ROBOT_TELEMETRY, {
        "robotId": robot_id, "x": x, "y": y, "heading": 0.0,
        "status": "IDLE", "battery": 100.0, "currentTaskId": None,
        "blocked": False, "online": True,
    })


def test_server_subscribes_to_all_control_topics():
    bus, _layout, _server = _make_server()
    for topic in (topics.CONTROL_TASK_CREATE, topics.CONTROL_TASK_ASSIGN,
                  topics.CONTROL_TASK_CANCEL, topics.CONTROL_OBSTACLE_ADD,
                  topics.CONTROL_OBSTACLE_REMOVE):
        assert topic in bus.handlers, f"Server did not subscribe to {topic}"


def test_control_create_creates_and_auctions_task():
    bus, _layout, server = _make_server()
    before = len(server.tasks.tasks)
    bus.publish(topics.CONTROL_TASK_CREATE, {
        "pickup": {"x": 5.0, "y": 4.0},
        "dropoff": {"x": 2.0, "y": 7.0},
    })
    assert len(server.tasks.tasks) == before + 1
    task = server.tasks.tasks[-1]
    assert task.pickup.x == 5.0 and task.pickup.y == 4.0
    assert task.dropoff.x == 2.0 and task.dropoff.y == 7.0
    assert any(topic == topics.CONTROL_TASK_CREATE for topic, _ in bus.messages)
    # The task is queued for the fleet auction (TASK_NEW gets published).
    assert any(topic == topics.TASK_NEW for topic, _ in bus.messages)


def test_control_create_random_count():
    bus, _layout, server = _make_server()
    before = len(server.tasks.tasks)
    bus.publish(topics.CONTROL_TASK_CREATE, {"randomCount": 3})
    assert len(server.tasks.tasks) == before + 3
    assert any(topic == topics.TASK_NEW for topic, _ in bus.messages)


def test_control_create_malformed_is_ignored():
    bus, _layout, server = _make_server()
    before = len(server.tasks.tasks)
    bus.publish(topics.CONTROL_TASK_CREATE, {"pickup": {"x": "nope"}})
    assert len(server.tasks.tasks) == before  # rejected, no crash


def test_control_assign_assigns_pending_task():
    bus, layout, server = _make_server()
    _checkin(bus, layout, "AMR1")
    task = server.tasks.create_task(
        pickup=Point2D(5.0, 4.0), dropoff=Point2D(2.0, 7.0), announce=False
    )
    assert task.status.value == "PENDING"
    bus.publish(topics.CONTROL_TASK_ASSIGN, {"taskId": task.id, "robotId": "AMR1"})
    assert task.status.value == "ASSIGNED"
    assert task.assigned_robot_id == "AMR1"
    assert any(topic == topics.TASK_ASSIGNED for topic, _ in bus.messages)


def test_control_assign_missing_robot_is_rejected():
    bus, _layout, server = _make_server()
    task = server.tasks.create_task(
        pickup=Point2D(5.0, 4.0), dropoff=Point2D(2.0, 7.0), announce=False
    )
    bus.publish(topics.CONTROL_TASK_ASSIGN, {"taskId": task.id, "robotId": "GHOST"})
    assert task.status.value == "PENDING"


def test_control_cancel_cancels_task():
    bus, _layout, server = _make_server()
    task = server.tasks.create_task(
        pickup=Point2D(5.0, 4.0), dropoff=Point2D(2.0, 7.0), announce=False
    )
    bus.publish(topics.CONTROL_TASK_CANCEL, {"taskId": task.id})
    assert task.status.value == "CANCELLED"
    assert any(topic == topics.TASK_CANCELLED for topic, _ in bus.messages)


def test_control_cancel_unknown_task_is_noop():
    bus, _layout, server = _make_server()
    bus.publish(topics.CONTROL_TASK_CANCEL, {"taskId": "T-DOESNOTEXIST"})
    assert not any(topic == topics.TASK_CANCELLED for topic, _ in bus.messages)


def test_control_obstacle_add_applies_and_publishes_world():
    bus, layout, server = _make_server()
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {"x": 3.0, "y": 2.0, "width": 1.5, "height": 2.5})
    assert len(server.runtime_obstacles) == 1
    obs = server.runtime_obstacles[0]
    assert obs["id"].startswith("OBS")
    assert obs["type"] == "obstacle"
    assert obs["x"] == 3.0 and obs["y"] == 2.0
    assert obs["width"] == 1.5 and obs["height"] == 2.5
    # World/state re-published immediately with preset racks + runtime obstacle.
    world = bus.messages[-1]
    assert world[0] == topics.WORLD_STATE
    assert len(world[1]["obstacles"]) == len(layout.obstacles()) + 1


def test_control_obstacle_add_clamps_to_bounds():
    bus, layout, server = _make_server()
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {
        "x": layout.width - 0.2, "y": layout.height - 0.2, "width": 50.0, "height": 50.0,
    })
    obs = server.runtime_obstacles[0]
    assert obs["x"] + obs["width"] <= layout.width + 0.001
    assert obs["y"] + obs["height"] <= layout.height + 0.001


def test_control_obstacle_add_too_small_ignored():
    bus, _layout, server = _make_server()
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {"x": 3.0, "y": 2.0, "width": 0.2, "height": 2.0})
    assert server.runtime_obstacles == []


def test_control_obstacle_add_updates_existing_id():
    bus, _layout, server = _make_server()
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {"id": "OBS", "x": 1.0, "y": 1.0, "width": 1, "height": 1})
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {"id": "OBS", "x": 8.0, "y": 8.0, "width": 2, "height": 2})
    assert len(server.runtime_obstacles) == 1
    assert server.runtime_obstacles[0]["x"] == 8.0 and server.runtime_obstacles[0]["width"] == 2.0


def test_control_obstacle_remove_removes_and_publishes():
    bus, _layout, server = _make_server()
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {"x": 1.0, "y": 1.0, "width": 1, "height": 1})
    bus.publish(topics.CONTROL_OBSTACLE_ADD, {"x": 8.0, "y": 8.0, "width": 1, "height": 1})
    oid = server.runtime_obstacles[0]["id"]
    bus.publish(topics.CONTROL_OBSTACLE_REMOVE, {"id": oid})
    assert len(server.runtime_obstacles) == 1
    assert all(o["id"] != oid for o in server.runtime_obstacles)
    world = bus.messages[-1]
    assert world[0] == topics.WORLD_STATE
    assert world[1]["obstacles"][-1]["id"] == server.runtime_obstacles[0]["id"]


def test_control_obstacle_remove_unknown_noop():
    bus, _layout, server = _make_server()
    messages_before = len(bus.messages)
    bus.publish(topics.CONTROL_OBSTACLE_REMOVE, {"id": "NOPE"})
    # Only the remove command itself lands on the bus — no world re-publish.
    assert len(bus.messages) == messages_before + 1
    assert bus.messages[-1][0] == topics.CONTROL_OBSTACLE_REMOVE
    assert not any(topic == topics.WORLD_STATE for topic, _ in bus.messages[messages_before:])


def test_mirrors_js_topics_file():
    """The JS topics.js constants must match the Python ones byte-for-byte."""
    js_topics = {
        "CONTROL_TASK_CREATE": "control/tasks/create",
        "CONTROL_TASK_ASSIGN": "control/tasks/assign",
        "CONTROL_TASK_CANCEL": "control/tasks/cancel",
        "CONTROL_OBSTACLE_ADD": "control/world/obstacles/add",
        "CONTROL_OBSTACLE_REMOVE": "control/world/obstacles/remove",
        "CONTROL_ROSTER_UPDATE": "control/roster/update",
    }
    for name, value in js_topics.items():
        assert getattr(topics, name) == value


def test_world_state_tags_pads_with_assigned_robot_id():
    """World state chargingPads must include assignedRobotId from pad owners."""
    bus = FakeBus()
    layout = build_from_preset("ECOMMERCE")
    pads = layout.charging_pads()
    assert len(pads) >= 2
    pad0_id = pads[0]["id"]
    roster = [
        {"id": "AMR1", "x": 27.75, "y": 5.0, "homeBay": {"kind": "pad", "padId": pad0_id, "slot": 0}},
    ]
    server = ServerNode(layout, roster, bus, _Log(), task_count=0)
    world_msgs = [msg for top, msg in bus.messages if top == topics.WORLD_STATE]
    assert len(world_msgs) >= 1
    world = world_msgs[-1]
    assigned_pads = {p["id"]: p.get("assignedRobotId") for p in world["chargingPads"]}
    assert assigned_pads.get(pad0_id) == "AMR1"


def test_world_state_contains_standby_spots():
    """World state includes standbySpots list when standby robots exist."""
    bus = FakeBus()
    layout = build_from_preset("ECOMMERCE")
    roster = [
        {"id": "AMR1", "x": 27.75, "y": 5.0, "homeBay": {"kind": "pad", "padId": "BAY-1", "slot": 0}},
        {"id": "AMR4", "x": 2.0, "y": 4.0, "homeBay": {"kind": "standby", "padId": None, "slot": 0, "x": 2.0, "y": 4.0}},
    ]
    server = ServerNode(layout, roster, bus, _Log(), task_count=0)
    world_msgs = [msg for top, msg in bus.messages if top == topics.WORLD_STATE]
    world = world_msgs[-1]
    assert "standbySpots" in world
    assert len(world["standbySpots"]) == 1
    assert world["standbySpots"][0]["robotId"] == "AMR4"
    assert world["standbySpots"][0]["slot"] == 0


def test_control_roster_update_updates_coordinator_dynamically():
    """CONTROL_ROSTER_UPDATE updates coordinator roster, pad allocation, and publishes WORLD_STATE."""
    bus, layout, server = _make_server()

    # Publish roster update adding AMR_NEW and updating homeBay
    new_roster = [
        {"id": "AMR_NEW", "x": 1.0, "y": 1.0, "homeBay": {"kind": "standby", "slot": 0, "padId": None, "x": 1.0, "y": 1.0}},
    ]
    bus.publish(topics.CONTROL_ROSTER_UPDATE, {"roster": new_roster})

    assert len(server.roster) == 1
    assert server.roster[0]["id"] == "AMR_NEW"
    assert len(server._standby_spots) == 1
    assert server._standby_spots[0]["robotId"] == "AMR_NEW"

    # Verify a new WORLD_STATE message was emitted with updated roster & standby spots
    latest_world = [msg for top, msg in bus.messages if top == topics.WORLD_STATE][-1]
    assert len(latest_world["roster"]) == 1
    assert latest_world["roster"][0]["id"] == "AMR_NEW"
    assert len(latest_world["standbySpots"]) == 1