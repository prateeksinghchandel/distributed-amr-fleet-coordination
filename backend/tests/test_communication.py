"""Phase 2 transport contract tests without requiring a real Zenoh router."""

from common.models import ZenohEnvelope
from robot.communication import ZenohBus


class FakeSubscriber:
    def undeclare(self):
        self.undeclared = True


class FakeSession:
    def __init__(self):
        self.published = []
        self.subscribers = {}

    def put(self, topic, payload):
        self.published.append((topic, payload))

    def declare_subscriber(self, topic, callback):
        self.subscribers[topic] = callback
        return FakeSubscriber()


def test_encode_decode_preserves_json_envelope():
    raw = ZenohBus.encode("AMR1", "tasks/new", {"taskId": "T-001"})
    envelope = ZenohBus.decode(raw)
    assert isinstance(envelope, ZenohEnvelope)
    assert envelope.origin == "AMR1"
    assert envelope.type == "tasks/new"
    assert envelope.payload == {"taskId": "T-001"}


def test_publish_writes_envelope_to_same_topic():
    session = FakeSession()
    bus = ZenohBus(session, "server", None)
    bus.publish("world/state", {"width": 30, "height": 20})

    topic, raw = session.published[0]
    envelope = ZenohBus.decode(raw)
    assert topic == "world/state"
    assert envelope.origin == "server"
    assert envelope.type == topic
    assert envelope.payload["width"] == 30


def test_subscribe_decodes_and_dispatches_payload():
    session = FakeSession()
    received = []
    bus = ZenohBus(session, "AMR1", None)
    bus.subscribe("tasks/new", lambda topic, payload: received.append((topic, payload)))

    class Sample:
        payload = ZenohBus.encode("server", "tasks/new", {"taskId": "T-42"})

    session.subscribers["tasks/new"](Sample())
    assert received == [("tasks/new", {"taskId": "T-42"})]
