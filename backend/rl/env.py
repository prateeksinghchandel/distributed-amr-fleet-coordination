"""
env.py — Gymnasium environment for AMR local collision avoidance.

Architecture (mirrors the project's algorithmic baseline):

    Task / Goal
        -> Existing Global Path Planner (A*)          desired route
        -> RL collision-avoidance policy              local movement decision
        -> Safety validation layer                    (configurable override)
        -> Robot motion

The environment reuses the existing simulation core directly:
  * ``RobotState``             — robot state/telemetry shape (robot/controller.py)
  * ``AlgorithmicNavPipeline`` — avoidance / chokepoint / deadlock / safety /
                                 trajectory for algorithmic (non-RL) robots
  * ``AStarPlanner``           — global waypoint planning (robot/planning/astar.py)
  * ``common.geometry.Rect``   — obstacle representation
  * ``server.warehouse``       — rack layouts for ``racked`` obstacle mode

The RL robot follows the global path produced by A* (a follow-ahead target
waypoint) and emits a local velocity from the policy action. Everything is
seeded and deterministic — identical seed + actions => identical trajectory.

Observation space (fixed-size vector, float32 in [-1, 1])::

    [0:36]      lidar ray distances / LIDAR_RANGE          (0..1)
    [36]        goal distance / LIDAR_RANGE                (0..1)
    [37:39]     goal direction sin/cos (robot frame)
    [39:41]     ego velocity (vx, vy) / max_speed
    [41]        nearest lidar distance / LIDAR_RANGE        (0..1)
    [42:44]     desired (path) heading sin/cos (robot frame)
    [44:68]     up to MAX_PEERS=6 peer slots [dx, dy, vx, vy] / obs range

Action space (Box(-1,1)^2)::

    [0] throttle  -> linear velocity ~ max_speed   (v = 0..max_speed forward,
                    mild reverse at the low end)
    [1] steer     -> heading offset from the desired path heading, rad*
STEER_LIMIT

Reward components (returned separately in ``info`` for visualization)::

    progress        goal-distance improvement
    goal            reaching the goal region
    collision       static-obstacle / robot-robot contact
    danger          proximity to obstacles or peers
    stopping        standing still while far from the goal
    path_deviation  perpendicular distance from the global path
    oscillation     rapid steering sign flips while moving
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from common.geometry import Rect
from robot.navigation.interfaces import NavContext
from robot.navigation.safety import AlgorithmicSafetyController
from robot.navigation.pipeline import AlgorithmicNavPipeline
from robot.controller import RobotState

from rl.scenarios import build_scene, evaluate_algorithmic  # noqa: F401  (re-export)

try:
    from rl.scenarios import evaluate_algorithmic as _algorithmic_eval
except ImportError:  # pragma: no cover
    _algorithmic_eval = None

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

DT = 0.1                     # control period (s)
LIDAR_RAYS = 36
LIDAR_RANGE = 8.0
MAX_PEERS = 6
PEER_OBS_RANGE = 6.0
STEER_LIMIT = 0.9            # rad ->  ~52 deg off the path heading
MAX_ANGULAR = 1.8           # rad/s
REVERSE_SCALE = 0.3
GOAL_RADIUS = 0.4
ARRIVE_WAYPOINT = 0.25
LOOKAHEAD = 0.6
STUCK_STEPS = 200            # ~20 s without progress -> truncated
OBS_INFLATE = 0.55           # lidar/logic inflation around obstacles (r + margin)
SAFETY_PROJECTION = 0.30     # s ahead for static guard
NEAR_COLLISION_DIST = 0.65   # proximity that counts as a near miss (episode metric)
REWARD_GOAL = 50.0
REWARD_COLLISION_ROBOT = -40.0
REWARD_COLLISION_OBSTACLE = -50.0
REWARD_PROGRESS = 10.0
REWARD_DANGER = 2.0
REWARD_DANGER_DIST = 0.6
REWARD_STOP = 0.06
REWARD_DEVIATION = 0.25
REWARD_OSCILLATION = 0.3

OBS_DIM = LIDAR_RAYS + 1 + 2 + 2 + 1 + 2 + MAX_PEERS * 4
ACTION_DIM = 2


def _norm_angle(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _ray_rect(t0_ox, ox, oy, dx, dy, rect: Rect, inflate: float) -> float:
    """Distance along normalized ray (dx,dy) to an inflated AABB or inf."""
    x0 = rect.x - inflate
    x1 = rect.x + rect.width + inflate
    y0 = rect.y - inflate
    y1 = rect.y + rect.height + inflate
    tmin, tmax = 0.0, math.inf
    if abs(dx) < 1e-12:
        if ox < x0 or ox > x1:
            return math.inf
    else:
        ta = (x0 - ox) / dx
        tb = (x1 - ox) / dx
        tmin, tmax = min(ta, tb), max(ta, tb)
    if abs(dy) < 1e-12:
        if oy < y0 or oy > y1:
            return math.inf
    else:
        tc = (y0 - oy) / dy
        td = (y1 - oy) / dy
        tmin = max(tmin, min(tc, td))
        tmax = min(tmax, max(tc, td))
    if tmin <= tmax and tmax > 0.0:
        return max(0.0, tmin)
    return math.inf


def _ray_circle(ox, oy, dx, dy, cx, cy, r) -> float:
    m = ox - cx
    n = oy - cy
    a = dx * dx + dy * dy
    b = 2.0 * (m * dx + n * dy)
    c = m * m + n * n - r * r
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return math.inf
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    return t if t > 0.0 else math.inf


def _dist_to_segments(x: float, y: float, path: Sequence) -> float:
    if not path:
        return 0.0
    best = math.inf
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        vx, vy = bx - ax, by - ay
        wx, wy = x - ax, y - ay
        L2 = vx * vx + vy * vy
        if L2 <= 1e-12:
            continue
        t = max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
        px, py = ax + t * vx, ay + t * vy
        best = min(best, math.hypot(x - px, y - py))
    return best if math.isfinite(best) else 0.0


class _Agent:
    """Runtime bookkeeping for one robot in the environment."""

    __slots__ = ("state", "rl", "pipeline", "safety_guard", "path", "path_idx",
                 "goal", "reached", "collided", "prev_goal_dist", "prev_steer",
                 "stuck_steps", "last_obs", "last_action", "last_override",
                 "trajectory", "oscillations")

    def __init__(self, spec, rl: bool):
        self.state = RobotState(
            id=spec.id, x=spec.x, y=spec.y, heading=spec.heading,
            max_speed=spec.max_speed, radius=spec.radius,
        )
        self.rl = rl
        self.pipeline = None if rl else AlgorithmicNavPipeline(
            self.state, lambda _m: None, resolution=0.25, safety_margin=0.1)
        self.safety_guard = AlgorithmicSafetyController(self.state, lambda _m: None)
        self.path = list(spec.path)
        self.path_idx = 0
        self.goal = tuple(spec.goal)
        self.reached = False
        self.collided = False
        self.prev_goal_dist = math.hypot(self.goal[0] - spec.x, self.goal[1] - spec.y)
        self.prev_steer = 0.0
        self.stuck_steps = 0
        self.last_obs = None
        self.last_action = np.zeros(2, dtype=np.float32)
        self.last_override = {"overridden": False, "reason": None, "guard": "none"}
        self.trajectory: list[tuple[float, float]] = [(spec.x, spec.y)]
        self.oscillations = 0


class AMRCollisionEnv(gym.Env):
    """Gymnasium environment for learning AMR local collision avoidance."""

    metadata = {"render_modes": ["state", "none"]}
    reward_range = (float("-inf"), float("inf"))

    def __init__(self, scenario: Optional[dict] = None, *, seed: Optional[int] = None,
                 dt: float = DT, safety: str = "guard"):
        super().__init__()
        from rl.scenarios import _clamp_scenario
        self.cfg = _clamp_scenario(scenario or {})
        self.seed_initial = int(seed) if seed is not None else 0
        self.dt = dt
        self.safety_mode = safety          # 'off' | 'guard' (peers) | 'strict'
        self._episode = 0
        self._rng = np.random.RandomState(self.seed_initial)
        self._time = 0.0
        self.step_count = 0
        self.max_steps = self.cfg["max_steps"]
        self._set_spaces()

    def _set_spaces(self) -> None:
        self.action_space = spaces.Box(
            low=np.array([-1.0, -1.0], dtype=np.float32),
            high=np.array([1.0, 1.0], dtype=np.float32),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(OBS_DIM,), dtype=np.float32)

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        if seed is not None:
            self.seed_initial = int(seed)
            self._rng = np.random.RandomState(self.seed_initial)
        scene_seed = None
        if options and "seed" in options:
            scene_seed = int(options["seed"])
        elif seed is not None:
            scene_seed = seed
        if scene_seed is None:
            scene_seed = int(self._rng.randint(0, 2 ** 31 - 1))

        scene = build_scene(self.cfg, seed=scene_seed)
        self.scene = scene
        self.bounds = {"width": scene.width, "height": scene.height}
        self.obstacle_rects = scene.to_rects()
        self._obstacle_dicts = [dict(o) for o in scene.obstacles]
        self._dynamic = scene.dynamic_obstacles

        self.agents: list[_Agent] = [_Agent(spec, rl=spec.rl) for spec in scene.robots]
        for ag in self.agents:
            if not ag.rl:
                ag.pipeline.set_goal(ag.goal[0], ag.goal[1])
                ag.state.current_path = list(ag.path)
                ag.state.path_index = 0

        self.rl_agents = [ag for ag in self.agents if ag.rl]
        self._episode += 1
        self._time = 0.0
        self.step_count = 0
        self.last_info = {}
        self._reset_trackers()

        obs = self._collect_obs()
        info = {"scene": self._scene_meta(), "seed": scene.seed,
                "episode": self._episode}
        self.last_info = dict(info)
        return obs, info

    def step(self, action):
        # ---- Accept single-agent or per-agent action arrays.
        if isinstance(action, (list, tuple)) and len(self.rl_agents) == 1:
            action = np.asarray(action, dtype=np.float32)
        else:
            action = np.asarray(action, dtype=np.float32)
        if action.ndim == 1:
            action = action[None, :]
        if action.shape[0] != len(self.rl_agents):
            raise ValueError(
                f"expected {len(self.rl_agents)} agent action(s), got {action.shape[0]}")

        fleet = self._fleet_snapshot()

        # ---- 1. Advance dynamic obstacles (if enabled).
        self._advance_dynamic(self.dt)

        # ---- 2. Apply RL actions with safety validation.
        for i, ag in enumerate(self.rl_agents):
            self._apply_rl_action(ag, fleet, action[i])

        # ---- 3. Step algorithmic robots through the shared pipeline.
        for ag in self.agents:
            if ag.rl or ag.reached or ag.collided or not ag.state.online:
                continue
            # Re-path if the stored scene path is empty (defensive).
            if ag.state.current_path and ag.path_idx >= len(ag.state.current_path) - 1:
                pass
            ag.pipeline.step(self.dt, self.obstacle_rects, self.bounds, fleet,
                             self._time, goal_override=ag.goal)
            ag.path_idx = ag.state.path_index

        # ---- 4. Resolve contacts / goal arrivals & collect observations.
        self._update_episode_status()
        self._count_near_collisions()
        self._time += self.dt
        self.step_count += 1

        obs = self._collect_obs()
        rewards = np.zeros(len(self.rl_agents), dtype=np.float32)
        comps: dict[str, list] = {}
        terminated = False
        truncated = False

        for i, ag in enumerate(self.rl_agents):
            r, rcomps, term, trunc = self._reward_for(ag, i)
            rewards[i] = r
            for k, v in rcomps.items():
                comps.setdefault(k, []).append(v)
            terminated = terminated or term
            truncated = truncated or trunc

        if len(self.rl_agents) == 1:
            reward = float(rewards[0])
            rcomps_single = {k: v[0] for k, v in comps.items()}
        else:
            reward = rewards
            rcomps_single = comps

        # Episode end conditions.
        if self.step_count >= self.max_steps:
            truncated = True
        all_done = all(a.reached or a.collided for a in self.rl_agents)
        if len(self.rl_agents) > 1 and all_done and not terminated:
            pass

        info = {
            "step": self.step_count,
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "reward": reward,
            "reward_components": rcomps_single,
            "agents": self._agents_report(),
            "collision": bool(any(a.collided for a in self.rl_agents)),
            "near_collisions": self.episode_near_collisions,
            "success": bool(terminated and not any(a.collided for a in self.rl_agents)),
            "scene": self._scene_meta(),
        }
        self.last_info = dict(info)
        return obs, reward, bool(terminated), bool(truncated), info

    def render(self, mode: str = "state"):
        if mode == "state":
            return self.render_state()
        return None

    def seed(self, seed: Optional[int] = None):
        self.seed_initial = int(seed) if seed is not None else self.seed_initial
        self._rng = np.random.RandomState(self.seed_initial)
        return [self.seed_initial]

    # ------------------------------------------------------------------
    # RL robot control + safety
    # ------------------------------------------------------------------

    def _apply_rl_action(self, ag: _Agent, fleet, act) -> None:
        s = ag.state
        throttle = float(np.clip(act[0], -1.0, 1.0))
        steer = float(np.clip(act[1], -1.0, 1.0))
        ag.last_action = np.array([throttle, steer], dtype=np.float32)

        v = throttle * s.max_speed if throttle >= 0 else throttle * REVERSE_SCALE * s.max_speed
        target_heading = self._desired_heading(ag) + steer * STEER_LIMIT

        guard = "none"
        overridden = False
        reason = None
        vx = v * math.cos(s.heading)
        vy = v * math.sin(s.heading)

        # Rotate smoothly toward the target heading (rate-limited).
        dtheta = _norm_angle(target_heading - s.heading)
        s.heading += float(np.clip(dtheta, -MAX_ANGULAR * self.dt, MAX_ANGULAR * self.dt))
        vx = v * math.cos(s.heading)
        vy = v * math.sin(s.heading)

        # -- Safety validation layer ---------------------------------------
        if self.safety_mode != "off":
            ctx = NavContext(s, fleet, self.obstacle_rects, self.bounds)
            guard = "none"
            if self.safety_mode in ("guard", "strict"):
                safe = ag.safety_guard.compute(ctx, vx, vy, self._time)
                if safe["stop"]:
                    guard, overridden, reason = "peer_hard_stop", True, safe["reason"]
                    vx = vy = 0.0
                else:
                    overridden, reason = False, None
            if self.safety_mode == "strict":
                # Static geometry guard: refuse to project into an obstacle.
                if not overridden and self._static_blocked(s, vx, vy):
                    guard, overridden, reason = "static_guard", True, "obstacle_ahead"
                    vx = vy = 0.0

        ag.last_override = {"overridden": bool(overridden), "reason": reason,
                            "guard": guard}
        # -- Integrate -----------------------------------------------------
        nx = s.x + vx * self.dt
        ny = s.y + vy * self.dt
        nx = max(0.0, min(nx, self.bounds["width"]))
        ny = max(0.0, min(ny, self.bounds["height"]))
        if self.safety_mode == "strict" and self._inside_obstacle(nx, ny):
            nx, ny = s.x, s.y
        s.x, s.y = nx, ny
        s.vx, s.vy = vx, vy
        s.speed = math.hypot(vx, vy)
        ag.trajectory.append((s.x, s.y))
        if len(ag.trajectory) > 4000:
            ag.trajectory = ag.trajectory[-2000:]

    @property
    def _obstacles_for_check(self):
        return self.obstacle_rects

    def _inside_obstacle(self, x: float, y: float) -> bool:
        m = OBS_INFLATE - 0.15
        for r in self.obstacle_rects:
            if r.inflated_contains(x, y, m):
                return True
        for r in self._dynamic:
            if r.x - m <= x <= r.x + r.width + m and \
               r.y - m <= y <= r.y + r.height + m:
                return True
        return False

    def _static_blocked(self, s, vx: float, vy: float) -> bool:
        if math.hypot(vx, vy) < 1e-6:
            return False
        px, py = s.x + vx * SAFETY_PROJECTION, s.y + vy * SAFETY_PROJECTION
        return self._inside_obstacle(px, py)

    def _desired_heading(self, ag: _Agent) -> float:
        """Follow-ahead target on the global A* path (existing planner output)."""
        path = ag.path
        x, y = ag.state.x, ag.state.y
        idx = ag.path_idx
        while idx < len(path) - 1 and \
                math.hypot(path[idx][0] - x, path[idx][1] - y) <= ARRIVE_WAYPOINT:
            idx += 1
        ag.path_idx = idx
        target = path[min(idx, len(path) - 1)]
        return math.atan2(target[1] - y, target[0] - x)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def _lidar(self, ag: _Agent) -> tuple[np.ndarray, list]:
        s = ag.state
        ox, oy = s.x, s.y
        rays = np.zeros(LIDAR_RAYS, dtype=np.float32)
        endpoints = []
        for i in range(LIDAR_RAYS):
            ang = s.heading + (2.0 * math.pi * i) / LIDAR_RAYS
            dx, dy = math.cos(ang), math.sin(ang)
            best = math.inf
            for r in self.obstacle_rects:
                t = _ray_rect(0, ox, oy, dx, dy, r, OBS_INFLATE)
                if t < best:
                    best = t
            for dv in self._dynamic:
                rr = Rect(dv.x, dv.y, dv.width, dv.height)
                t = _ray_rect(0, ox, oy, dx, dy, rr, OBS_INFLATE)
                if t < best:
                    best = t
            for peer in self.agents:
                if peer.state.id == ag.state.id:
                    continue
                t = _ray_circle(ox, oy, dx, dy, peer.state.x, peer.state.y,
                                peer.state.radius + 0.15)
                if t < best:
                    best = t
            # world bounds
            tb = math.inf
            if dx < 0:
                tb = min(tb, -ox / dx if dx != 0 else math.inf)
            elif dx > 0:
                tb = min(tb, (self.bounds["width"] - ox) / dx)
            if dy < 0:
                tb = min(tb, -oy / dy if dy != 0 else math.inf)
            elif dy > 0:
                tb = min(tb, (self.bounds["height"] - oy) / dy)
            best = min(best, tb)
            if not math.isfinite(best):
                best = LIDAR_RANGE
            best = max(0.0, min(best, LIDAR_RANGE))
            rays[i] = best / LIDAR_RANGE
            endpoints.append((ox + math.cos(ang) * best,
                              oy + math.sin(ang) * best))
        return rays, endpoints

    def _peers(self, ag: _Agent) -> tuple[list, np.ndarray]:
        s = ag.state
        peers = []
        for p in self.agents:
            if p.state.id == ag.state.id or not p.state.online:
                continue
            peers.append(p)
        peers.sort(key=lambda p: math.hypot(p.state.x - s.x, p.state.y - s.y))
        peers = peers[:MAX_PEERS]
        vec = np.zeros(MAX_PEERS * 4, dtype=np.float32)
        for i, p in enumerate(peers):
            dx = (p.state.x - s.x) / PEER_OBS_RANGE
            dy = (p.state.y - s.y) / PEER_OBS_RANGE
            vx = p.state.vx / max(1e-6, p.state.max_speed)
            vy = p.state.vy / max(1e-6, p.state.max_speed)
            vec[4 * i:4 * i + 4] = (float(np.clip(dx, -1, 1)),
                                    float(np.clip(dy, -1, 1)),
                                    float(np.clip(vx, -1, 1)),
                                    float(np.clip(vy, -1, 1)))
        return peers, vec

    def _obs_for(self, ag: _Agent) -> np.ndarray:
        s = ag.state
        rays, _ = self._lidar(ag)
        goal_dx = ag.goal[0] - s.x
        goal_dy = ag.goal[1] - s.y
        goal_dist = max(0.0, min(math.hypot(goal_dx, goal_dy) / LIDAR_RANGE, 1.0))
        goal_ang = math.atan2(goal_dy, goal_dx) - s.heading
        _, peers = self._peers(ag)
        des = self._desired_heading(ag) - s.heading
        obs = np.concatenate([
            rays,
            [goal_dist],
            [math.sin(goal_ang), math.cos(goal_ang)],
            [s.vx / max(1e-6, s.max_speed), s.vy / max(1e-6, s.max_speed)],
            [float(np.min(rays))],
            [math.sin(des), math.cos(des)],
            peers,
        ]).astype(np.float32)
        ag.last_obs = obs
        return obs

    def _collect_obs(self) -> np.ndarray:
        if len(self.rl_agents) == 1:
            return self._obs_for(self.rl_agents[0])
        return np.stack([self._obs_for(a) for a in self.rl_agents], axis=0)

    # ------------------------------------------------------------------
    # Reward
    # ------------------------------------------------------------------

    def _reward_for(self, ag: _Agent, idx: int):
        s = ag.state
        goal_dist = math.hypot(ag.goal[0] - s.x, ag.goal[1] - s.y)
        comps = {k: 0.0 for k in
                 ("progress", "goal", "collision", "danger", "stopping",
                  "path_deviation", "oscillation")}
        terminated = False
        truncated = False

        if ag.collided:
            comps["collision"] = (REWARD_COLLISION_ROBOT if ag.state.blocked
                                  else REWARD_COLLISION_OBSTACLE)
            terminated = True
            total = sum(comps.values())
            return total, comps, terminated, truncated

        if ag.reached:
            comps["goal"] = REWARD_GOAL
            terminated = True
            total = sum(comps.values())
            return total, comps, terminated, truncated

        # progress
        delta = ag.prev_goal_dist - goal_dist
        comps["progress"] = float(np.clip(REWARD_PROGRESS * delta, -2.0, 2.0))
        ag.prev_goal_dist = goal_dist

        # danger (nearest ray + nearest peer)
        min_ray = float(np.min(ag.last_obs[:LIDAR_RAYS])) * LIDAR_RANGE
        nearest = min_ray
        for p in self.agents:
            if p.state.id == s.id:
                continue
            nearest = min(nearest, math.hypot(p.state.x - s.x, p.state.y - s.y))
        if nearest < REWARD_DANGER_DIST:
            comps["danger"] = -REWARD_DANGER * (REWARD_DANGER_DIST - nearest)

        # stopping
        if s.speed < 0.08 and goal_dist > 0.8:
            comps["stopping"] = -REWARD_STOP

        # path deviation
        dev = _dist_to_segments(s.x, s.y, ag.path)
        comps["path_deviation"] = -REWARD_DEVIATION * min(dev, 4.0)

        # oscillation
        if s.speed > 0.1:
            steer = float(ag.last_action[1])
            if steer * ag.prev_steer < 0:
                comps["oscillation"] = -REWARD_OSCILLATION
                ag.oscillations += 1
            ag.prev_steer = steer
        else:
            ag.prev_steer = float(ag.last_action[1])

        # stuck detection -> truncated
        if goal_dist > 1.0:
            if s.speed < 0.05 and comps["progress"] < 1e-6:
                ag.stuck_steps += 1
            else:
                ag.stuck_steps = 0
            if ag.stuck_steps >= STUCK_STEPS:
                truncated = True

        total = sum(comps.values())
        return float(total), comps, terminated, truncated

    # ------------------------------------------------------------------
    # Episode bookkeeping
    # ------------------------------------------------------------------

    def _update_episode_status(self) -> None:
        for ag in self.agents:
            if ag.reached or ag.collided:
                continue
            s = ag.state
            # static collision (physical penetration)
            hit_static = self._inside_obstacle(s.x, s.y)
            hit_peer = False
            for p in self.agents:
                if p.state.id == s.id:
                    continue
                if math.hypot(p.state.x - s.x, p.state.y - s.y) < \
                        s.radius + p.state.radius:
                    hit_peer = True
                    break
            if hit_static or hit_peer:
                ag.collided = True
                s.blocked = True
                continue
            if math.hypot(ag.goal[0] - s.x, ag.goal[1] - s.y) <= GOAL_RADIUS:
                ag.reached = True
                s.speed = 0.0
                s.vx = s.vy = 0.0
            if not ag.rl:
                pass

    def _reset_trackers(self) -> None:
        for ag in self.agents:
            ag.prev_goal_dist = math.hypot(ag.goal[0] - ag.state.x,
                                           ag.goal[1] - ag.state.y)
            ag.stuck_steps = 0
            ag.prev_steer = 0.0
            ag.trajectory = [(ag.state.x, ag.state.y)]
            ag.last_override = {"overridden": False, "reason": None, "guard": "none"}
        self.episode_near_collisions = 0

    def _count_near_collisions(self) -> None:
        """Count RL-agents near misses (close approach without contact)."""
        for ag in self.rl_agents:
            if ag.collided or ag.reached:
                continue
            s = ag.state
            nearest = float(np.min(ag.last_obs[:LIDAR_RAYS])) * LIDAR_RANGE
            for p in self.agents:
                if p.state.id == s.id or not p.state.online:
                    continue
                nearest = min(nearest, math.hypot(p.state.x - s.x, p.state.y - s.y))
            if nearest < NEAR_COLLISION_DIST and s.speed > 0.05:
                self.episode_near_collisions += 1

    def _advance_dynamic(self, dt: float) -> None:
        for dv in self._dynamic:
            nx = dv.x + dv.vx * dt
            if nx < dv.min_x or nx > dv.max_x - dv.width:
                dv.vx = -dv.vx
                nx = np.clip(nx, dv.min_x, dv.max_x - dv.width)
            ny = dv.y + dv.vy * dt
            if ny < dv.min_y or ny > dv.max_y - dv.height:
                dv.vy = -dv.vy
                ny = np.clip(ny, dv.min_y, dv.max_y - dv.height)
            dv.x, dv.y = float(nx), float(ny)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _fleet_snapshot(self) -> list[dict]:
        return [ag.state.to_dict() for ag in self.agents]

    def _scene_meta(self) -> dict:
        return {
            "name": self.scene.scenario_name,
            "difficulty": self.scene.difficulty,
            "seed": self.scene.seed,
            "width": self.scene.width,
            "height": self.scene.height,
            "n_robots": len(self.agents),
            "n_rl": len(self.rl_agents),
            "dynamic_obstacles": bool(self._dynamic),
        }

    def _agents_report(self) -> list[dict]:
        out = []
        for ag in self.agents:
            s = ag.state
            out.append({
                "id": s.id,
                "rl": ag.rl,
                "x": s.x, "y": s.y,
                "heading": s.heading,
                "vx": s.vx, "vy": s.vy,
                "speed": s.speed,
                "radius": s.radius,
                "reached": ag.reached,
                "collided": ag.collided,
                "goal": {"x": ag.goal[0], "y": ag.goal[1]},
                "goal_dist": math.hypot(ag.goal[0] - s.x, ag.goal[1] - s.y),
                "path": [[float(px), float(py)] for px, py in ag.path],
                "trajectory": [[float(px), float(py)] for px, py in ag.trajectory],
                "nav_state": s.nav_state if ag.rl else (ag.state.nav_state or ""),
                "nav_reason": s.nav_reason if ag.rl else (ag.state.nav_reason or ""),
                "override": ag.last_override,
            })
        return out

    def render_state(self) -> dict:
        """Authoritative full state snapshot forwarded to the visual UI."""
        s0 = self.rl_agents[0]
        rays, endpoints = self._lidar(s0)
        obs = s0.last_obs
        obs_list = obs.tolist() if obs is not None else None
        return {
            "type": "snapshot",
            "step": self.step_count,
            "time": round(self._time, 3),
            "max_steps": self.max_steps,
            "dt": self.dt,
            "scene": self._scene_meta(),
            "bounds": {"width": self.bounds["width"], "height": self.bounds["height"]},
            "obstacles": self._obstacle_dicts,
            "dynamic_obstacles": [
                {"id": d.id, "x": d.x, "y": d.y, "width": d.width, "height": d.height}
                for d in self._dynamic
            ],
            "robots": self._agents_report(),
            "rl_agent": s0.state.id,
            "lidar": {
                "range": LIDAR_RANGE,
                "origin": [s0.state.x, s0.state.y],
                "rays": [{"dx": float(math.cos(s0.state.heading + 2 * math.pi * i / LIDAR_RAYS)),
                          "dy": float(math.sin(s0.state.heading + 2 * math.pi * i / LIDAR_RAYS)),
                          "distance": float(v * LIDAR_RANGE)}
                         for i, v in enumerate(rays)] ,
            },
            "observation": obs_list,
            "action": s0.last_action.tolist(),
            "safety": s0.last_override,
        }