"""geometry.py — Shared 2D geometry primitives for backends and AMRs.

`Rect` is used both by the warehouse layout (shelves, docks, pads) and by the
AMR controllers/planners (obstacle inflation). Keeping it here gives every
Python component a single definition instead of per-module copies.
"""

from __future__ import annotations
from dataclasses import dataclass


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