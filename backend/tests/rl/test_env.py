"""
test_env.py — Gymnasium environment contract tests.

The env is the single source of truth the UI visualizes, so the obs/action
shapes, reward plumbing, termination rules and determinism are pinned here.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from rl.env import (ACTION_DIM, AMRCollisionEnv, CROSS_TRACK_SCALE, DT,
                    LIDAR_RAYS, LIDAR_RANGE, MAX_PEERS, NEAR_COLLISION_DIST,
                    OBS_DIM, OBS_PATH_FEATS, OBS_PEERS_START, _path_local)
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
    assert len(obs) == LIDAR_RAYS + 8 + OBS_PATH_FEATS + 4 * MAX_PEERS
    assert OBS_PEERS_START == LIDAR_RAYS + 8 + OBS_PATH_FEATS
    # path traits: signed cross-track (v2 feature at +0) + return-bearing sin/cos
    path_feats = obs[44:47]
    assert len(path_feats) == OBS_PATH_FEATS
    assert np.all(np.isfinite(path_feats))
    assert (path_feats >= -1.0).all() and (path_feats <= 1.0).all()
    # peer slots are the last 4*MAX_PEERS entries (tail preserved for v1 tooling)
    assert len(obs[OBS_PEERS_START:]) == 4 * MAX_PEERS


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
    for key in ("progress", "goal", "collision", "near_collision", "clearance",
                "amr_clearance", "path_deviation", "path_return", "stopping",
                "oscillation", "time"):
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
        n1, r1, t1, u1, i1 = e1.step(a)
        n2, r2, t2, u2, i2 = e2.step(a)
        assert np.allclose(n1, n2), "divergence at step"
        assert r1 == r2
        assert t1 == t2
        assert u1 == u2
        for k, v in i1["reward_components"].items():
            v2 = i2["reward_components"][k]
            assert float(v) == float(v2), f"component {k} diverged"


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
                "observation", "action", "action_executed", "safety",
                "path", "clearances", "reward_cfg"):
        assert key in snap, key
    assert snap["dt"] == DT
    assert "cross_track" in snap["path"]
    assert "obstacle" in snap["clearances"] and "amr" in snap["clearances"]
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

def test_per_agent_termination_flags_are_emitted() -> None:
    env = make_env("simple", seed=4, max_steps=120)
    env.reset()
    action = np.zeros((1, ACTION_DIM), dtype=np.float32)
    for _ in range(3):
        _, _r, term, trunc, info = env.step(action)
        assert len(info["agent_terminated"]) == 1
        assert len(info["agent_truncated"]) == 1
        assert term == info["agent_terminated"][0]   # single-RL parity
        assert trunc == info["agent_truncated"][0]


def test_reward_fires_once_then_parks() -> None:
    env = make_env("simple", seed=4, max_steps=120)
    env.reset()
    ag = env.rl_agents[0]
    ag.state.x, ag.state.y = ag.goal          # teleport to the goal
    env._update_episode_status()              # marks reached from position
    action = np.zeros((1, ACTION_DIM), dtype=np.float32)
    r1, comps, term1, _t1 = env._reward_for(ag, 0)
    assert term1 and comps["goal"] > 0
    assert r1 > 0
    r2, comps2, term2, _t2 = env._reward_for(ag, 0)
    assert term2 and r2 == 0.0                # terminal reward never repeats
    assert comps2["goal"] == 0.0 and comps2["collision"] == 0.0


def test_multi_rl_one_agent_done_keeps_episode_running() -> None:
    env = make_env("two_robot", seed=6, max_steps=200, n_rl=2, n_opponents=0)
    env.reset(options={"seed": 6})
    ag0 = env.rl_agents[0]
    ag0.state.x, ag0.state.y = ag0.goal       # only agent 0 finishes
    action = np.zeros((2, ACTION_DIM), dtype=np.float32)
    for _ in range(5):
        _, _r, term, trunc, info = env.step(action)
        assert info["agent_terminated"][0] is True
        assert info["agent_terminated"][1] is False
        assert term is False and trunc is False       # scene still running
        assert not info["success"]
    assert env.rl_agents[1].done is False             # peer kept going


def test_max_steps_truncation_flips_all_agent_flags() -> None:
    env = make_env("simple", seed=2, max_steps=40)
    env.reset()
    action = np.zeros((1, ACTION_DIM), dtype=np.float32)
    last: dict | None = None
    for _ in range(41):           # env clamps max_steps; one step past limit
        last = env.step(action)[-1]
    assert last is not None
    assert last["truncated"] is True or last["terminated"] is True
    assert all(last["agent_truncated"])


# ---------------------------------------------------------- obs path traits

def test_path_traits_signed_cross_track() -> None:
    env = make_env("simple", seed=5)
    env.reset(options={"seed": 5})
    ag = env.rl_agents[0]
    sx, sy = ag.state.x, ag.state.y
    gx, gy = ag.goal
    dx, dy = gx - sx, gy - sy
    L = float(np.hypot(dx, dy))
    dx, dy = dx / L, dy / L
    px, py = -dy, dx                  # left of direction of travel

    on = env._obs_for(ag).copy()
    assert abs(on[44]) < 1e-3, "start is on the path -> cross-track ~ 0"
    # obs[45:47] = sin/cos of the bearing to the nearest path point in the
    # robot's frame; verify directly against the geometry helper.
    _d, near, _sc = _path_local(ag.state.x, ag.state.y, ag.path)
    bearing = math.atan2(near[1] - ag.state.y, near[0] - ag.state.x) - ag.state.heading
    assert on[45] == pytest.approx(math.sin(bearing), abs=1e-3)
    assert on[46] == pytest.approx(math.cos(bearing), abs=1e-3)

    # two metres LEFT of the path, at a point 5 m along
    ag.state.x = sx + dx * 5.0 + px * 2.0
    ag.state.y = sy + dy * 5.0 + py * 2.0
    left = env._obs_for(ag)
    assert left[44] > 0.1 and left[44] <= 1.0
    assert not np.allclose(left[44], on[44])

    # two metres RIGHT => cross-track sign flips
    ag.state.x = sx + dx * 5.0 - px * 2.0
    ag.state.y = sy + dy * 5.0 - py * 2.0
    right = env._obs_for(ag)
    assert right[44] < -0.1
    assert right[44] * left[44] < 0.0, "left/right should have opposite signs"

    # four metres LEFT saturates the scaled cross-track at +/-1
    ag.state.x = sx + dx * 5.0 + px * 4.0
    ag.state.y = sy + dy * 5.0 + py * 4.0
    far = env._obs_for(ag)
    assert far[44] == pytest.approx(1.0)


def test_obs_near_lidar_drops_when_obstacle_approaches() -> None:
    env = make_env("simple", seed=5)
    env.reset(options={"seed": 5})
    ag = env.rl_agents[0]
    base = env._obs_for(ag).copy()
    from common.geometry import Rect
    env.obstacle_rects.append(Rect(ag.state.x + 0.8, ag.state.y - 0.3,
                                   0.4, 0.6))
    near = env._obs_for(ag)
    assert float(np.min(near[:LIDAR_RAYS])) < float(np.min(base[:LIDAR_RAYS]))
    assert near[41] < base[41]           # nearest-lidar slot (index 41)


def test_path_traits_deterministic_across_reinit() -> None:
    e1 = make_env("simple", seed=3)
    e2 = make_env("simple", seed=3)
    o1, _ = e1.reset(options={"seed": 3})
    o2, _ = e2.reset(options={"seed": 3})
    assert np.array_equal(o1[44:47], o2[44:47])
