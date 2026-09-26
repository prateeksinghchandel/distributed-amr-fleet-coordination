"""
test_env.py — Gymnasium environment contract tests.

The env is the single source of truth the UI visualizes, so the obs/action
shapes, reward plumbing, termination rules and determinism are pinned here.
"""

from __future__ import annotations

import numpy as np
import pytest

from rl.env import (ACTION_DIM, AMRCollisionEnv, DT, LIDAR_RAYS, MAX_PEERS,
                    OBS_DIM)
from rl.scenarios import scenario_config


def make_env(scenario: str = "simple", seed: int = 0, safety: str = "guard",
             **overrides) -> AMRCollisionEnv:
    cfg = scenario_config(scenario)
    cfg = {**cfg, **overrides}
    return AMRCollisionEnv(cfg, seed=seed, dt=DT, safety=safety)


# ---------------------------------------------------------------- shapes / bounds

def test_observation_shape() -> None:
    env = make_env()
    obs, _ = env.reset()
    assert env.observation_space.shape == (OBS_DIM,)
    assert obs.shape == (OBS_DIM,)
    assert env.action_space.shape == (ACTION_DIM,)
    assert env.action_space.low.shape == (ACTION_DIM,)
    assert env.action_space.high.shape == (ACTION_DIM,)


def test_observation_is_finite() -> None:
    env = make_env()
    obs, _ = env.reset()
    assert np.all(np.isfinite(obs))


def test_observation_layout_lidar_and_peers() -> None:
    env = make_env()
    obs, _ = env.reset()
    rays = obs[:LIDAR_RAYS]
    assert len(rays) == LIDAR_RAYS
    assert (rays >= 0.0).all() and (rays <= 1.0).all()  # normalized
    assert len(obs) == LIDAR_RAYS + 8 + 4 * MAX_PEERS


def test_action_bounds_and_step_returns() -> None:
    env = make_env()
    env.reset()
    obs, reward, terminated, truncated, info = env.step(np.array([9.0, 9.0]))
    # actions are clamped to [-1, 1]
    assert obs.shape == (OBS_DIM,)
    assert np.isfinite(reward)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert "reward_components" in info
    for key in ("progress", "goal", "collision", "danger", "stopping",
                "path_deviation", "oscillation"):
        assert key in info["reward_components"], key


# ---------------------------------------------------------------- determinism

def test_same_seed_is_deterministic() -> None:
    e1 = make_env(seed=11)
    e2 = make_env(seed=11)
    o1, _ = e1.reset(options={"seed": 11})
    o2, _ = e2.reset(options={"seed": 11})
    assert np.array_equal(o1[:4], o2[:4]) or np.allclose(o1, o2)
    for _ in range(20):
        a = np.array([0.3, 0.1], dtype=np.float32)
        n1, r1, t1, u1, _ = e1.step(a)
        n2, r2, t2, u2, _ = e2.step(a)
        assert np.allclose(n1, n2), "divergence at step"
        assert r1 == r2
        assert t1 == t2
        assert u1 == u2


def test_different_scene_seed_is_different() -> None:
    e1 = make_env(seed=1)
    e2 = make_env(seed=2)
    o1, _ = e1.reset()
    o2, _ = e2.reset()
    assert not np.allclose(o1[:LIDAR_RAYS + 4], o2[:LIDAR_RAYS + 4])


# ---------------------------------------------------------------- termination

def test_reaches_goal_on_greedy_throttle() -> None:
    # 'simple' has no obstacles: straight throttle closes on the goal.
    env = make_env("simple", seed=5, max_steps=300)
    env.reset()
    finished = False
    for _ in range(300):
        obs, r, term, trunc, info = env.step(np.array([0.9, 0.0], dtype=np.float32))
        if term or trunc:
            finished = True
            break
    assert finished
    assert bool(info.get("success")), "should have succeeded reaching the goal"


def test_truncates_at_max_steps() -> None:
    env = make_env("simple", seed=6, max_steps=25)
    obs, _ = env.reset()
    done = False
    steps = 0
    for _ in range(40):
        obs, r, term, trunc, info = env.step(np.array([0.0, 0.0], dtype=np.float32))
        steps += 1
        if trunc:
            done = True
            break
    assert done
    assert steps == 25


def test_collision_terminates() -> None:
    env = make_env("obstacle_avoidance", seed=3, max_steps=200)
    env.reset()
    rect = env.obstacle_rects[0]
    agent = env.rl_agents[0]
    # teleport the robot into the middle of an obstacle -> guaranteed contact
    agent.state.x = rect.x + rect.width / 2
    agent.state.y = rect.y + rect.height / 2
    obs, r, term, trunc, info = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
    assert bool(term), "collision should terminate the episode"
    assert any(a.get("collided") for a in info.get("agents", []))
    assert r <= 0.0 or abs(r) < 1e-6  # collision penalty dominates


# ---------------------------------------------------------------- safety modes

@pytest.mark.parametrize("safety", ["off", "guard", "strict"])
def test_safety_modes_run_without_crash(safety: str) -> None:
    env = make_env("obstacle_avoidance", seed=7, safety=safety, max_steps=50)
    env.reset()
    for _ in range(50):
        obs, r, term, trunc, info = env.step(np.array([0.4, 0.2], dtype=np.float32))
        assert np.all(np.isfinite(obs))
        if term or trunc:
            break


def test_strict_guard_records_overrides() -> None:
    env = make_env("obstacle_avoidance", seed=3, safety="strict", max_steps=80)
    env.reset()
    rect = env.obstacle_rects[0]
    agent = env.rl_agents[0]
    # Place the robot just outside an obstacle, heading straight into it.
    agent.state.x = rect.x - rect.width / 2 - agent.state.radius - 0.1
    agent.state.y = rect.y + rect.height / 2
    import math
    agent.state.heading = math.atan2(rect.y + rect.height / 2 - agent.state.y,
                                     rect.x + rect.width / 2 - agent.state.x)
    overrides_seen = 0
    crashes = 0
    for _ in range(80):
        obs, r, term, trunc, info = env.step(np.array([0.9, 0.0], dtype=np.float32))
        for a in info.get("agents", []):
            if a.get("override", {}).get("overridden"):
                overrides_seen += 1
            if a.get("collided"):
                crashes += 1
        if term:
            break
    # A strict guard overrides the demand heading long before contact.
    assert overrides_seen > 0
    assert crashes == 0


# ---------------------------------------------------------------- multi-robot obs

def test_multi_rl_agents_fill_peer_slots() -> None:
    env = make_env("dense_traffic", seed=9, max_steps=60)
    obs, _ = env.reset()
    # peer region is the last 4 * MAX_PEERS entries
    peer_start = OBS_DIM - 4 * MAX_PEERS
    assert np.any(np.not_equal(obs[peer_start:], 0.0))


# ---------------------------------------------------------------- render_state

def test_render_state_snapshot_contract() -> None:
    env = make_env("simple", seed=10, max_steps=40)
    env.reset()
    for _ in range(5):
        env.step(np.array([0.5, 0.0], dtype=np.float32))
    snap = env.render_state()
    for key in ("step", "time", "max_steps", "dt", "scene", "robots",
                "observation", "action"):
        assert key in snap, key
    assert len(snap["robots"]) == 1
    robot = snap["robots"][0]
    for key in ("id", "x", "y", "heading", "goal", "rl", "reached",
                "collided", "path", "vx", "vy"):
        assert key in robot, key
    assert snap["dt"] == DT


def test_path_is_present_and_reach_target() -> None:
    env = make_env("simple", seed=12, max_steps=40)
    env.reset()
    snap = env.render_state()
    r = snap["robots"][0]
    path = r["path"]
    assert len(path) >= 2
    # path endpoints: starts at robot, ends near the goal
    assert abs(path[0][0] - r["x"]) < 1e-6
    assert abs(path[0][1] - r["y"]) < 1e-6
    assert abs(path[-1][0] - r["goal"]["x"]) < 1.0
    assert abs(path[-1][1] - r["goal"]["y"]) < 1.0