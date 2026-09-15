"""In-process Phase 2 pipeline test: server + 3 AMR bidding agents."""

from common import topics
from robot.agent import FleetAgent
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


def test_distributed_auction_pipeline_assigns_lowest_bid():
    bus = FakeBus()
    layout = build_from_preset("MICRO_FULFILLMENT")
    server = ServerNode(layout, layout.roster(), bus, _Log(), task_count=0)

    states = [
        RobotState(id="AMR1", x=25.0, y=3.0),
        RobotState(id="AMR2", x=15.0, y=3.0),
        RobotState(id="AMR3", x=18.0, y=10.0),
    ]
    agents = []
    snapshots = [s.to_dict() for s in states]
    for state in states:
        agent = FleetAgent(
            robot_id=state.id,
            get_robot=state.to_dict,
            publish_cb=bus.publish,
            log=lambda _msg: None,
            get_obstacles=lambda: [],
            get_fleet_snapshot=lambda snapshots=snapshots: snapshots,
            disable_finalize=True,
        )
        agents.append(agent)

    # Give every agent the same initial fleet snapshot before the auction.
    for agent in agents:
        for snapshot in snapshots:
            agent.on_robot_telemetry(snapshot)

    # Connect robot-side subscriptions to the fake network.
    for agent in agents:
        bus.subscribe(topics.TASK_NEW, lambda topic, payload, a=agent: a.on_task_new(payload, 0.0))
        bus.subscribe(topics.ROBOT_TELEMETRY, lambda topic, payload, a=agent: a.on_robot_telemetry(payload))

    # Check in all robots so the server is allowed to dispatch.
    for state in states:
        bus.publish(topics.ROBOT_TELEMETRY, state.to_dict())

    task = server.tasks.create_task(
        pickup=Point2D(5.0, 4.0),
        dropoff=Point2D(2.0, 7.0),
        announce=True,
    )

    assert task is not None
    assert task.status.value == "ASSIGNED"
    assert task.assigned_robot_id == "AMR2"
    assert server.tasks.auctions[-1]["winner"] == "AMR2"
    assert server.tasks.auctions[-1]["committed"] is True
    assert any(topic == topics.TASK_NEW for topic, _ in bus.messages)
    assert any(topic == topics.BID_PLACED for topic, _ in bus.messages)
    assert any(topic == topics.AUCTION_RESULT for topic, _ in bus.messages)
    assert any(topic == topics.TASK_ASSIGNED for topic, _ in bus.messages)


class _Log:
    def info(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass
