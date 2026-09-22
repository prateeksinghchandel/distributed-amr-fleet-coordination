"""
regionmap.py — Explicit chokepoint / conflict-zone detection.

The RegionMap is derived *deterministically* from the warehouse layout (bounds +
static/runtime obstacles) and exposes the regions that require explicit
coordination:

* ``corridor``     — a contiguous run of cells too narrow for two robots to pass.
* ``intersection`` — a wide hub where >= ``INTERSECTION_EXITS`` corridors and/or
                     terminal zones converge.
* ``terminal``     — delivery docks and charging pads (parking/loading zones).

A robot must obtain a temporary reservation before entering a region that another
robot has claimed (see :mod:`reservation`). The map itself is pure geometry and
contains no coordination state, so every robot that shares the same world state
derives the identical region set — which keeps arbitration distributed and
deterministic.

Clearance model
---------------
Every free cell is assigned a clearance equal to the distance (meters) from the
cell centre to the nearest obstacle edge or warehouse boundary. A cell whose
clearance is below ``CORRIDOR_CLEARANCE_M`` can carry at most one robot, so runs
of such cells are single-lane corridors.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Sequence

from common.geometry import Rect

REGION_RES = 0.5                 # meters per cell
CORRIDOR_CLEARANCE_M = 1.0       # clearance below this => single-robot corridor
MIN_REGION_CELLS = 2             # smallest corridor we coordinate
MAX_CORRIDOR_SPAN_M = 12.0       # longest corridor span we treat as a region
EXIT_NEIGHBOUR_CELLS = 2         # chebyshev radius gathering region exits
INTERSECTION_EXITS = 3           # distinct exits required for an intersection hub
TERMINAL_RADIUS_M = 1.0          # radius of a terminal (dock / pad) zone
REGION_PAD_M = 0.9               # padding added to region bounding boxes


def _segment_intersects_rect(a, b, xmin, ymin, xmax, ymax) -> bool:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - xmin), (dx, xmax - a[0]),
                 (-dy, a[1] - ymin), (dy, ymax - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t0 <= t1


@dataclass
class Region:
    rid: str
    kind: str                       # "corridor" | "intersection" | "terminal"
    cells: set = field(default_factory=set)
    bbox: Optional[tuple] = None    # (xmin, ymin, xmax, ymax) inflated by REGION_PAD_M
    center: tuple = (0.0, 0.0)

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        if self.bbox is None:
            return False
        xmin, ymin, xmax, ymax = self.bbox
        return xmin - pad <= x <= xmax + pad and ymin - pad <= y <= ymax + pad

    def intersects(self, ax: float, ay: float, bx: float, by: float,
                   pad: float = 0.0) -> bool:
        if self.bbox is None:
            return False
        xmin, ymin, xmax, ymax = self.bbox
        return _segment_intersects_rect(
            (ax, ay), (bx, by),
            xmin - pad, ymin - pad, xmax + pad, ymax + pad,
        )

    def exits(self) -> int:
        return len(self.cells)


class RegionMap:
    """Static geometry of warehouse conflict zones for one world revision."""

    def __init__(self, width: float, height: float, obstacles: Sequence = (),
                 resolution: float = REGION_RES,
                 corridor_clearance: float = CORRIDOR_CLEARANCE_M,
                 robot_radius: float = 0.4):
        self.width = float(width)
        self.height = float(height)
        self.resolution = resolution
        self.corridor_clearance = corridor_clearance
        self.cols = max(1, math.ceil(self.width / resolution))
        self.rows = max(1, math.ceil(self.height / resolution))
        self.obstacles = list(obstacles)
        self.robot_radius = robot_radius
        self.regions: list[Region] = []
        self._blocked: set = set()
        self._free: set = set()
        self._clearance: dict = {}
        self._corridor_components: dict[int, set] = {}
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _cell(self, x: float, y: float):
        return (min(self.cols - 1, max(0, int(x / self.resolution))),
                min(self.rows - 1, max(0, int(y / self.resolution))))

    def _centre(self, cell):
        return ((cell[0] + 0.5) * self.resolution, (cell[1] + 0.5) * self.resolution)

    def _in_bounds(self, cell) -> bool:
        return 0 <= cell[0] < self.cols and 0 <= cell[1] < self.rows

    def _obstacle_at(self, ox: float, oy: float, rect: Rect) -> bool:
        return (rect.x <= ox <= rect.x + rect.width and
                rect.y <= oy <= rect.y + rect.height)

    def _build(self) -> None:
        rects = []
        for obs in self.obstacles:
            if isinstance(obs, dict):
                rects.append(Rect(float(obs["x"]), float(obs["y"]),
                                  float(obs.get("width", 0)), float(obs.get("height", 0))))
            else:
                rects.append(obs)

        free: set = set()
        blocked: set = set()
        for c in range(self.cols):
            for r in range(self.rows):
                cx, cy = self._centre((c, r))
                hit = any(rect.contains(cx, cy) for rect in rects)
                if hit:
                    blocked.add((c, r))
                else:
                    free.add((c, r))
        self._free = free
        self._blocked = blocked
        self._clearance = self._distance_transform(free, blocked)
        self._corridor_components = {}
        self._build_regions()

    def _distance_transform(self, free, blocked) -> dict:
        """Multi-source BFS over the free grid; returns cell -> clearance (m).

        Only obstacle cells are sources; the warehouse boundary is not treated
        as a blocked cell so floor space along walls is *not* flagged as a
        corridor (an open lane beside a wall is wide enough for two robots).
        """
        dist: dict = {}
        queue = deque()
        for cell in blocked:
            dist[cell] = 0.0
            queue.append(cell)
        while queue:
            cur = queue.popleft()
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    if dc == 0 and dr == 0:
                        continue
                    nxt = (cur[0] + dc, cur[1] + dr)
                    if not self._in_bounds(nxt) or nxt in dist or nxt in blocked:
                        continue
                    dist[nxt] = dist[cur] + self.resolution
                    queue.append(nxt)
        return dist

    def _blocked_in_direction(self, cell, dx: int, dy: int) -> bool:
        """True when an obstacle lies within ``corridor_clearance`` along a ray."""
        steps = int(self.corridor_clearance / self.resolution)
        cx, cy = cell
        for i in range(1, steps + 1):
            nxt = (cx + dx * i, cy + dy * i)
            if not self._in_bounds(nxt):
                return False  # warehouse boundary is not an obstacle
            if nxt in self._blocked:
                return True
        return False

    def _gap_cell(self, cell) -> bool:
        """A free cell that is squeezed between obstacles on two opposite sides.

        This is the single-lane condition: two robots cannot pass side by side.
        Obstacle-perimeter cells (only one blocked direction) are excluded so
        corridors do not bleed into shelf rings.
        """
        if self._clearance.get(cell, 0.0) >= self.corridor_clearance:
            return False
        ns = self._blocked_in_direction(cell, 0, -1) and \
            self._blocked_in_direction(cell, 0, 1)
        ew = self._blocked_in_direction(cell, -1, 0) and \
            self._blocked_in_direction(cell, 1, 0)
        return ns or ew

    def _build_regions(self) -> None:
        # 1. corridor components: cells with an obstacle on two opposite sides
        gap = {cell for cell in self._free if self._gap_cell(cell)}
        seen: set = set()
        comp_id = 0
        for cell in gap:
            if cell in seen:
                continue
            component = set()
            queue = [cell]
            seen.add(cell)
            while queue:
                cur = queue.pop()
                component.add(cur)
                for dc, dr in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nxt = (cur[0] + dc, cur[1] + dr)
                    if nxt in gap and nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            self._corridor_components[comp_id] = component
            comp_id += 1

        index = 1
        for comp in self._corridor_components.values():
            if len(comp) < MIN_REGION_CELLS:
                continue
            span = self._component_span(comp)
            if span > MAX_CORRIDOR_SPAN_M:
                continue
            self.regions.append(self._region_from_cells(
                f"CP-{index}", "corridor", comp))
            index += 1

        # 2. terminal zones last so they can seed intersection detection too.
        self._terminal_zones: list[Region] = []

        # 3. intersection hubs: wide cells with >= INTERSECTION_EXITS exits
        hubs: list[tuple] = []
        for cell in self._free:
            if self._clearance.get(cell, 0.0) < self.corridor_clearance:
                continue
            if self._exit_count(cell) >= INTERSECTION_EXITS:
                hubs.append(cell)
        clusters = self._cluster_hubs(hubs)
        for cl in clusters:
            self.regions.append(self._region_from_cells(
                f"IX-{index}", "intersection", cl))
            index += 1

    def _component_span(self, comp) -> float:
        """Bounding span of a component in meters (largest axis)."""
        cs = [c[0] for c in comp]
        rs = [c[1] for c in comp]
        return max(max(cs) - min(cs), max(rs) - min(rs)) * self.resolution

    def _exit_count(self, cell) -> int:
        exits = set()
        radius = EXIT_NEIGHBOUR_CELLS
        for ridd, comp in self._corridor_components.items():
            if any(abs(cell[0] - c) <= radius and abs(cell[1] - r) <= radius
                   for c, r in comp):
                exits.add(("corridor", ridd))
        for term in self._terminal_zones:
            if term.contains(*self._centre(cell), pad=exits):
                exits.add(("terminal", term.rid))
        return len(exits)

    def _cluster_hubs(self, hubs: list) -> list:
        clusters: list[list] = []
        used: set = set()
        connect = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        for hub in hubs:
            if hub in used:
                continue
            cluster = []
            stack = [hub]
            used.add(hub)
            while stack:
                cur = stack.pop()
                cluster.append(cur)
                for dc, dr in connect:
                    nxt = (cur[0] + dc, cur[1] + dr)
                    if nxt in hubs and nxt not in used:
                        used.add(nxt)
                        stack.append(nxt)
            clusters.append(cluster)
        return clusters

    def _region_from_cells(self, rid: str, kind: str, cells) -> Region:
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        xmin, xmax = min(xs) * self.resolution, (max(xs) + 1) * self.resolution
        ymin, ymax = min(ys) * self.resolution, (max(ys) + 1) * self.resolution
        centre = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        region = Region(rid=rid, kind=kind, cells=set(cells), center=centre)
        region.bbox = (xmin - REGION_PAD_M, ymin - REGION_PAD_M,
                       xmax + REGION_PAD_M, ymax + REGION_PAD_M)
        return region

    def add_terminal(self, rid: str, x: float, y: float) -> None:
        """Register a dock / charging pad point as a terminal zone."""
        region = Region(rid=rid, kind="terminal", cells=set(), center=(x, y))
        region.bbox = (x - TERMINAL_RADIUS_M - REGION_PAD_M,
                       y - TERMINAL_RADIUS_M - REGION_PAD_M,
                       x + TERMINAL_RADIUS_M + REGION_PAD_M,
                       y + TERMINAL_RADIUS_M + REGION_PAD_M)
        self.regions.append(region)
        self._terminal_zones.append(region)

    # ------------------------------------------------------------------
    # Queries used by the coordination layer
    # ------------------------------------------------------------------

    def regions_at(self, x: float, y: float, pad: float = 0.0) -> list:
        return [r for r in self.regions
                if self._region_geom(r).contains(x, y, pad)]

    def regions_intersecting(self, ax: float, ay: float, bx: float, by: float,
                             pad: float = 0.0) -> list:
        out = []
        for r in self.regions:
            if r.intersects(ax, ay, bx, by, pad):
                out.append(r)
        return out

    def first_region_on_path(self, path: list, from_index: int,
                             within: Optional[int] = None) -> Optional[Region]:
        pts = path[max(0, from_index):]
        if within is not None:
            pts = pts[:within]
        if len(pts) < 2:
            return None
        for r in self.regions:
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                if r.intersects(ax, ay, bx, by, pad=0.0):
                    return r
        return None

    def region_by_id(self, rid: str) -> Optional[Region]:
        for r in self.regions:
            if r.rid == rid:
                return r
        return None

    @staticmethod
    def _region_geom(r: Region):
        return r

    def revision_token(self) -> str:
        return f"{round(self.width,2)}x{round(self.height,2)}:{len(self.obstacles)}"