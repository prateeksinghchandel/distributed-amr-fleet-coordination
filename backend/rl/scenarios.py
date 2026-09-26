"""
scenarios.py — scenario definitions and curriculum levels for RL training.

A scenario config is a plain dict controlling warehouse size, obstacle layout,
robot population, spawn/goal placement, dynamics and episode limits. Building a
``Scene`` from a config is fully seeded and deterministic, which makes
evaluation repeatable: the same ``seed`` always produces the same warehouse,
spawns, goals and global paths.

Difficulty is organised into a curriculum (levels 1..8, see ``CURRICULUM``).
Presets (``SCENARIO_PRESETS``) mirror the progression described in the project
requirements while staying usable directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from robot.planning.astar import AStarPlanner, PathNotFoundError

# bounds guard — never allow settings that can crash the simulation.
MAX_SIZE = 120.0
MAX_ROBOTS = 32
MAX_DENSITY = 0.20

MIN_SPAWN_SEP = 1.2   # m between distinct robots at reset
OBSTACLE_MARGIN = 1.0 # min border clearance from walls/obstacles when spawning


@dataclass
class RobotSpec:
    id: str
    x: float
    y: float
    heading: float
    max_speed: float
    radius: float
    goal: tuple[float, float]
    rl: bool = False          # True => controlled by the RL policy
    path: list = field(default_factory=list)  # global waypoints from A*


@dataclass
class DynamicObstacle:
    id: str
    x: float
    y: float
    width: float
    height: float
    vx: float
    vy: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float


@dataclass
class Scene:
    width: float
    height: float
    obstacles: list[dict]                 # {id,x,y,width,height,type} for planner
    dynamic_obstacles: list[DynamicObstacle]
    robots: list[RobotSpec]
    seed: int
    scenario_name: str
    difficulty: int

    def to_rects(self):
        from common.geometry import Rect
        return [Rect(o["x"], o["y"], o["width"], o["height"]) for o in self.obstacles]


def _clamp_scenario(cfg: dict) -> dict:
    """Sanitize user-supplied scenario config to safe limits."""
    cfg = dict(cfg)
    cfg["width"] = float(np.clip(float(cfg.get("width", 30.0)), 12.0, MAX_SIZE))
    cfg["height"] = float(np.clip(float(cfg.get("height", 20.0)), 12.0, MAX_SIZE))
    cfg["n_robots"] = int(np.clip(int(cfg.get("n_robots", 1)), 1, MAX_ROBOTS))
    cfg["n_rl"] = int(np.clip(int(cfg.get("n_rl", 1)), 1, cfg["n_robots"]))
    cfg["n_opponents"] = int(np.clip(int(cfg.get("n_opponents", 0)), 0,
                                     max(0, cfg["n_robots"] - cfg["n_rl"])))
    cfg["obstacle_density"] = float(
        np.clip(float(cfg.get("obstacle_density", 0.05)), 0.0, MAX_DENSITY)
    )
    cfg["max_steps"] = int(np.clip(int(cfg.get("max_steps", 600)), 20, 100000))
    cfg["dynamic_obstacles"] = bool(cfg.get("dynamic_obstacles", False))
    cfg["obstacle_mode"] = str(cfg.get("obstacle_mode", "random")) or "random"
    cfg["spawn_mode"] = str(cfg.get("spawn_mode", "random")) or "random"
    cfg["goal_mode"] = str(cfg.get("goal_mode", "random")) or "random"
    cfg["difficulty"] = int(np.clip(int(cfg.get("difficulty", 1)), 1, 8))
    return cfg


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

SCENARIO_PRESETS: dict[str, dict] = {
    "simple": {
        "name": "simple",
        "width": 24.0, "height": 16.0,
        "n_robots": 1, "n_rl": 1,
        "obstacle_mode": "none",
        "obstacle_density": 0.0,
        "spawn_mode": "edges", "goal_mode": "opposite",
        "max_steps": 400, "difficulty": 1,
    },
    "obstacle_avoidance": {
        "name": "obstacle_avoidance",
        "width": 30.0, "height": 20.0,
        "n_robots": 1, "n_rl": 1,
        "obstacle_mode": "random",
        "obstacle_density": 0.05,
        "spawn_mode": "edges", "goal_mode": "opposite",
        "max_steps": 500, "difficulty": 2,
    },
    "two_robot": {
        "name": "two_robot",
        "width": 30.0, "height": 20.0,
        "n_robots": 2, "n_rl": 1,
        "obstacle_mode": "random",
        "obstacle_density": 0.04,
        "spawn_mode": "random", "goal_mode": "random",
        "max_steps": 600, "difficulty": 3,
    },
    "intersection": {
        "name": "intersection",
        "width": 30.0, "height": 24.0,
        "n_robots": 3, "n_rl": 1,
        "obstacle_mode": "quadrant",
        "obstacle_density": 0.05,
        "spawn_mode": "corners", "goal_mode": "center",
        "max_steps": 700, "difficulty": 6,
    },
    "narrow_corridor": {
        "name": "narrow_corridor",
        "width": 24.0, "height": 20.0,
        "n_robots": 1, "n_rl": 1,
        "obstacle_mode": "corridor",
        "obstacle_density": 0.0,
        "spawn_mode": "edges", "goal_mode": "opposite",
        "max_steps": 600, "difficulty": 5,
    },
    "chokepoint": {
        "name": "chokepoint",
        "width": 24.0, "height": 20.0,
        "n_robots": 2, "n_rl": 1,
        "obstacle_mode": "corridor",
        "obstacle_density": 0.0,
        "spawn_mode": "opposite_edges", "goal_mode": "opposite",
        "max_steps": 900, "difficulty": 7,
    },
    "dense_traffic": {
        "name": "dense_traffic",
        "width": 36.0, "height": 24.0,
        "n_robots": 6, "n_rl": 1,
        "obstacle_mode": "random",
        "obstacle_density": 0.06,
        "spawn_mode": "random", "goal_mode": "random",
        "max_steps": 900, "difficulty": 8,
    },
    "random": {
        "name": "random",
        "width": 30.0, "height": 20.0,
        "n_robots": 3, "n_rl": 1,
        "obstacle_mode": "random",
        "obstacle_density": 0.05,
        "spawn_mode": "random", "goal_mode": "random",
        "dynamic_obstacles": False,
        "max_steps": 700, "difficulty": 4,
    },
}

# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------

CURRICULUM: dict[int, dict] = {
    1: {**SCENARIO_PRESETS["simple"], "name": "curriculum_1", "difficulty": 1},
    2: {**SCENARIO_PRESETS["obstacle_avoidance"], "name": "curriculum_2", "difficulty": 2},
    3: {**SCENARIO_PRESETS["two_robot"], "name": "curriculum_3", "difficulty": 3},
    4: {
        "name": "curriculum_4", "width": 32.0, "height": 22.0,
        "n_robots": 4, "n_rl": 1, "obstacle_mode": "random",
        "obstacle_density": 0.05, "spawn_mode": "random", "goal_mode": "random",
        "max_steps": 800, "difficulty": 4,
    },
    5: {**SCENARIO_PRESETS["narrow_corridor"], "name": "curriculum_5", "difficulty": 5},
    6: {**SCENARIO_PRESETS["intersection"], "name": "curriculum_6", "difficulty": 6},
    7: {
        "name": "curriculum_7", "width": 26.0, "height": 22.0,
        "n_robots": 3, "n_rl": 1, "obstacle_mode": "corridor",
        "obstacle_density": 0.0, "spawn_mode": "opposite_edges",
        "goal_mode": "opposite", "max_steps": 900, "difficulty": 7,
    },
    8: {**SCENARIO_PRESETS["dense_traffic"], "name": "curriculum_8", "difficulty": 8},
}


def scenario_config(name: str, overrides: Optional[dict] = None) -> dict:
    """Return a sanitized config for a named preset (falls back to 'random')."""
    base = SCENARIO_PRESETS.get(name, SCENARIO_PRESETS["random"])
    if overrides:
        base = {**base, **overrides}
    return _clamp_scenario(base)


def curriculum_config(level: int, overrides: Optional[dict] = None) -> dict:
    level = int(np.clip(int(level), 1, 8))
    base = dict(CURRICULUM[level])
    if overrides:
        base = {**base, **overrides}
    return _clamp_scenario(base)


def resolve_scenario(scenario: Optional[str], level: Optional[int],
                     overrides: Optional[dict] = None) -> tuple[dict, str]:
    """Unify scenario/level precedence across CLI, server and trainer.

    Rule (the least surprising choice, applied everywhere): when both a named
    scenario and a curriculum ``level`` are provided, the level explicitly
    wins and the caller is told so via the returned note. Returns
    ``(config, note)``.
    """
    overrides = overrides or {}
    if level is not None:
        cfg = curriculum_config(int(level), overrides)
        note = f"curriculum level {cfg['difficulty']} ('{cfg['name']}')"
        if scenario:
            note += (f" overrides scenario '{scenario}' "
                     "(both provided; level wins)")
        return cfg, note
    if scenario is not None:
        cfg = scenario_config(scenario, overrides)
        return cfg, f"scenario '{cfg['name']}'"
    cfg = scenario_config("obstacle_avoidance", overrides)
    return cfg, "default scenario 'obstacle_avoidance'"


class SceneSeedGen:
    """Deterministic, effectively non-repeating stream of scene seeds.

    The trainer owns one generator for its whole lifetime and shares it with
    every ``VectorEnv`` it builds, so re-building the vectorised environments
    (scenario switch, reset_episode, league promotion, opponent swap) continues
    the same campaign sequence instead of restarting the RNG and replaying
    scenes. The same master ``seed`` + configuration therefore reproduces the
    same scene campaign; consecutive draws are distinct (MT19937 does not
    repeat a 31-bit value within any realistic campaign length). Evaluation
    bypasses this stream entirely because it passes fixed explicit seeds.
    """

    def __init__(self, master_seed: int = 0):
        self._rng = np.random.RandomState(int(master_seed))
        self.draws = 0

    def next(self) -> int:
        self.draws += 1
        return int(self._rng.randint(0, 2 ** 31 - 1))

    def take(self, n: int) -> list[int]:
        return [self.next() for _ in range(n)]


# ---------------------------------------------------------------------------
# Scene building
# ---------------------------------------------------------------------------

def _rects_vs_point(rects, x, y, margin: float) -> bool:
    for r in rects:
        if r.x - margin <= x <= r.x + r.width + margin and \
           r.y - margin <= y <= r.y + r.height + margin:
            return True
    return False


def _build_obstacles(cfg: dict, rng, rects_out=None) -> list[dict]:
    """Return obstacle dicts plus fill a list of Rects (for inflation checks)."""
    mode = cfg["obstacle_mode"]
    w, h = cfg["width"], cfg["height"]
    obstacles: list[dict] = []
    rects: list = rects_out if rects_out is not None else []
    from common.geometry import Rect

    if mode == "none":
        return [], rects

    if mode == "corridor" or mode == "quadrant":
        # A single vertical wall split by a gap (chokepoint) in the middle.
        gap = 2.2
        wall_h = (h - gap) / 2.0
        wx = w * 0.5
        y0 = (h - gap) / 2.0
        left_wall = Rect(wx - 0.4, 0.0, 0.8, y0)
        right_wall = Rect(wx - 0.4, y0 + gap, 0.8, wall_h)
        obstacles = [
            {"id": "wall_lo", "x": left_wall.x, "y": left_wall.y,
             "width": left_wall.width, "height": left_wall.height, "type": "wall"},
            {"id": "wall_hi", "x": right_wall.x, "y": right_wall.y,
             "width": right_wall.width, "height": right_wall.height, "type": "wall"},
        ]
        rects = [left_wall, right_wall]
        return obstacles, rects

    if mode == "racked":
        # Shelf racks mirroring the shared warehouse builder (dense islands).
        from server.warehouse import build_from_preset
        layout = build_from_preset("ECOMMERCE")
        obstacles = layout.obstacles()
        from common.geometry import Rect
        rects = [Rect(o["x"], o["y"], o["width"], o["height"]) for o in obstacles]
        return obstacles, rects

    # Random boxes sized relative to the warehouse.
    density = cfg["obstacle_density"]
    area = w * h
    min_dim = min(w, h)
    count = int(density * area / 8.0)
    count = int(np.clip(count, 0, 28))
    for i in range(count):
        attempts = 0
        while attempts < 60:
            bw = rng.uniform(0.6, 2.2)
            bh = rng.uniform(0.6, 2.2)
            bx = rng.uniform(1.5, w - 1.5 - bw)
            by = rng.uniform(1.5, h - 1.5 - bh)
            rect = Rect(bx, by, bw, bh)
            if not _rects_vs_point(rects, bx, by, 1.2):
                rects.append(rect)
                obstacles.append({"id": f"o{i}", "x": bx, "y": by,
                                  "width": bw, "height": bh, "type": "obstacle"})
                break
            attempts += 1
    return obstacles, rects


def _spawn_points(cfg: dict, rng, rects) -> list[tuple[float, float]]:
    """Generate n unique non-colliding spawn points per the spawn mode."""
    w, h = cfg["width"], cfg["height"]
    n = cfg["n_robots"]
    mode = cfg["spawn_mode"]
    m = OBSTACLE_MARGIN
    pts: list[tuple[float, float]] = []

    if mode == "edges":
        # Left/right edges, spread vertically.
        for i in range(n):
            side = 0.55 if i % 2 == 0 else w - 0.55
            y = min(h - m, max(m, (h / max(1, n)) * (i // 2 + 1)))
            pts.append((side, y))
    elif mode == "corners":
        corners = [
            (2.0, 2.0), (w - 2.0, 2.0),
            (2.0, h - 2.0), (w - 2.0, h - 2.0),
        ]
        start_idx = rng.randint(0, len(corners))
        for i in range(n):
            pts.append(corners[(start_idx + i) % len(corners)])
    elif mode == "opposite_edges":
        lhs = rng.uniform(0.5 * h, 0.5 * h + 0.5)
        for i in range(n):
            if i % 2 == 0:
                pts.append((max(m, min(w - m, w * 0.5)), max(m, min(h - m, lhs))))
            else:
                ys = lhs + 2.0 if lhs + 2.0 < h - m else lhs - 2.0
                pts.append((max(m, min(w - m, w * 0.5)), ys))
    else:  # random with separation
        attempts_left = 400
        while len(pts) < n and attempts_left > 0:
            attempts_left -= 1
            x = rng.uniform(m, w - m)
            y = rng.uniform(m, h - m)
            if _rects_vs_point(rects, x, y, 0.4):
                continue
            if any(math.hypot(x - px, y - py) < MIN_SPAWN_SEP for px, py in pts):
                continue
            pts.append((x, y))
    # Fallback: scatter remaining deterministically.
    while len(pts) < n:
        x = m + (len(pts) % max(1, int(w))) % int(max(w, 1)) * 2.0
        y = m + (len(pts) // max(1, int(w))) * 2.0 % int(max(h, 1))
        pts.append((min(w - m, max(m, x)), min(h - m, max(m, y))))
    return pts[:n]


def _goal_for(cfg: dict, rng, rects, obst, idx: int, start: tuple,
              planner: AStarPlanner, occupied: list) -> tuple[float, float]:
    """Pick a reachable goal for robot ``idx`` starting at ``start``."""
    w, h = cfg["width"], cfg["height"]
    mode = cfg["goal_mode"]
    m = OBSTACLE_MARGIN
    for _attempt in range(50):
        if mode == "opposite":
            # Far side from start.
            target = (w - start[0], h - start[1]) if rng.rand() < 0.7 else (w - start[0], start[1])
        elif mode == "center":
            target = (w * 0.5, h * 0.5)
        else:
            target = (rng.uniform(m, w - m), rng.uniform(m, h - m))
        gx, gy = min(w - m, max(m, target[0])), min(h - m, max(m, target[1]))
        if math.hypot(gx - start[0], gy - start[1]) < 3.0:
            continue
        if _rects_vs_point(rects, gx, gy, 0.5):
            continue
        if any(math.hypot(gx - ox, gy - oy) < 2.0 for ox, oy in occupied):
            continue
        try:
            planner.plan(start, (gx, gy), obst)
            occupied.append((gx, gy))
            return (gx, gy)
        except PathNotFoundError:
            continue
    # Last resort: straight-line goal at a safe distance.
    ang = (2.0 * math.pi * idx) / max(1, cfg["n_robots"])
    dist = min(w, h) * 0.6
    gx = min(w - m, max(m, start[0] + dist * math.cos(ang)))
    gy = min(h - m, max(m, start[1] + dist * math.sin(ang)))
    return (gx, gy)


def build_scene(cfg: dict, seed: Optional[int] = None,
                planner_resolution: float = 0.25) -> Scene:
    """Deterministically build a ``Scene`` from a scenario config."""
    cfg = _clamp_scenario(cfg)
    seed = int(seed if seed is not None else cfg.get("seed", 0))
    rng = np.random.RandomState(seed)

    obstacles, rects = _build_obstacles(cfg, rng)
    spawns = _spawn_points(cfg, rng, rects)

    planner = AStarPlanner(cfg["width"], cfg["height"],
                           resolution=planner_resolution,
                           robot_radius=0.4, safety_margin=0.1)

    robots: list[RobotSpec] = []
    occupied: list[tuple[float, float]] = []
    n_rl = cfg["n_rl"]
    for idx, (x, y) in enumerate(spawns):
        goal = _goal_for(cfg, rng, rects, obstacles, idx, (x, y), planner, occupied)
        path = []
        try:
            path = planner.plan((x, y), goal, obstacles)
        except PathNotFoundError:
            path = [(x, y), goal]
        robots.append(RobotSpec(
            id=f"AMR{idx + 1}",
            x=x, y=y,
            heading=rng.uniform(0.0, 2 * math.pi),
            max_speed=1.5,
            radius=0.4,
            goal=goal,
            rl=(idx < n_rl),
            path=path,
        ))

    dynamic: list[DynamicObstacle] = []
    if cfg["dynamic_obstacles"]:
        for i in range(min(2, max(0, cfg["n_robots"] - 1))):
            bw, bh = 1.2, 1.2
            bx = rng.uniform(2.0, cfg["width"] - 2.0)
            by = rng.uniform(2.0, cfg["height"] - 2.0)
            vx = rng.choice([-0.4, 0.4])
            vy = rng.choice([-0.25, 0.25])
            span_x = min(6.0, cfg["width"] * 0.3)
            span_y = min(5.0, cfg["height"] * 0.3)
            dynamic.append(DynamicObstacle(
                id=f"dyn{i + 1}", x=bx, y=by, width=bw, height=bh,
                vx=vx, vy=vy,
                min_x=max(1.0, bx - span_x), max_x=min(cfg["width"] - 1.0, bx + span_x),
                min_y=max(1.0, by - span_y), max_y=min(cfg["height"] - 1.0, by + span_y),
            ))

    return Scene(
        width=cfg["width"], height=cfg["height"],
        obstacles=obstacles, dynamic_obstacles=dynamic,
        robots=robots, seed=seed,
        scenario_name=cfg.get("name", "custom"),
        difficulty=cfg["difficulty"],
    )


def evaluate_algorithmic(cfg: dict, seed: Optional[int] = None, *,
                         dt: float = 0.1, max_steps: Optional[int] = None,
                         goal_radius: float = 0.4) -> dict:
    """Run a scenario entirely with the algorithmic controller (baseline).

    Every robot uses the shared ``AlgorithmicNavPipeline`` (A* global paths from
    the existing planner, velocity-obstacle avoidance, chokepoint reservations,
    safety and trajectory limits). Returns per-robot metrics plus an aggregate
    so the frontend can present an unbiased ``Algorithmic vs RL`` comparison.
    """
    from robot.controller import RobotState
    from robot.navigation.pipeline import AlgorithmicNavPipeline

    scene = build_scene(cfg, seed=seed)
    bounds = {"width": scene.width, "height": scene.height}
    obstacles = scene.to_rects()

    states: list[RobotState] = []
    pipelines: list[AlgorithmicNavPipeline] = []
    reached = [False] * len(scene.robots)
    dist0 = []

    for spec in scene.robots:
        st = RobotState(id=spec.id, x=spec.x, y=spec.y, heading=spec.heading,
                        max_speed=spec.max_speed, radius=spec.radius)
        st.current_path = list(spec.path)
        st.path_index = 0
        pipe = AlgorithmicNavPipeline(st, lambda _m: None, resolution=0.25,
                                      safety_margin=0.1)
        pipe.set_goal(spec.goal[0], spec.goal[1])
        states.append(st)
        pipelines.append(pipe)
        dist0.append(math.hypot(spec.goal[0] - spec.x, spec.goal[1] - spec.y))

    max_steps = max_steps or (cfg["max_steps"] + 400)
    now = 0.0
    ep_len = max_steps
    for step in range(max_steps):
        fleet = [st.to_dict() for st in states]
        if all(reached):
            ep_len = step
            break
        for i, (st, pipe) in enumerate(zip(states, pipelines)):
            if reached[i]:
                continue
            pipe.step(dt, obstacles, bounds,
                      [f for f in fleet if f["robotId"] != st.id], now,
                      goal_override=scene.robots[i].goal)
            if math.hypot(st.x - scene.robots[i].goal[0],
                          st.y - scene.robots[i].goal[1]) <= goal_radius:
                reached[i] = True
        now += dt

    per_robot = []
    total_dist = total_wait = 0.0
    collisions = near = 0
    successes = 0
    for i, (st, spec) in enumerate(zip(states, scene.robots)):
        per_robot.append({
            "id": st.id,
            "success": bool(reached[i]),
            "steps": ep_len,
            "time": round(ep_len * dt, 2),
            "distance": round(st.metrics.get("distance", 0.0), 2),
            "collisions": st.metrics.get("collisions", 0),
            "near_collisions": st.metrics.get("near_collisions", 0),
            "waiting_time": round(st.metrics.get("waiting_time", 0.0), 2),
            "interventions": st.metrics.get("interventions", 0),
            "replan_count": st.metrics.get("replan_count", 0),
            "goal_dist": round(math.hypot(st.x - spec.goal[0], st.y - spec.goal[1]), 3),
        })
        successes += int(reached[i])
        total_dist += st.metrics.get("distance", 0.0)
        total_wait += st.metrics.get("waiting_time", 0.0)
        collisions += st.metrics.get("collisions", 0)
        near += st.metrics.get("near_collisions", 0)

    return {
        "controller": "algorithmic",
        "scenario": scene.scenario_name,
        "seed": scene.seed,
        "success": successes == len(scene.robots),
        "successes": successes,
        "n_robots": len(scene.robots),
        "steps": ep_len,
        "time": round(ep_len * dt, 2),
        "distance": round(total_dist / max(1, len(scene.robots)), 2),
        "collisions": int(collisions),
        "near_collisions": int(near),
        "waiting_time": round(total_wait / max(1, len(scene.robots)), 2),
        "per_robot": per_robot,
    }