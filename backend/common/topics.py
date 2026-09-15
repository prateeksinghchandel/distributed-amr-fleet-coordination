"""
topics.py — Zenoh topic string constants for the AMR Fleet Coordination System.

MUST mirror JS: dashboard/src/simulation/messages/topics.js exactly.
Both Python and JS nodes subscribe/publish on the same Zenoh network.
"""

TASK_NEW = "tasks/new"
"""Coordinator → Fleet: auction announcement for a new task."""

TASK_ASSIGNED = "tasks/assigned"
"""Coordinator → Winner robot: task committed to winner."""

TASK_CANCELLED = "tasks/cancelled"
"""Coordinator → Fleet: task cancelled (before or after assignment)."""

BID_PLACED = "auction/bids"
"""Robot → Coordinator: bid submission during an auction round."""

AUCTION_RESULT = "auction/results"
"""Coordinator → Fleet: winner + committed flag for a completed auction."""

ROBOT_TELEMETRY = "robots/telemetry"
"""Robot → Coordinator: position / status heartbeat, published every 0.5 s."""

WORLD_STATE = "world/state"
"""Coordinator → Fleet: warehouse dimensions, obstacles, robot roster (1 s heartbeat)."""

# robots/intent — PLANNED for Phase 4 (intention broadcasting between peers).
ROBOT_INTENT = "robots/intent"

CONTROL_TASK_CREATE = "control/tasks/create"
"""Dashboard → Coordinator: create a task (pickup/dropoff or randomCount) for auction."""

CONTROL_TASK_ASSIGN = "control/tasks/assign"
"""Dashboard → Coordinator: manually assign a pending task to a robot."""

CONTROL_TASK_CANCEL = "control/tasks/cancel"
"""Dashboard → Coordinator: cancel a task."""
