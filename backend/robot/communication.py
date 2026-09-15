"""Zenoh transport wrapper used by Python server and AMR nodes.

The wrapper keeps the wire contract deliberately small: every publication is
an UTF-8 JSON ``{origin, type, payload}`` envelope on the topic itself.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from common.models import ZenohEnvelope


class ZenohBus:
    """Small, testable wrapper around an eclipse-zenoh session."""

    def __init__(self, session: Any, origin: str, log: Any):
        self._session = session
        self._origin = origin
        self._log = log
        self._subs: list[Any] = []

    @staticmethod
    def encode(origin: str, topic: str, payload: dict) -> bytes:
        envelope = ZenohEnvelope(origin=origin, type=topic, payload=payload)
        return envelope.model_dump_json().encode("utf-8")

    @staticmethod
    def decode(data: bytes | bytearray | memoryview | Any) -> ZenohEnvelope:
        if hasattr(data, "to_bytes"):
            data = data.to_bytes()
        elif not isinstance(data, (bytes, bytearray, memoryview)):
            data = bytes(data)
        return ZenohEnvelope.model_validate_json(bytes(data).decode("utf-8"))

    def publish(self, topic: str, payload: dict) -> None:
        self._session.put(topic, self.encode(self._origin, topic, payload))

    def subscribe(self, topic: str, callback: Callable[[str, dict], None]) -> None:
        def _handler(sample: Any) -> None:
            try:
                envelope = self.decode(sample.payload)
                if envelope.type != topic:
                    self._log.warning(
                        "Ignoring envelope type %r received on %r",
                        envelope.type,
                        topic,
                    )
                    return
                callback(envelope.type, envelope.payload)
            except Exception as exc:  # malformed peer data must not kill the callback
                self._log.warning("Bad message on %s: %s", topic, exc)

        self._subs.append(self._session.declare_subscriber(topic, _handler))

    def close(self) -> None:
        for sub in self._subs:
            try:
                sub.undeclare()
            except Exception:
                pass
        self._subs.clear()


def open_session(endpoint: str) -> Any:
    """Open a Zenoh session connected to a router endpoint.

    Importing the backend does not require eclipse-zenoh; only starting a live
    node does. This makes the protocol and business logic easy to unit test.
    """
    try:
        import zenoh
    except ImportError as exc:
        raise RuntimeError(
            "eclipse-zenoh is required for live networking. Install with "
            "'pip install -r backend/requirements.txt'."
        ) from exc

    config = zenoh.Config()
    config.insert_json5("connect/endpoints", json.dumps([endpoint]))
    return zenoh.open(config)
