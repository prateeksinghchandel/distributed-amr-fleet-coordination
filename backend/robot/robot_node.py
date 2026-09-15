"""
robot_node.py — Individual AMR process over Zenoh.

Usage:
    python -m robot.robot_node --id AMR1 [--url ws/127.0.0.1:10000]

Options:
    --id    Robot identifier (required, e.g. AMR1)
    --url   Zenoh bridge WebSocket URL (default: ws/127.0.0.1:10000)
    --x     Initial x position (overridden by world/state spawn)
    --y     Initial y position
"""

from __future__ import annotations
import argparse
import json
import sys
import time
import threading

from robot.communication import ZenohBus, open_session

from common import topics
from common.logger import get_logger
from robot.agent import FleetAgent
from robot.controller import MotionController, ObstacleRect, RobotState, Status

STEP_S = 0.05   # 50 ms


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AMR Robot Node (Python/Zenoh)")
    p.add_argument("--id", required=True, help="Robot identifier, e.g. AMR1")
    p.add_argument("--url", default="ws/127.0.0.1:10000")
    p.add_argument("--x", type=float, default=0.0, help="Initial x (overridden by world/state)")
    p.add_argument("--y", type=float, default=0.0, help="Initial y (overridden by world/state)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# RobotNode
# ---------------------------------------------------------------------------

class RobotNode:
    def __init__(self, robot_id: str, init_x: float, init_y: float,
                 bus: ZenohBus, log):
        self.robot_id = robot_id
        self.bus = bus
        self.log = log

        # Mutable robot state
        self.state = RobotState(id=robot_id, x=init_x, y=init_y)

        # Motion controller
        self.controller = MotionController(self.state, log=log.info)

        # World data received from coordinator
        self.world: dict = {}
        self.obstacles: list[ObstacleRect] = []
        self.spawned = False

        # Pending tasks (keyed by taskId)
        self.pending_tasks: dict[str, dict] = {}

        # Fleet agent (bidding)
        self.agent = FleetAgent(
            robot_id=robot_id,
            get_robot=lambda: self.state.to_dict(),
            publish_cb=bus.publish,
            log=log.info,
            get_obstacles=lambda: self.obstacles,
            get_fleet_snapshot=lambda: list(self.agent.fleet.values()) if hasattr(self, "agent") else [],
            get_world_bounds=lambda: (self.world["width"], self.world["height"]) if self.world else None,
            disable_finalize=True,   # coordinator commits
        )

        # Subscribe to all relevant topics
        bus.subscribe(topics.WORLD_STATE, self._on_world_state)
        bus.subscribe(topics.TASK_NEW, self._on_task_new)
        bus.subscribe(topics.TASK_ASSIGNED, self._on_task_assigned)
        bus.subscribe(topics.TASK_CANCELLED, self._on_task_cancelled)
        bus.subscribe(topics.AUCTION_RESULT, self._on_auction_result)
        bus.subscribe(topics.ROBOT_TELEMETRY, self._on_peer_telemetry)

        self._running = True
        self._last_tick = None

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_world_state(self, _topic: str, payload: dict) -> None:
        self.world = payload
        self.obstacles = [
            ObstacleRect(id=o.get("id", "?"), x=o["x"], y=o["y"],
                         width=o["width"], height=o["height"])
            for o in payload.get("obstacles", [])
        ]
        # Spawn at roster position on first world state
        if not self.spawned:
            roster = payload.get("roster", [])
            me = next((r for r in roster if r["id"] == self.robot_id), None)
            if me:
                self.state.x = me["x"]
                self.state.y = me["y"]
                self.state.home_charge_bay = (me["x"], me["y"])
                self.spawned = True
                self.log.info(
                    f"Spawned at ({self.state.x:.1f}, {self.state.y:.1f}) "
                    f"— home charging bay set, world {payload['width']}×{payload['height']}"
                )
        else:
            self.log.debug(
                f"World state updated ({payload['width']}×{payload['height']}, "
                f"{len(payload.get('roster', []))} robots)"
            )

    def _on_task_new(self, _topic: str, payload: dict) -> None:
        task_id = payload.get("taskId", "?")
        self.pending_tasks[task_id] = payload
        now = time.time()
        self.agent.on_task_new(payload, now)
        self.log.info(
            f"Task {task_id} announced "
            f"({payload['pickup']['x']},{payload['pickup']['y']}) → "
            f"({payload['dropoff']['x']},{payload['dropoff']['y']})"
        )

    def _on_task_assigned(self, _topic: str, payload: dict) -> None:
        task_id = payload.get("taskId", "?")
        robot_id = payload.get("robotId", "")
        self.agent.forget_round(task_id)
        if robot_id != self.robot_id:
            return
        task = self.pending_tasks.get(task_id)
        if not task:
            self.log.warning(f"Assigned task {task_id} but no announcement remembered")
            return
        self.state.current_task_id = task_id
        self.controller.assign_task(
            task_id,
            pickup=task["pickup"],
            dropoff=task["dropoff"],
            obstacles=self.obstacles,
            bounds={"width": self.world.get("width", 30), "height": self.world.get("height", 20)},
        )
        self.log.info(f"{self.robot_id} started task {task_id} (via auction)")

    def _on_task_cancelled(self, _topic: str, payload: dict) -> None:
        task_id = payload.get("taskId", "?")
        self.pending_tasks.pop(task_id, None)
        self.agent.forget_round(task_id)
        if self.state.current_task_id == task_id:
            self.controller.cancel_task()
            self.log.info(f"Task {task_id} cancelled")

    def _on_auction_result(self, _topic: str, payload: dict) -> None:
        task_id = payload.get("taskId", "?")
        winner = payload.get("winner")
        committed = payload.get("committed", False)
        mine = " (mine)" if winner == self.robot_id else ""
        self.agent.forget_round(task_id)
        self.log.info(
            f"[AUCTION] result {task_id} → winner={winner} committed={committed}{mine}"
        )

    def _on_peer_telemetry(self, _topic: str, payload: dict) -> None:
        self.agent.on_robot_telemetry(payload)

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        now = time.time()
        if self._last_tick is None:
            dt = STEP_S
        else:
            dt = min(max(now - self._last_tick, 0.01), 0.2)
        self._last_tick = now

        self.controller.update(dt, self.obstacles, bounds={"width": self.world.get("width", 30), "height": self.world.get("height", 20)})
        self.agent.tick(dt, now)

        # If task just completed, reset current_task_id so agent can accept new tasks
        if self.state.status == Status.COMPLETED:
            self.state.current_task_id = None

    def shutdown(self) -> None:
        self._running = False
        self.bus.close()
        self.log.info("Robot node stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    log = get_logger(args.id)
    log.info(f"Starting robot {args.id}")

    log.info(f"Connecting to Zenoh bridge at {args.url} …")
    try:
        session = open_session(args.url)
    except Exception as exc:
        log.error(f"Cannot open Zenoh session: {exc}")
        sys.exit(1)
    log.info("Connected to Zenoh bridge")

    bus = ZenohBus(session, args.id, log)
    node = RobotNode(args.id, args.x, args.y, bus, log)

    stop_event = threading.Event()

    def _sighandler(signum, _frame):
        log.info("Signal received — shutting down")
        stop_event.set()

    import signal
    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    log.info(f"Robot {args.id} running. Press Ctrl+C to stop.")
    try:
        while not stop_event.is_set():
            node.tick()
            time.sleep(STEP_S)
    finally:
        node.shutdown()
        session.close()


if __name__ == "__main__":
    main()
