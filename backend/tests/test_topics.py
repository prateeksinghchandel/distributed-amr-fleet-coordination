"""
test_topics.py — Verify topic strings match JS topics.js exactly.

These are the interoperability contracts between Python and JS nodes.
All strings must remain byte-identical.
"""

import pytest
from common import topics


class TestTopicStrings:
    """Every topic must match the JS constant value exactly."""

    def test_task_new(self):
        assert topics.TASK_NEW == "tasks/new"

    def test_task_assigned(self):
        assert topics.TASK_ASSIGNED == "tasks/assigned"

    def test_task_cancelled(self):
        assert topics.TASK_CANCELLED == "tasks/cancelled"

    def test_bid_placed(self):
        assert topics.BID_PLACED == "auction/bids"

    def test_auction_result(self):
        assert topics.AUCTION_RESULT == "auction/results"

    def test_robot_telemetry(self):
        assert topics.ROBOT_TELEMETRY == "robots/telemetry"

    def test_world_state(self):
        assert topics.WORLD_STATE == "world/state"

    def test_robot_intent_planned(self):
        """robots/intent is planned but must be present as a constant."""
        assert topics.ROBOT_INTENT == "robots/intent"

    def test_all_topics_are_strings(self):
        all_topics = [
            topics.TASK_NEW, topics.TASK_ASSIGNED, topics.TASK_CANCELLED,
            topics.BID_PLACED, topics.AUCTION_RESULT,
            topics.ROBOT_TELEMETRY, topics.WORLD_STATE, topics.ROBOT_INTENT,
        ]
        for t in all_topics:
            assert isinstance(t, str), f"Topic {t!r} is not a str"
            assert len(t) > 0, "Topic must not be empty"
            assert "/" in t, f"Topic {t!r} must contain a '/' (Zenoh key expression)"

    def test_no_duplicate_topics(self):
        all_topics = [
            topics.TASK_NEW, topics.TASK_ASSIGNED, topics.TASK_CANCELLED,
            topics.BID_PLACED, topics.AUCTION_RESULT,
            topics.ROBOT_TELEMETRY, topics.WORLD_STATE,
        ]
        assert len(all_topics) == len(set(all_topics)), "Duplicate topic strings detected"

    def test_topics_use_slash_not_dot(self):
        """Zenoh key expressions use '/' not '.'"""
        all_topics = [
            topics.TASK_NEW, topics.TASK_ASSIGNED, topics.TASK_CANCELLED,
            topics.BID_PLACED, topics.AUCTION_RESULT,
            topics.ROBOT_TELEMETRY, topics.WORLD_STATE,
        ]
        for t in all_topics:
            assert "." not in t, f"Topic {t!r} must not contain '.'"
