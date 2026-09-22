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
    --mode     SERVER_AUCTION | P2P_AUCTION   Auction mode (default: SERVER_AUCTION)
"""

from __future__ import annotations
import argparse
import json
import math
import sys
import time
import threading
from typing import Optional

from common.auction import AuctionMode
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
    p.add_argument("--mode", default=AuctionMode.SERVER_AUCTION,
                   choices=list(AuctionMode.VALID), help="Auction mode (default: SERVER_AUCTION)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# ServerNode — the main coordinator loop
# ---------------------------------------------------------------------------

class ServerNode:
    def __init__(self, layout: WarehouseLayout, roster: list[dict],
                 bus: ZenohBus, log, task_count: int,
                 auction_mode: str = AuctionMode.SERVER_AUCTION):
        self.layout = layout
        self.roster = roster
        self.bus = bus
        self.log = log
        self.task_count = task_count
        self.auction_mode = AuctionMode.normalize(auction_mode)

        self.telemetry = TelemetryManager(log=log.info)
        self.tasks = TaskManager(
            layout=layout,
            publish_cb=bus.publish,
            log=log.info,
            auction_mode=self.auction_mode,
        )
        # Share fleet view between task manager and telemetry
        self.tasks.fleet = self.telemetry.fleet

        # In SERVER_AUCTION mode the server aggregates bids and finalizes the
        # auction (selection happens in FleetAgent.select_winner, then the
        # server commits the selected winner to the task ledger). In P2P_AUCTION
        # there is NO coordinator auction agent: robots finalize among
        # themselves, the winner publishes AUCTION_COMMIT, and the server only
        # applies that first valid commit as a passive ledger.
        self.auction_agent = None
        if self.auction_mode == AuctionMode.SERVER_AUCTION:
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
                eligible_filter=self._auction_eligible_robot,
            )

        self._pad_owners: dict[str, str] = {}
        self._standby_spots: list[dict] = []
        self._rebuild_charge_allocation()

        # Runtime obstacles (session-scoped, added via the dashboard). They join
        # the preset shelf racks in every world/state publication, so robots
        # path-plan around them until the coordinator restarts.
        self.runtime_obstacles: list[dict] = []
        self._obstacle_seq = 0

        self._started = False
        self._last_world = -float("inf")
        self._running = True

        # Subscribe to topics
        bus.subscribe(topics.ROBOT_TELEMETRY, self._on_telemetry)
        if self.auction_agent is not None:
            bus.subscribe(topics.TASK_NEW, self._on_task_new)
            bus.subscribe(topics.BID_PLACED, self._on_bid_placed)
        bus.subscribe(topics.AUCTION_RESULT, self._on_auction_result)
        bus.subscribe(topics.AUCTION_COMMIT, self._on_auction_commit)
        bus.subscribe(topics.CONTROL_TASK_CREATE, self._on_control_create)
        bus.subscribe(topics.CONTROL_TASK_ASSIGN, self._on_control_assign)
        bus.subscribe(topics.CONTROL_TASK_CANCEL, self._on_control_cancel)
        bus.subscribe(topics.CONTROL_OBSTACLE_ADD, self._on_control_obstacle_add)
        bus.subscribe(topics.CONTROL_OBSTACLE_REMOVE, self._on_control_obstacle_remove)
        bus.subscribe(topics.CONTROL_ROSTER_UPDATE, self._on_control_roster_update)

        # Publish initial world state
        self._publish_world()

    def _rebuild_charge_allocation(self) -> None:
        self._pad_owners = {}
        self._standby_spots = []
        has_home_bay = any(bool(r.get("homeBay")) for r in self.roster)
        if has_home_bay:
            for r in self.roster:
                hb = r.get("homeBay")
                if hb and isinstance(hb, dict):
                    kind = hb.get("kind")
                    if kind == "pad" and hb.get("padId"):
                        self._pad_owners[hb["padId"]] = r["id"]
                    elif kind == "standby":
                        self._standby_spots.append({
                            "slot": hb.get("slot", 0),
                            "x": hb.get("x", r.get("x", 0.0)),
                            "y": hb.get("y", r.get("y", 0.0)),
                            "robotId": r["id"],
                        })
        else:
            if hasattr(self.layout, "charging_zone") and hasattr(self.layout.charging_zone, "pads"):
                for pad in self.layout.charging_zone.pads:
                    if getattr(pad, "assigned_robot_id", None):
                        self._pad_owners[pad.id] = pad.assigned_robot_id

    def _on_control_roster_update(self, _topic: str, payload: dict) -> None:
        raw_roster = payload.get("roster", [])
        new_roster = []
        for item in raw_roster:
            if isinstance(item, str):
                new_roster.append(parse_roster_entry(item))
            elif isinstance(item, dict):
                new_roster.append(item)
        old_ids = {r["id"] for r in self.roster}
        new_ids = {r["id"] for r in new_roster}
        removed = old_ids - new_ids
        for rid in removed:
            self.tasks.cancel_tasks_for_robot(rid)
        self.roster = new_roster
        self._rebuild_charge_allocation()
        self._publish_world()
        self.log.info(f"Dynamic roster update applied ({len(self.roster)} robot(s))")

    # ------------------------------------------------------------------
    # Zenoh message handlers
    # ------------------------------------------------------------------

    def _on_telemetry(self, _topic: str, payload: dict) -> None:
        self.telemetry.on_telemetry(payload)
        if self.auction_agent is not None:
            self.auction_agent.on_robot_telemetry(payload)

    def _on_task_new(self, _topic: str, payload: dict) -> None:
        if self.auction_agent is not None:
            self.auction_agent.on_task_new(payload, time.time())

    def _on_auction_result(self, _topic: str, payload: dict) -> None:
        # SERVER_AUCTION: the coordinator agent attempts the commit before it
        # publishes this result. P2P_AUCTION: the winning robot publishes the
        # result for fleet observability. Either way the task manager records
        # the outcome and handles retry/defer semantics.
        self.tasks.handle_auction_result(payload)

    def _on_auction_commit(self, _topic: str, payload: dict) -> None:
        """P2P_AUCTION only — apply the first valid robot commit to the ledger."""
        if self.auction_mode != AuctionMode.P2P_AUCTION:
            return
        task_id = payload.get("taskId", "")
        auction_id = payload.get("auctionId") or task_id
        winner = payload.get("winner")
        robot_id = payload.get("robotId")
        if not task_id or not winner:
            self.log.warning("[P2P] malformed AUCTION_COMMIT ignored")
            return
        ok = self.tasks.apply_auction_commit(
            task_id, auction_id, winner, payload.get("bids", [])
        )
        self.log.info(
            f"[P2P] commit task {task_id} winner {winner} "
            f"by {robot_id}: {'applied' if ok else 'ignored'}"
        )

    def _coordinator_commit(self, task_id: str, winner: str, bids: list[dict]) -> bool:
        committed = self.tasks.assign_task(task_id, winner, source="auction")
        if committed:
            self.log.info(f"[AUCTION] {task_id} committed for {winner}")
        else:
            self.log.warning(f"[AUCTION] {task_id} commit failed for {winner}")
        return committed

    def _auction_eligible_robot(self, robot_id: str) -> bool:
        """Auction candidates must be available per the authoritative ledger."""
        return self.tasks.is_robot_available(robot_id)

    def _on_bid_placed(self, _topic: str, payload: dict) -> None:
        if self.auction_agent is None:
            return
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

    def _on_control_obstacle_add(self, _topic: str, payload: dict) -> None:
        """Dashboard → coordinator: add/update a runtime obstacle rectangle."""
        try:
            width = float(payload.get("width", 0))
            height = float(payload.get("height", 0))
            if width < 0.5 or height < 0.5:
                self.log.warning("[CTRL] obstacle too small, ignored")
                return
            ox = max(0.0, min(float(payload.get("x", 0)), max(0.0, self.layout.width - 0.5)))
            oy = max(0.0, min(float(payload.get("y", 0)), max(0.0, self.layout.height - 0.5)))
            width = max(0.5, min(width, self.layout.width - ox))
            height = max(0.5, min(height, self.layout.height - oy))
            oid = str(payload.get("id") or "").strip()
            if not oid:
                self._obstacle_seq += 1
                oid = f"OBS{self._obstacle_seq:02d}"
            obstacle = {"id": oid, "x": round(ox, 2), "y": round(oy, 2),
                        "width": round(width, 2), "height": round(height, 2),
                        "type": "obstacle"}
            replaced = False
            for i, existing in enumerate(self.runtime_obstacles):
                if existing["id"] == oid:
                    self.runtime_obstacles[i] = obstacle
                    replaced = True
                    break
            if not replaced:
                self.runtime_obstacles.append(obstacle)
            verb = "updated" if replaced else "added"
            self.log.info(
                f"[CTRL] obstacle {oid} {width:.1f}×{height:.1f}m "
                f"@ ({ox:.1f}, {oy:.1f}) {verb}"
            )
            self._publish_world()
        except Exception as exc:
            self.log.warning(f"[CTRL] malformed obstacle payload: {exc}")

    def _on_control_obstacle_remove(self, _topic: str, payload: dict) -> None:
        """Dashboard → coordinator: remove a runtime obstacle by id."""
        oid = str(payload.get("id") or "").strip()
        if not oid:
            self.log.warning("[CTRL] obstacle remove needs an id")
            return
        before = len(self.runtime_obstacles)
        self.runtime_obstacles = [o for o in self.runtime_obstacles if o["id"] != oid]
        if len(self.runtime_obstacles) != before:
            self.log.info(f"[CTRL] obstacle {oid} removed")
            self._publish_world()
        else:
            self.log.warning(f"[CTRL] no runtime obstacle with id {oid}")

    # ------------------------------------------------------------------
    # Main step loop
    # ------------------------------------------------------------------

    def step(self) -> None:
        now = time.time()
        self.telemetry.prune_stale()
        if self.auction_agent is not None:
            self.auction_agent.tick(STEP_S, now)
        self.tasks.sync_tasks()
        self.tasks.sync_auction_timeout(now)
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

    def _pad_centroid(self, pad: dict) -> tuple[float, float]:
        sp = pad.get("spawnPoint")
        if sp and isinstance(sp, dict) and "x" in sp and "y" in sp:
            return (float(sp["x"]), float(sp["y"]))
        w = float(pad.get("width", 0.0))
        h = float(pad.get("height", 0.0))
        return (float(pad.get("x", 0.0)) + w / 2.0, float(pad.get("y", 0.0)) + h / 2.0)

    def _pad_occupied_by_robot(self, pad: dict) -> Optional[str]:
        """Check telemetry fleet for a robot currently within PAD_OCCUPANCY of pad centre."""
        c = self._pad_centroid(pad)
        for r in self.telemetry.fleet.values():
            if not r.get("online", True):
                continue
            rx = float(r.get("x", 0.0))
            ry = float(r.get("y", 0.0))
            if math.dist((rx, ry), c) <= 0.6:
                return r.get("robotId")
        return None

    def _publish_world(self) -> None:
        pads = self.layout.charging_pads()
        for pad in pads:
            pad["assignedRobotId"] = self._pad_owners.get(pad["id"])
            occupant = self._pad_occupied_by_robot(pad)
            pad["occupiedBy"] = occupant
        self.bus.publish(topics.WORLD_STATE, {
            "width": self.layout.width,
            "height": self.layout.height,
            "obstacles": self.layout.obstacles() + self.runtime_obstacles,
            "chargingPads": pads,
            "deliveryDocks": self.layout.delivery_docks(),
            "roster": self.roster,
            "standbySpots": self._standby_spots,
            "auctionMode": self.auction_mode,
            "tasks": self.tasks.world_tasks(),
            "taskStats": self.tasks.task_stats(),
            "robotStats": self.tasks.robot_stats(),
            "metrics": self.tasks.metrics_dict(),
            "navStats": self.telemetry.nav_summary(),
        })
        self.log.info(
            f"World state published ({self.layout.width}×{self.layout.height}, "
            f"{len(self.roster)} robot(s), mode={self.auction_mode})"
        )

    def shutdown(self) -> None:
        self._running = False
        self.log.info("Shutting down")
        if self.auction_agent is not None:
            self.auction_agent.rounds.clear()
        self.bus.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_roster_entry(token: str) -> dict:
    parts = token.strip().split(":")
    if len(parts) >= 8:
        rid, x, y, kind, slot, pad_id, sb_x, sb_y = parts[:8]
        is_pad = (kind == "pad")
        return {
            "id": rid,
            "x": float(x),
            "y": float(y),
            "homeBay": {
                "kind": kind,
                "slot": int(slot) if slot else 0,
                "padId": pad_id if pad_id else None,
                "x": float(x) if is_pad else (float(sb_x) if sb_x else float(x)),
                "y": float(y) if is_pad else (float(sb_y) if sb_y else float(y)),
            },
        }
    if len(parts) >= 3:
        return {
            "id": parts[0],
            "x": float(parts[1]),
            "y": float(parts[2]),
            "homeBay": None,
        }
    return {"id": parts[0], "x": 0.0, "y": 0.0, "homeBay": None}


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
        roster = [parse_roster_entry(entry) for entry in args.roster.split(",") if entry.strip()]
    else:
        from fleet_manager.charge_allocation import allocate_charge_spots
        roster = allocate_charge_spots(layout, [r.id for r in layout.robots])
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
    node = ServerNode(layout, roster, bus, log, args.tasks, auction_mode=args.mode)

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
