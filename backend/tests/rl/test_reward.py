"""
test_reward.py — reward-structure and local-rerouting behaviour tests.

These validate *relative* behaviour, not tuned optimality: collision must
hurt far more than a near-clearance episode; a safe temporary detour around
an obstacle must beat ploughing into it; converging back onto the global path
must be reinforced while holding station off-route must not; AMR proximity
must gate an independent term; idling must be worse than progress; and
oscillatory steering must be penalised. Scenario geometry is injected
deterministically (straight path, axis-aligned barrier on it), so the cases
never depend on scene RNG or scenario tuning.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from rl.env import AMRCollisionEnv, DT, LIDAR_RANGE, NEAR_COLLISION_DIST, \
    OBS_PEERS_START, RewardConfig
from rl.scenarios import scenario_config


def make_env(scenario: str = "simple", seed: int = 0, safety: str = "off",
             reward=None, **overrides) -> AMRCollisionEnv:
    cfg = scenario_config(scenario)
    cfg = {**cfg, **overrides}
    return AMRCollisionEnv(cfg, seed=seed, dt=DT, safety=safety, reward=reward)


# --------------------------------------------------------------------- helpers

def _path_frame(env: AMRCollisionEnv):
    """Straight-path frame for the 'simple' scenario: dir, perp, start."""
    ag = env.rl_agents[0]
    sx, sy = ag.state.x, ag.state.y
    gx, gy = ag.goal
    dx, dy = gx - sx, gy - sy
    L = math.hypot(dx, dy)
    dx, dy = dx / L, dy / L
    return (sx, sy), (dx, dy), (-dy, dx)


def _add_barrier(env: AMRCollisionEnv, centre: tuple, depth: float,
                 span: float, dirv: tuple, perp: tuple):
    """Insert a square barrier centred on the straight path.

    The square is aligned with the world axes and side ``2*span``. A world
    square avoids the inflated bounding box that a rotated AABB would produce
    (the axis-aligned ``Rect`` bbox of rotated corners protrudes back along the
    path and grazes through-robots early). ``depth`` is kept in the signature
    for readability but the world-square construction ignores it.
    """
    cx, cy = centre
    side = 2 * span
    from common.geometry import Rect
    env.obstacle_rects.append(
        Rect(cx - side / 2, cy - side / 2, side, side))
    return env.obstacle_rects[-1]


def _step(env: AMRCollisionEnv, throttle=0.9, steer=0.0):
    return env.step(np.array([throttle, steer], dtype=np.float32))


def _comp(info, key):
    v = info["reward_components"].get(key)
    return float(v[0]) if isinstance(v, (list, tuple)) else float(v or 0.0)


# ------------------------------------------------ Case A: clear-road baseline

def test_case_a_clear_path_has_no_avoidance_penalty() -> None:
    env = make_env("simple", seed=5)
    env.reset(options={"seed": 5})
    penalties = []
    for _ in range(20):
        obs, _r, term, trunc, info = _step(env)
        assert np.all(np.isfinite(obs))
        penalties.append((_comp(info, "near_collision"),
                          _comp(info, "clearance"),
                          _comp(info, "amr_clearance")))
        if term or trunc:
            break
    # No obstacles, single robot => zero avoidance penalties start to finish.
    assert all(nc == 0.0 and cl == 0.0 and amr == 0.0
               for nc, cl, amr in penalties)


# ---------------------- Case B: clearance penalty grows as obstacle closes in

def test_case_b_clearance_penalty_grows_as_barrier_closes() -> None:
    env = make_env("simple", seed=5, max_steps=400)
    env.reset(options={"seed": 5})
    _s, dirv, perp = _path_frame(env)
    cx = (_s[0] + dirv[0] * 6.0, _s[1] + dirv[1] * 6.0)
    _add_barrier(env, cx, 0.4, 3.0, dirv, perp)

    penalties = []
    clearances = []
    term_seen = False
    for _ in range(300):
        _obs, r, term, trunc, info = _step(env)
        ag = env.rl_agents[0]
        penalties.append(_comp(info, "clearance"))
        clearances.append(env._clearances(ag)[0])
        if term or trunc:
            term_seen = True
            break
    # The terminal step does not update the continuous clearance component
    # (its rewards switch to the collision/goal branch), so drop that sample
    # before judging the approach trend.
    if term_seen:
        penalties.pop()
        clearances.pop()
    # The influence zone is the last ~1 m before contact (the penalty only
    # engages inside ``clearance_limit``), so a handful of fresh measures is
    # enough to judge the trend.
    assert len(penalties) >= 5, "robot never approached the barrier"
    # Reasonable head-on approach: penalty magnitude is monotone non-decreasing
    # and clearly larger at the end than at the start.
    mag = [abs(p) for p in penalties]
    assert all(b >= a for a, b in zip(mag, mag[1:]))
    assert mag[-1] > mag[0] + 1.0
    assert clearances[-1] < clearances[0]


def test_case_b_barrier_far_away_parks_nothing() -> None:
    # With the barrier far from the line of travel the clearance term stays 0
    # (deviation framing, not a corridor clamp: no penalty just for being
    # laterally offset while still in open space).
    env = make_env("simple", seed=5, max_steps=300)
    env.reset(options={"seed": 5})
    _s, dirv, perp = _path_frame(env)
    # barrier offset 8 m to one side, well outside the robot's path
    cx = (_s[0] + dirv[0] * 5.0 + perp[0] * 8.0,
          _s[1] + dirv[1] * 5.0 + perp[1] * 8.0)
    _add_barrier(env, cx, 0.4, 1.0, dirv, perp)
    for _ in range(20):
        _obs, _r, _term, _trunc, info = _step(env)
        assert _comp(info, "clearance") == 0.0


# ------------- Case C: safe temporary deviation around a barrier beats plow

def test_case_c_safe_deviation_cheaper_than_ploughing() -> None:
    def plow() -> float:
        env = make_env("simple", seed=5, max_steps=400)
        env.reset(options={"seed": 5})
        _s, dirv, perp = _path_frame(env)
        cx = (_s[0] + dirv[0] * 6.0, _s[1] + dirv[1] * 6.0)
        _add_barrier(env, cx, 0.4, 1.0, dirv, perp)
        total, collided = 0.0, False
        for _ in range(300):
            _, r, term, trunc, info = _step(env)
            total += float(r)
            if any(a.get("collided") for a in info.get("agents", [])):
                collided = True
            if term or trunc:
                break
        return total, collided

    def evade() -> float:
        env = make_env("simple", seed=5, max_steps=400)
        env.reset(options={"seed": 5})
        _s, dirv, perp = _path_frame(env)
        cx = (_s[0] + dirv[0] * 6.0, _s[1] + dirv[1] * 6.0)
        _add_barrier(env, cx, 0.4, 1.0, dirv, perp)
        total, collided = 0.0, False
        # Swing out hard for a short lead-in, then hold the opposite bias long
        # enough that we are already past the barrier plane when we straighten
        # up: a temporary, recoverable detour around it.
        plan = [0.9] * 12 + [-0.9] * 36
        for _ in range(300):
            steer = plan.pop(0) if plan else 0.0
            _, r, term, trunc, info = _step(env, 0.9, steer)
            total += float(r)
            if any(a.get("collided") for a in info.get("agents", [])):
                collided = True
            if term or trunc:
                break
        return total, collided

    t1, hit1 = plow()
    t2, hit2 = evade()
    assert hit1, "control run should have collided with the barrier"
    assert not hit2, "detour run unexpectedly collided"
    assert t2 > t1, f"safe detour ({t2:.1f}) should beat ploughing ({t1:.1f})"


# ------------------------- Case D: collision >> near-clearance >> nothing

def test_case_d_collision_dominates_any_single_step() -> None:
    env = make_env("simple", seed=5, max_steps=400)
    env.reset(options={"seed": 5})
    _s, dirv, perp = _path_frame(env)
    cx = (_s[0] + dirv[0] * 6.0, _s[1] + dirv[1] * 6.0)
    _add_barrier(env, cx, 0.4, 3.0, dirv, perp)
    pre_steps = []
    collision_r = None
    observed_near = observed_clearance = 0.0
    for _ in range(300):
        _, r, term, trunc, info = _step(env)
        observed_near = max(observed_near, -_comp(info, "near_collision"))
        observed_clearance = max(observed_clearance,
                                 -_comp(info, "clearance"))
        if term or trunc:
            collision_r = float(r)
            break
        pre_steps.append(float(r))
    assert collision_r is not None
    worst_pre = min(pre_steps)
    # -40 (or -50) catastrophe dwarfs the worst single near-clearance step,
    # which itself dwarfs a normal step's magnitude.
    assert collision_r < worst_pre
    assert collision_r <= -40.0
    assert worst_pre >= -20.0
    # per-term caps: near_collision <= 4, clearance <= 3 (default weights)
    assert 0.0 < observed_near <= 4.0 + 1e-6
    assert 0.0 < observed_clearance <= 3.0 + 1e-6


# -------------------------------------------- Case E: learnable path return

def test_case_e_converging_back_carries_path_return_bonus() -> None:
    env = make_env("simple", seed=5, max_steps=300)
    env.reset(options={"seed": 5})
    ag = env.rl_agents[0]
    _s, dirv, perp = _path_frame(env)
    # drop the robot 2 m off the path (left), facing back toward it
    off = (_s[0] + dirv[0] * 5.0 + perp[0] * 2.0,
           _s[1] + dirv[1] * 5.0 + perp[1] * 2.0)
    goal_pt = (_s[0] + dirv[0] * 5.0, _s[1] + dirv[1] * 5.0)
    ag.state.x, ag.state.y = off
    ag.state.heading = math.atan2(goal_pt[1] - off[1], goal_pt[0] - off[0])
    ag.prev_cross = 2.0  # as if the robot has been off-route for a while

    return_credit = 0.0
    for _ in range(12):
        _obs, _r, _term, _trunc, info = _step(env)
        return_credit += _comp(info, "path_return")
    assert return_credit > 0.0, "converging back onto the path was not rewarded"


def test_case_e_holding_station_off_path_is_not_rewarded() -> None:
    env = make_env("simple", seed=5, max_steps=300)
    env.reset(options={"seed": 5})
    ag = env.rl_agents[0]
    _s, dirv, perp = _path_frame(env)
    off = (_s[0] + dirv[0] * 10.0 + perp[0] * 2.0,
           _s[1] + dirv[1] * 10.0 + perp[1] * 2.0)
    ag.state.x, ag.state.y = off
    ag.state.heading = math.atan2(perp[1], perp[0])  # travel parallel to path
    ag.prev_cross = 2.0

    return_credit = 0.0
    observed_deviation = 0.0
    for _ in range(6):
        _obs, _r, _term, _trunc, info = _step(env)
        return_credit += _comp(info, "path_return")
        observed_deviation = min(observed_deviation, _comp(info, "path_deviation"))
    assert return_credit == 0.0, "parallel off-route travel must not earn return credit"
    assert observed_deviation < 0.0, "off-route travel must still pay deviation cost"


# ----------------------------- Case F: AMR clearance and peer observation feed

def test_case_f_nearby_amr_activates_amr_clearance_and_peer_slot() -> None:
    env = make_env("two_robot", seed=6, n_rl=2, n_opponents=0, max_steps=60)
    env.reset(options={"seed": 6})
    ag0, ag1 = env.rl_agents[0], env.rl_agents[1]
    obs_far = env._obs_for(ag0).copy()
    amr_far = env._clearances(ag0)[1]
    # bring the peer to within ~1.0 m of ag0 (just above the 0.8 m combined
    # radius, so the ray jack overlaps but they are not yet in contact)
    ag1.state.x = ag0.state.x + 1.0
    ag1.state.y = ag0.state.y
    ag1.state.vx = ag1.state.vy = 0.0
    obs_near = env._obs_for(ag0)
    amr_near = env._clearances(ag0)[1]
    assert amr_near < amr_far
    assert amr_near < LIDAR_RANGE
    assert np.any(obs_near[OBS_PEERS_START:OBS_PEERS_START + 2] !=
                  obs_far[OBS_PEERS_START:OBS_PEERS_START + 2]), \
        "closing peer must move the peer observation slots"

    # one step with both robots inert => AMR term active, worth recording
    info = env.step(np.zeros((2, 2), dtype=np.float32))[-1]
    amr_comp = float(info["reward_components"]["amr_clearance"][0])
    assert amr_comp < 0.0, "proximity to another AMR must incur amr_clearance"


def test_case_f_far_peer_has_no_amr_penalty() -> None:
    env = make_env("simple", seed=6, max_steps=30)
    env.reset(options={"seed": 6})
    for _ in range(5):
        info = _step(env)[-1]
        assert _comp(info, "amr_clearance") == 0.0


# ------------------------------- Case G: unnecessary stopping is penalised

def test_case_g_idling_worse_than_progress() -> None:
    def cumulative(actions):
        env = make_env("simple", seed=7, max_steps=200)
        env.reset(options={"seed": 7})
        total, stopping = 0.0, 0.0
        for a in actions:
            _, r, term, trunc, info = env.step(np.array(a, dtype=np.float32))
            total += float(r)
            stopping += _comp(info, "stopping")
            if term or trunc:
                break
        return total, stopping

    idle_total, idle_stop = cumulative([[0.0, 0.0]] * 15)
    move_total, move_stop = cumulative([[0.9, 0.0]] * 15)
    assert idle_stop < 0.0, "idling while far from the goal must penalise stopping"
    assert move_stop == 0.0
    assert move_total > idle_total, "making progress must outscore standing still"


# -------------------------------------------- Case H: oscillation is penalised

def test_case_h_alternating_steer_incurs_oscillation() -> None:
    def cumulative(steers):
        env = make_env("simple", seed=8, max_steps=200)
        env.reset(options={"seed": 8})
        osc = 0.0
        for s in steers:
            _, _r, term, trunc, info = env.step(
                np.array([0.9, s], dtype=np.float32))
            osc += _comp(info, "oscillation")
            if term or trunc:
                break
        return osc

    alt = cumulative([0.8, -0.8] * 8)
    steady = cumulative([0.3] * 16)
    assert alt < 0.0, "alternating steering while moving must be penalised"
    assert steady == 0.0


# ------------------------------------------------------------ config plumbing

def test_reward_config_defaults_and_roundtrip() -> None:
    cfg = RewardConfig()
    each = cfg.validate().to_dict()
    assert 0.0 <= each["progress"] <= 50.0
    assert each["goal"] == 50.0
    assert each["collision_obstacle"] >= each["collision_robot"]
    assert each["path_return"] > 0.0
    back = RewardConfig.from_dict(each)
    assert back.to_dict() == each
    assert RewardConfig.from_dict(None).to_dict() == RewardConfig().to_dict()


def test_reward_config_partial_override_merges() -> None:
    cfg = RewardConfig.from_dict({"clearance": 7.5, "path_return": 1.2})
    assert cfg.clearance == 7.5
    assert cfg.path_return == 1.2
    assert cfg.progress == RewardConfig().progress  # untouched weight preserved


@pytest.mark.parametrize("bad", [{"clearance": -1}, {"progress": math.nan},
                                 {"amr_clearance_limit": 0}])
def test_reward_config_rejects_invalid(bad) -> None:
    with pytest.raises(ValueError):
        RewardConfig.from_dict(bad)


def test_env_uses_reward_override() -> None:
    env = make_env("simple", reward={"clearance": 5.0})
    assert env.reward.clearance == 5.0
    env2 = make_env("simple")
    assert env2.reward.clearance == 3.0