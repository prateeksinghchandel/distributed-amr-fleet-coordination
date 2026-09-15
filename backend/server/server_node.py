"""
server_node.py — Central fleet coordinator process over Zenoh.

Usage:
    python -m server.server_node [OPTIONS]

Options:
    --preset   ECOMMERCE | DISTRIBUTION | MICRO_FULFILLMENT  (default: ECOMMERCE)
    --tasks    N          Generate N random tasks after robots report in
    --url      ws/HOST:PORT   Zenoh bridge URL  (default: ws/127.0.0.1:10000)
    --width    W          Override warehouse width
    --height   H          Override warehouse height
    --roster   id:x:y,id:x:y   Override robot roster (default: from layout)
"""

from __future__ import annotations
import argparse
import json
import sys
import time
import threading
from typing import Optional

from robot.agent import FleetAgent
from robot.communication import ZenohBus, open_session
from common import topics
from common.logger import get_logger
from server.warehouse import build_from_preset, build_warehouse, WAREHOUSE_PRESETS, WarehouseLayout, Point2D
from server.task_manager import TaskManager
from server.telemetry_manager import TelemetryManager

STEP_S = 0.05            # 50 ms tick
WORLD_HEARTBEAT_S = 1.0  # publish world/state every 1 s
ORIGIN = "server"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AMR Fleet Coordinator (Python/Zenoh)")
    p.add_argument("--preset", default="ECOMMERCE",
                   choices=list(WAREHOUSE_PRESETS), metavar="PRESET",
                   help="Warehouse preset (default: ECOMMERCE)")
    p.add_argument("--tasks", type=int, default=1,
                   help="Number of random tasks to generate after robots report in (default: 1)")
    p.add_argument("--url", default="ws/127.0.0.1:10000",
                   help="Zenoh bridge WebSocket URL (default: ws/127.0.0.1:10000)")
    p.add_argument("--width", type=float, default=None)
    p.add_argument("--height", type=float, default=None)
    p.add_argument("--roster", default=None,
                   help="Override robot roster: 'AMR1:x:y,AMR2:x:y'")
    return p.parse_args()


# ---------------------------------------------------------------------------
# ServerNode — the main coordinator loop
# ---------------------------------------------------------------------------

class ServerNode:
    def __init__(self, layout: WarehouseLayout, roster: list[dict],
                 bus: ZenohBus, log, task_count: int):
        self.layout = layout
        self.roster = roster
        self.bus = bus
        self.log = log
        self.task_count = task_count

        self.telemetry = TelemetryManager(log=log.info)
        self.tasks = TaskManager(
            layout=layout,
            publish_cb=bus.publish,
            log=log.info,
        )
        # Share fleet view between task manager and telemetry
        self.tasks.fleet = self.telemetry.fleet

        # The server aggregates bids but does not invent a bid. Winner selection
        # happens in FleetAgent.select_winner(), then the server only commits the
        # selected winner to the task ledger.
        self._coordinator_state = {
            "robotId": "server",
            "x": 0.0, "y": 0.0, "heading": 0.0,
            "status": "IDLE", "battery": 100.0,
            "currentTaskId": None, "blocked": False, "online": False,
        }
        self.auction_agent = FleetAgent(
            robot_id="server",
            get_robot=lambda: self._coordinator_state,
            publish_cb=bus.publish,
            log=log.info,
            get_obstacles=lambda: [],
            get_fleet_snapshot=self.telemetry.snapshot,
            disable_finalize=False,
            coordinator_commit=self._coordinator_commit,
        )

        self._started = False
        self._last_world = -float("inf")
        self._running = True

        # Subscribe to topics
        bus.subscribe(topics.ROBOT_TELEMETRY, self._on_telemetry)
        bus.subscribe(topics.TASK_NEW, self._on_task_new)
        bus.subscribe(topics.BID_PLACED, self._on_bid_placed)
        bus.subscribe(topics.AUCTION_RESULT, self._on_auction_result)
        bus.subscribe(topics.CONTROL_TASK_CREATE, self._on_control_create)
        bus.subscribe(topics.CONTROL_TASK_ASSIGN, self._on_control_assign)
        bus.subscribe(topics.CONTROL_TASK_CANCEL, self._on_control_cancel)

        # Publish initial world state
        self._publish_world()

    # ------------------------------------------------------------------
    # Zenoh message handlers
    # ------------------------------------------------------------------

    def _on_telemetry(self, _topic: str, payload: dict) -> None:
        self.telemetry.on_telemetry(payload)
        self.auction_agent.on_robot_telemetry(payload)

    def _on_task_new(self, _topic: str, payload: dict) -> None:
        self.auction_agent.on_task_new(payload, time.time())

    def _on_auction_result(self, _topic: str, payload: dict) -> None:
        # The coordinator agent has already attempted the commit before it
        # publishes this result. The task manager records the outcome and
        # handles retry/defer semantics.
        self.tasks.handle_auction_result(payload)

    def _coordinator_commit(self, task_id: str, winner: str, bids: list[dict]) -> bool:
        committed = self.tasks.assign_task(task_id, winner, source="auction")
        if committed:
            self.log.info(f"[AUCTION] {task_id} committed for {winner}")
        else:
            self.log.warning(f"[AUCTION] {task_id} commit failed for {winner}")
        return committed

    def _on_bid_placed(self, _topic: str, payload: dict) -> None:
        self.auction_agent.on_bid_placed(payload, time.time())
        self.log.debug(
            f"[BID] {payload.get('robotId')} bid {payload.get('bid')} "
            f"for task {payload.get('taskId')}"
        )

    # ------------------------------------------------------------------
    # Dashboard command handlers (control/* topics)
    # ------------------------------------------------------------------

    def _on_control_create(self, _topic: str, payload: dict) -> None:
        """Dashboard → coordinator: create a task (and auction it)."""
        try:
            random_count = payload.get("randomCount")
            if random_count is not None:
                n = max(0, min(int(random_count), 100))
                self.tasks.generate_random_tasks(n)
                self.log.info(f"[CTRL] generate {n} random task(s)")
                return
            pickup = payload.get("pickup", {})
            dropoff = payload.get("dropoff", {})
            pt = Point2D(float(pickup.get("x", 0.0)), float(pickup.get("y", 0.0)))
            dp = Point2D(float(dropoff.get("x", 0.0)), float(dropoff.get("y", 0.0)))
            priority = int(payload.get("priority", 1))
            task = self.tasks.create_task(pt, dp, priority=priority, announce=True)
            if task is None:
                self.log.warning("[CTRL] create task rejected")
            else:
                self.log.info(f"[CTRL] create task {task.id} → queued for auction")
        except Exception as exc:
            self.log.warning(f"[CTRL] malformed create payload: {exc}")

    def _on_control_assign(self, _topic: str, payload: dict) -> None:
        """Dashboard → coordinator: manually assign a pending task to a robot."""
        task_id = payload.get("taskId")
        robot_id = payload.get("robotId")
        if not task_id or not robot_id:
            self.log.warning("[CTRL] assign needs taskId and robotId")
            return
        ok = self.tasks.assign_task(task_id, robot_id, source="manual")
        self.log.info(f"[CTRL] assign {task_id} → {robot_id}: {'ok' if ok else 'rejected'}")

    def _on_control_cancel(self, _topic: str, payload: dict) -> None:
        """Dashboard → coordinator: cancel a task."""
        task_id = payload.get("taskId")
        if not task_id:
            return
        self.tasks.cancel_task(task_id)
        self.log.info(f"[CTRL] cancel task {task_id}")

    # ------------------------------------------------------------------
    # Main step loop
    # ------------------------------------------------------------------

    def step(self) -> None:
        now = time.time()
        self.telemetry.prune_stale()
        self.auction_agent.tick(STEP_S, now)
        self.tasks.sync_tasks()
        self.tasks.reconsider_waiting()

        if not self._started:
            if self.telemetry.is_all_reported(self.roster):
                self._started = True
                self.log.info(
                    f"All {len(self.roster)} robot(s) reported in — starting task dispatch"
                )
                if self.task_count > 0:
                    self.tasks.generate_random_tasks(self.task_count)
            elif not self.telemetry.fleet:
                # No robots have checked in yet; wait for them
                pass

        self.tasks.advance_auction()

        if now - self._last_world >= WORLD_HEARTBEAT_S:
            self._publish_world()
            self._last_world = now

    def _publish_world(self) -> None:
        self.bus.publish(topics.WORLD_STATE, {
            "width": self.layout.width,
            "height": self.layout.height,
            "obstacles": self.layout.obstacles(),
            "chargingPads": self.layout.charging_pads(),
            "deliveryDocks": self.layout.delivery_docks(),
            "roster": self.roster,
        })
        self.log.info(
            f"World state published ({self.layout.width}×{self.layout.height}, "
            f"{len(self.roster)} robot(s))"
        )

    def shutdown(self) -> None:
        self._running = False
        self.log.info("Shutting down")
        self.auction_agent.rounds.clear()
        self.bus.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    log = get_logger("server")

    # Build layout
    overrides: dict = {}
    if args.width:
        overrides["width"] = args.width
    if args.height:
        overrides["height"] = args.height
    layout = build_from_preset(args.preset, overrides or None)
    log.info(f"Layout: {args.preset} ({layout.width}×{layout.height}m, "
             f"{len(layout.shelves)} shelves, {len(layout.robots)} robot(s))")

    # Build roster
    if args.roster:
        roster = []
        for entry in args.roster.split(","):
            parts = entry.split(":")
            roster.append({"id": parts[0], "x": float(parts[1]), "y": float(parts[2])})
    else:
        roster = layout.roster()
    log.info(f"Roster: {[r['id'] for r in roster]}")

    # Open Zenoh session
    log.info(f"Connecting to Zenoh bridge at {args.url} …")
    try:
        session = open_session(args.url)
    except Exception as exc:
        log.error(f"Cannot open Zenoh session: {exc}")
        sys.exit(1)
    log.info("Connected to Zenoh bridge")

    bus = ZenohBus(session, ORIGIN, log)
    node = ServerNode(layout, roster, bus, log, args.tasks)

    stop_event = threading.Event()

    def _sighandler(signum, _frame):
        log.info("Signal received — shutting down")
        stop_event.set()

    import signal
    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    log.info(f"Server running (step={int(STEP_S * 1000)}ms). Press Ctrl+C to stop.")
    try:
        while not stop_event.is_set():
            node.step()
            time.sleep(STEP_S)
    finally:
        node.shutdown()
        session.close()
        log.info("Server stopped")


if __name__ == "__main__":
    main()
