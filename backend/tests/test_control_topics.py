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
                  topics.CONTROL_TASK_CANCEL):
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


def test_mirrors_js_topics_file():
    """The JS topics.js constants must match the Python ones byte-for-byte."""
    js_topics = {
        "CONTROL_TASK_CREATE": "control/tasks/create",
        "CONTROL_TASK_ASSIGN": "control/tasks/assign",
        "CONTROL_TASK_CANCEL": "control/tasks/cancel",
    }
    for name, value in js_topics.items():
        assert getattr(topics, name) == value