"""
test_nan_hardening.py — NaN/±inf resilience of the training stack.

The observed failure mode was a CUDA trainer crash: ``Normal(loc) ... invalid
values: tensor([[nan, nan]])`` — one NaN reaching the policy net permanently
poisoned the weights and training parked in ERROR. These tests pin the layered
defences so a stray non-finite value can degrade gracefully (finite obs, sane
rewards, skipped minibatch, weight recovery) instead of crashing the run.

They run entirely on the main thread (no trainer-loop threads, no cross-thread
torch racing), so real backprops are safe here.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from rl.env import ACTION_DIM, AMRCollisionEnv, OBS_DIM
from rl.rl_policy import PPOAgent, RolloutBuffer
from rl.scenarios import _clamp_scenario, resolve_scenario
from rl.trainer import RLTrainer


def make_env(scenario: str = "simple", seed: int = 0) -> AMRCollisionEnv:
    cfg, _ = resolve_scenario(scenario, None, None)
    cfg["n_robots"] = max(cfg.get("n_rl", 1), 1)
    return AMRCollisionEnv(cfg, seed=seed, safety="guard")


# ---------------------------------------------------------------- env boundary

def test_obs_and_reward_stay_finite_across_scenarios_and_seeds() -> None:
    for name in ("simple", "dense_traffic", "intersection", "random"):
        for seed in range(0, 5):
            env = make_env(name, seed)
            obs, _ = env.reset(options={"seed": seed * 7919 + 7})
            assert np.isfinite(obs).all()
            for _ in range(100):
                act = np.random.uniform(-1.0, 1.0, size=(1, ACTION_DIM))
                obs, reward, term, trunc, _ = env.step(act)
                assert np.isfinite(obs).all(), name
                assert np.isfinite(reward), name
                if term or trunc:
                    obs, _ = env.reset(options={"seed": seed * 7919 + 11})
                    assert np.isfinite(obs).all()


def test_nan_actions_are_sanitized_not_crashing() -> None:
    env = make_env()
    obs0, _ = env.reset()
    obs, reward, term, trunc, _ = env.step(np.array([np.nan, np.inf]))
    assert np.isfinite(obs).all()
    assert np.isfinite(reward)
    assert obs0.shape == obs.shape


# ---------------------------------------------------------------- scenario clamps

def test_clamp_scenario_nan_values_fall_back() -> None:
    cfg = _clamp_scenario({
        "width": float("nan"), "height": float("nan"),
        "n_robots": float("nan"), "n_rl": float("nan"),
        "n_opponents": float("nan"),
        "obstacle_density": float("nan"), "max_steps": float("nan"),
        "difficulty": float("nan"),
    })
    assert cfg["width"] == 30.0
    assert cfg["height"] == 20.0
    assert cfg["n_robots"] == 1
    assert cfg["n_rl"] == 1
    assert cfg["n_opponents"] == 0
    assert cfg["obstacle_density"] == 0.05
    assert cfg["max_steps"] == 600
    assert cfg["difficulty"] == 1
    for v in cfg.values():
        if isinstance(v, (int, float)):
            assert np.isfinite(v), v


def test_clamp_scenario_invalid_strings_fall_back() -> None:
    cfg = _clamp_scenario({"width": "abc", "n_robots": "nan",
                           "obstacle_density": None, "difficulty": ""})
    assert cfg["width"] == 30.0
    assert cfg["n_robots"] == 1
    assert cfg["obstacle_density"] == 0.05
    assert cfg["difficulty"] == 1


# ---------------------------------------------------------------- buffer + update

def test_rollout_push_sanitizes_nonfinite_scalars() -> None:
    buf = RolloutBuffer(OBS_DIM, ACTION_DIM)
    buf.push(np.zeros(OBS_DIM), np.zeros(ACTION_DIM),
             float("nan"), float("inf"), float("-inf"), False)
    assert buf.logp[-1] == 0.0
    assert buf.values[-1] == 0.0
    assert buf.rewards[-1] == 0.0


def test_poisoned_buffer_train_stays_finite() -> None:
    agent = PPOAgent(OBS_DIM, ACTION_DIM, minibatch=8, update_epochs=2)
    buf = RolloutBuffer(OBS_DIM, ACTION_DIM)
    rng = np.random.RandomState(0)
    for i in range(32):
        logp = -0.5 if i % 5 else float("nan")
        reward = 0.1 if i % 3 else float("inf")
        buf.push(rng.rand(OBS_DIM) * 2 - 1, rng.rand(ACTION_DIM) * 2 - 1,
                 logp, float(rng.rand()), reward, False)
    result = agent.train(buf, [0.0])
    assert result["updates"] == 1
    assert np.isfinite(result["policy_loss"])
    assert np.isfinite(result["value_loss"])
    assert np.isfinite(result["entropy"])
    assert result["nan_weights"] is False
    assert all(p.isfinite().all().item() for p in agent.net.parameters())


def test_nan_dist_params_do_not_crash_inference() -> None:
    """Regression for the exact crash: NaN params hitting Normal()'s validator."""
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.mu.weight.fill_(float("nan"))
        agent.net.log_std.fill_(float("nan"))
    obs = np.zeros((2, OBS_DIM), dtype=np.float32)
    action, logp, value = agent.select_action(obs)
    assert np.isfinite(action).all()
    assert np.isfinite(value).all()


# ---------------------------------------------------------------- recovery

def make_trainer(tmp_path) -> RLTrainer:
    cfg, _ = resolve_scenario("simple", None, None)
    cfg.update({"max_steps": 200})
    return RLTrainer(cfg, n_envs=1, rollout_steps=32, minibatch=8,
                     update_epochs=2, seed=1, speed=1.0,
                     checkpoint_dir=str(tmp_path / "ck"))


def test_recover_weights_from_autosave(tmp_path) -> None:
    t = make_trainer(tmp_path)
    t.agent.updates = 7
    t.agent.save(str(t.checkpoint_dir / "autosave.pt"),
                 meta={"steps": 100})
    with torch.no_grad():
        for p in t.agent.net.parameters():
            p.fill_(float("nan"))
    assert not all(p.isfinite().all().item() for p in t.agent.net.parameters())
    source = t._recover_weights()
    assert source == "autosave.pt"
    assert all(p.isfinite().all().item() for p in t.agent.net.parameters())
    assert t.agent.updates == 7


def test_recover_weights_falls_back_to_reset(tmp_path) -> None:
    t = make_trainer(tmp_path)
    assert not any(t.checkpoint_dir.glob("*.pt"))
    with torch.no_grad():
        for p in t.agent.net.parameters():
            p.fill_(float("nan"))
    source = t._recover_weights()
    assert source == "reset_weights"
    assert all(p.isfinite().all().item() for p in t.agent.net.parameters())


def test_status_reports_nan_recoveries(tmp_path) -> None:
    t = make_trainer(tmp_path)
    assert t.status()["nan_recoveries"] == 0
    assert t.agent.summary()["skipped_minibatches"] == 0
    assert t.agent.summary()["weights_finite"] is True