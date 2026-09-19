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

AUCTION_COMMIT = "auction/commit"
"""Winner robot → Fleet: self-determined winner announcement (P2P_AUCTION only)."""

AUCTION_RESULT = "auction/results"
"""Coordinator → Fleet: winner + committed flag for a completed auction.

In P2P_AUCTION mode the result is published by the winning robot (for fleet
observability) rather than by the coordinator, which stays a passive ledger."""

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

CONTROL_OBSTACLE_ADD = "control/world/obstacles/add"
"""Dashboard → Coordinator: add a runtime obstacle rectangle to the live world.

Runtime obstacles are session-scoped (they join the preset shelf racks and are
removed when the coordinator restarts). The coordinator re-publishes world/state
immediately so robots re-plan around them."""

CONTROL_OBSTACLE_REMOVE = "control/world/obstacles/remove"
"""Dashboard → Coordinator: remove a runtime obstacle by its id."""
