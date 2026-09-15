"""
warehouse.py — Python mirror of WarehouseBuilder.js and Warehouse.js.

Generates the same logistics layout (delivery-left, AMR-charging-right, shelves-center)
used by the JS frontend so both sides share an identical spatial model.
"""

from __future__ import annotations
import math
import random
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    """Axis-aligned rectangle."""
    x: float
    y: float
    width: float
    height: float

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x2 and self.y <= py <= self.y2

    def inflated_contains(self, px: float, py: float, margin: float = 0.35) -> bool:
        return (self.x - margin <= px <= self.x2 + margin and
                self.y - margin <= py <= self.y2 + margin)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass
class Point2D:
    x: float
    y: float

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


# ---------------------------------------------------------------------------
# Warehouse zone models
# ---------------------------------------------------------------------------

@dataclass
class DeliveryStation:
    id: str
    name: str
    rect: Rect
    dropoff_point: Point2D


@dataclass
class DeliveryZone:
    rect: Rect
    stations: list[DeliveryStation] = field(default_factory=list)

    def random_dropoff(self) -> Point2D:
        if not self.stations:
            return Point2D(self.rect.x + self.rect.width / 2, self.rect.y + self.rect.height / 2)
        st = random.choice(self.stations)
        return st.dropoff_point


@dataclass
class ChargingPad:
    id: str
    name: str
    rect: Rect
    spawn_point: Point2D
    assigned_robot_id: Optional[str] = None


@dataclass
class ChargingZone:
    rect: Rect
    pads: list[ChargingPad] = field(default_factory=list)


@dataclass
class ShelfRack:
    id: str
    name: str
    rect: Rect
    pick_points: list[Point2D] = field(default_factory=list)

    def to_obstacle_dict(self) -> dict:
        return {"id": self.id, "x": self.rect.x, "y": self.rect.y,
                "width": self.rect.width, "height": self.rect.height, "type": "shelf"}

    def random_pick_point(self) -> Point2D:
        if not self.pick_points:
            return Point2D(self.rect.x + self.rect.width / 2, self.rect.y + self.rect.height / 2)
        return random.choice(self.pick_points)


@dataclass
class RobotSpawn:
    id: str
    x: float
    y: float
    radius: float = 0.4
    max_speed: float = 2.0

    def to_roster_dict(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y}


# ---------------------------------------------------------------------------
# Presets (mirror WAREHOUSE_PRESETS in WarehouseBuilder.js)
# ---------------------------------------------------------------------------

WAREHOUSE_PRESETS: dict[str, dict] = {
    "ECOMMERCE": {
        "id": "ecommerce",
        "name": "E-Commerce Fulfillment Center",
        "width": 30, "height": 20,
        "delivery_width": 4.5, "delivery_docks": 3,
        "charging_width": 4.5, "charging_pads": 3,
        "shelf_rows": 2, "shelf_cols": 3,
        "shelf_width": 3.5, "shelf_depth": 1.6,
        "aisle_width": 3.0, "robot_count": 3,
    },
    "DISTRIBUTION": {
        "id": "distribution",
        "name": "High-Density Distribution Hub",
        "width": 40, "height": 25,
        "delivery_width": 5.0, "delivery_docks": 4,
        "charging_width": 5.0, "charging_pads": 5,
        "shelf_rows": 3, "shelf_cols": 4,
        "shelf_width": 4.0, "shelf_depth": 1.6,
        "aisle_width": 2.8, "robot_count": 5,
    },
    "MICRO_FULFILLMENT": {
        "id": "micro",
        "name": "Micro-Fulfillment Facility",
        "width": 20, "height": 14,
        "delivery_width": 3.5, "delivery_docks": 2,
        "charging_width": 3.5, "charging_pads": 2,
        "shelf_rows": 2, "shelf_cols": 2,
        "shelf_width": 3.0, "shelf_depth": 1.4,
        "aisle_width": 2.5, "robot_count": 2,
    },
}


# ---------------------------------------------------------------------------
# WarehouseLayout — the full computed layout
# ---------------------------------------------------------------------------

@dataclass
class WarehouseLayout:
    width: float
    height: float
    delivery_zone: DeliveryZone
    charging_zone: ChargingZone
    shelves: list[ShelfRack]
    robots: list[RobotSpawn]

    # --- convenience accessors ---

    def obstacles(self) -> list[dict]:
        """Return shelf racks as obstacle dicts for world/state publication."""
        return [s.to_obstacle_dict() for s in self.shelves]

    def roster(self) -> list[dict]:
        return [r.to_roster_dict() for r in self.robots]

    def charging_pads(self) -> list[dict]:
        return [
            {
                "id": pad.id,
                "name": pad.name,
                "x": pad.rect.x,
                "y": pad.rect.y,
                "width": pad.rect.width,
                "height": pad.rect.height,
                "spawnPoint": pad.spawn_point.to_dict(),
                "assignedRobotId": pad.assigned_robot_id,
            }
            for pad in self.charging_zone.pads
        ]

    def delivery_docks(self) -> list[dict]:
        return [
            {
                "id": station.id,
                "name": station.name,
                "x": station.rect.x,
                "y": station.rect.y,
                "width": station.rect.width,
                "height": station.rect.height,
                "dropoffPoint": station.dropoff_point.to_dict(),
            }
            for station in self.delivery_zone.stations
        ]

    def random_pick_point(self) -> Point2D:
        """Pick a random point from any shelf rack (for task generation)."""
        if not self.shelves:
            return Point2D(self.width / 2, self.height / 2)
        return random.choice(self.shelves).random_pick_point()

    def random_delivery_point(self) -> Point2D:
        """Pick a random delivery dock dropoff point."""
        return self.delivery_zone.random_dropoff()

    def free_point(self) -> Point2D:
        """Return a random point not inside any obstacle (for fallback task gen)."""
        obstacle_rects = [s.rect for s in self.shelves]
        for _ in range(500):
            px = random.uniform(0.5, self.width - 0.5)
            py = random.uniform(0.5, self.height - 0.5)
            if not any(r.inflated_contains(px, py, 0.2) for r in obstacle_rects):
                return Point2D(px, py)
        return Point2D(self.width / 2, self.height / 2)


# ---------------------------------------------------------------------------
# Builder function (mirrors buildWarehouseConfig() in WarehouseBuilder.js)
# ---------------------------------------------------------------------------

def build_warehouse(params: Optional[dict] = None) -> WarehouseLayout:
    """
    Build a structured warehouse logistics layout.

    Args:
        params: Optional dict with keys matching WAREHOUSE_PRESETS fields.
                Merges on top of ECOMMERCE defaults.

    Returns:
        WarehouseLayout with delivery zone, charging zone, shelves, robot spawns.
    """
    p = params or {}
    width = max(12.0, min(float(p.get("width", 30)), 100.0))
    height = max(10.0, min(float(p.get("height", 20)), 100.0))

    delivery_width = max(2.5, min(float(p.get("delivery_width", 4.5)), width * 0.3))
    delivery_docks = max(1, min(int(p.get("delivery_docks", 3)), 8))

    charging_width = max(2.5, min(float(p.get("charging_width", 4.5)), width * 0.3))
    charging_pads_count = max(1, min(int(p.get("charging_pads", 3)), 12))
    robot_count = max(1, min(int(p.get("robot_count", 3)), charging_pads_count))

    # 1. Delivery zone (left: x=0 to delivery_width)
    delivery_rect = Rect(0, 0, delivery_width, height)
    stations: list[DeliveryStation] = []
    dock_h = min(2.5, (height - 2) / delivery_docks - 0.5)
    dock_spacing = (height - 2 - dock_h * delivery_docks) / (delivery_docks + 1)
    dock_w = delivery_width - 1.2
    for i in range(delivery_docks):
        sy = 1 + dock_spacing + i * (dock_h + dock_spacing)
        stations.append(DeliveryStation(
            id=f"DOCK-{i + 1}",
            name=f"Delivery Dock {i + 1}",
            rect=Rect(0.6, sy, dock_w, dock_h),
            dropoff_point=Point2D(0.6 + dock_w / 2, sy + dock_h / 2),
        ))
    delivery_zone = DeliveryZone(rect=delivery_rect, stations=stations)

    # 2. Charging zone (right: x = width - charging_width to width)
    charging_x = width - charging_width
    charging_rect = Rect(charging_x, 0, charging_width, height)
    pads: list[ChargingPad] = []
    pad_h = min(2.2, (height - 2) / charging_pads_count - 0.4)
    pad_spacing = (height - 2 - pad_h * charging_pads_count) / (charging_pads_count + 1)
    pad_w = charging_width - 1.2
    pad_x = charging_x + 0.6
    for i in range(charging_pads_count):
        py = 1 + pad_spacing + i * (pad_h + pad_spacing)
        spawn = Point2D(pad_x + pad_w / 2, py + pad_h / 2)
        pads.append(ChargingPad(
            id=f"BAY-{i + 1}",
            name=f"Charging Bay {i + 1}",
            rect=Rect(pad_x, py, pad_w, pad_h),
            spawn_point=spawn,
            assigned_robot_id=f"AMR{i + 1}" if i < robot_count else None,
        ))
    charging_zone = ChargingZone(rect=charging_rect, pads=pads)

    # 3. Shelves (center: between delivery_width+buffer and charging_x-buffer)
    center_x0 = delivery_width + 1.5
    center_x1 = charging_x - 1.5
    center_w = max(4.0, center_x1 - center_x0)

    shelf_rows = max(1, min(int(p.get("shelf_rows", 2)), 6))
    shelf_cols = max(1, min(int(p.get("shelf_cols", 3)), 8))
    shelf_width = max(1.5, min(float(p.get("shelf_width", 3.5)), 6.0))
    shelf_depth = max(0.8, min(float(p.get("shelf_depth", 1.6)), 3.0))

    x_spacing = (center_w - shelf_cols * shelf_width) / (shelf_cols - 1) if shelf_cols > 1 else 0
    avail_h = height - 4
    y_spacing = (avail_h - shelf_rows * shelf_depth) / (shelf_rows - 1) if shelf_rows > 1 else 0

    shelves: list[ShelfRack] = []
    shelf_index = 1
    for r in range(shelf_rows):
        for c in range(shelf_cols):
            if shelf_cols > 1:
                sx = center_x0 + c * (shelf_width + max(1.2, x_spacing))
            else:
                sx = center_x0 + (center_w - shelf_width) / 2

            if shelf_rows > 1:
                sy = 2 + r * (shelf_depth + max(1.8, y_spacing))
            else:
                sy = (height - shelf_depth) / 2

            if sx + shelf_width > center_x1 + 0.2 or sy + shelf_depth > height - 1.0:
                continue

            shelf_id = f"SH-{shelf_index}"
            shelf_index += 1
            pick_points = [
                Point2D(sx + shelf_width * 0.25, max(0.8, sy - 0.7)),
                Point2D(sx + shelf_width * 0.75, max(0.8, sy - 0.7)),
                Point2D(sx + shelf_width * 0.25, min(height - 0.8, sy + shelf_depth + 0.7)),
                Point2D(sx + shelf_width * 0.75, min(height - 0.8, sy + shelf_depth + 0.7)),
            ]
            shelves.append(ShelfRack(
                id=shelf_id,
                name=f"Shelf {shelf_id}",
                rect=Rect(sx, sy, shelf_width, shelf_depth),
                pick_points=pick_points,
            ))

    # 4. Robot spawns (at their charging pad centre)
    robots: list[RobotSpawn] = []
    for i in range(robot_count):
        pad = pads[i] if i < len(pads) else None
        robots.append(RobotSpawn(
            id=f"AMR{i + 1}",
            x=pad.spawn_point.x if pad else width - 2,
            y=pad.spawn_point.y if pad else 3 + i * 3,
            radius=float(p.get("robot_radius", 0.4)),
            max_speed=float(p.get("robot_speed", 2.0)),
        ))

    return WarehouseLayout(
        width=width,
        height=height,
        delivery_zone=delivery_zone,
        charging_zone=charging_zone,
        shelves=shelves,
        robots=robots,
    )


def build_from_preset(preset_key: str, overrides: Optional[dict] = None) -> WarehouseLayout:
    """Build from a named preset with optional overrides."""
    preset = WAREHOUSE_PRESETS.get(preset_key.upper())
    if not preset:
        raise ValueError(f"Unknown preset '{preset_key}'. Choices: {list(WAREHOUSE_PRESETS)}")
    params = dict(preset)
    if overrides:
        params.update(overrides)
    return build_warehouse(params)
