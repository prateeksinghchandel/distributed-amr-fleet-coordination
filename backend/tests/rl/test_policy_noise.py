"""
test_policy_noise.py — action-distribution noise limits and checkpoint fidelity.

Observed failure: a long self-play campaign inflated the free ``log_std``
parameter to +0.31 (std 0.86) on the throttle dim of a Box(-1, 1)^2 action
space, so throttle was near-saturated noise every step. Because the reward
penalises steering sign flips while moving, that noise tripped the oscillation
term almost continuously. ``MAX_ACTION_STD`` is the ceiling that fixes it, and
these tests pin that it holds for freshly built agents *and* for checkpoints
trained before the clamp existed.

Also covers the two checkpoint-fidelity fixes: hyperparameters are not adopted
from the file on load (so a lowered ``ent_coef`` survives a resume), and a
save records the effective config rather than whatever was last read off disk.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from rl.env import ACTION_DIM, OBS_DIM
from rl.rl_policy import (
    INIT_LOG_STD,
    MAX_ACTION_STD,
    PPOAgent,
    PPOConfig,
    load_policy,
)

OBS = np.zeros((2, OBS_DIM), dtype=np.float32)


def _sampled_std(agent: PPOAgent, n: int = 4000) -> float:
    """Empirical std of sampled actions, which the clamp must bound."""
    obs = np.zeros((n, OBS_DIM), dtype=np.float32)
    action, _logp, _value = agent.select_action(obs)
    return float(action.std())


# ------------------------------------------------------------------ the clamp

def test_fresh_agent_starts_at_init_log_std() -> None:
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    expected = float(torch.nn.functional.softplus(
        torch.tensor(INIT_LOG_STD)) + 1e-3)
    assert expected <= MAX_ACTION_STD
    assert agent.summary()["log_std"] == round(expected, 4)


def test_reset_weights_matches_fresh_init() -> None:
    """A cold agent and a reset one must explore identically."""
    fresh = PPOAgent(OBS_DIM, ACTION_DIM)
    reset = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():          # drift it away from init first
        reset.net.log_std.fill_(2.0)
    reset.reset_weights()
    assert torch.allclose(reset.net.log_std,
                          fresh.net.log_std)
    assert reset.summary()["log_std"] == fresh.summary()["log_std"]


def test_std_is_clamped_when_log_std_drifts_high() -> None:
    """The regression: an inflated log_std must not produce saturated noise."""
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.log_std.fill_(0.314)      # the value seen in autosave.pt
    assert agent.effective_std() <= MAX_ACTION_STD
    assert _sampled_std(agent) < 0.5
    # Even a wildly large parameter stays bounded.
    with torch.no_grad():
        agent.net.log_std.fill_(5.0)
    assert agent.effective_std() == pytest.approx(MAX_ACTION_STD, abs=1e-6)
    assert _sampled_std(agent) < 0.5


def test_clamp_holds_after_loading_a_pre_clamp_checkpoint(tmp_path) -> None:
    """A checkpoint trained before the clamp must be corrected on load."""
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.log_std.copy_(torch.tensor([0.314, -1.196]))
    path = str(tmp_path / "pre_clamp.pt")
    agent.save(path, meta={"steps": 918312})

    fresh = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        fresh.net.log_std.fill_(-4.0)        # would be < ceiling on its own
    fresh.load(path)

    assert fresh.effective_std() <= MAX_ACTION_STD
    assert _sampled_std(fresh) < 0.5
    # The raw parameter is still restored as-is; only sampling is bounded.
    assert float(fresh.net.log_std.detach()[0]) == pytest.approx(0.314, abs=1e-6)
    assert fresh.summary()["log_std_raw"] > fresh.summary()["log_std"]


def test_summary_reports_effective_and_raw_std() -> None:
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.log_std.fill_(0.314)
    s = agent.summary()
    assert s["log_std"] <= MAX_ACTION_STD        # what sampling actually uses
    assert s["log_std_raw"] > s["log_std"]        # the unclamped parameter
    assert s["max_action_std"] == MAX_ACTION_STD


def test_dist_std_never_exceeds_ceiling() -> None:
    """Direct check on the distribution object, not just sampled actions."""
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.log_std.fill_(3.0)
    obs = torch.zeros(4, OBS_DIM, device=agent.device)
    std = agent.net.dist(obs).base_dist.scale
    assert torch.all(std <= MAX_ACTION_STD + 1e-6)


def test_nan_log_std_still_degrades_to_finite_default() -> None:
    """The clamp must not swallow the NaN guard that precedes it."""
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.log_std.fill_(float("nan"))
    obs = torch.zeros(4, OBS_DIM, device=agent.device)
    std = agent.net.dist(obs).base_dist.scale
    assert torch.isfinite(std).all()
    action, _logp, _value = agent.select_action(OBS)
    assert np.isfinite(action).all()


# ------------------------------------------------------- checkpoint fidelity

def test_load_does_not_adopt_checkpoint_hyperparameters(tmp_path) -> None:
    """A lowered ent_coef must survive resuming from a checkpoint."""
    source = PPOAgent(OBS_DIM, ACTION_DIM, cfg=PPOConfig(ent_coef=0.01))
    path = str(tmp_path / "old.pt")
    source.save(path)

    resumed = PPOAgent(OBS_DIM, ACTION_DIM, cfg=PPOConfig(ent_coef=0.002))
    data = resumed.load(path)

    assert resumed.ent_coef == 0.002
    assert resumed.cfg.ent_coef == 0.002
    assert data["ppo_on_disk"]["ent_coef"] == 0.01   # still reported


def test_save_records_effective_config_after_load(tmp_path) -> None:
    """A resave must not re-record the value it just read off disk."""
    source = PPOAgent(OBS_DIM, ACTION_DIM, cfg=PPOConfig(ent_coef=0.01))
    first = str(tmp_path / "a.pt")
    source.save(first)

    resumed = PPOAgent(OBS_DIM, ACTION_DIM, cfg=PPOConfig(ent_coef=0.002))
    resumed.load(first)
    second = str(tmp_path / "b.pt")
    resumed.save(second)

    reloaded = torch.load(second, map_location="cpu")
    assert reloaded["ppo"]["ent_coef"] == 0.002


def test_load_preserves_explicit_learning_rate(tmp_path) -> None:
    """load_state_dict replaces the param group, so lr must be re-applied."""
    source = PPOAgent(OBS_DIM, ACTION_DIM, cfg=PPOConfig(lr=3e-4))
    path = str(tmp_path / "lr.pt")
    source.save(path)

    resumed = PPOAgent(OBS_DIM, ACTION_DIM, cfg=PPOConfig(lr=1e-4))
    resumed.load(path)
    assert resumed.lr == 1e-4
    assert all(g["lr"] == 1e-4 for g in resumed.optimizer.param_groups)


def test_load_still_restores_weights_and_optimizer(tmp_path) -> None:
    source = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        source.net.mu.weight.fill_(0.123)
    path = str(tmp_path / "w.pt")
    source.save(path)

    target = PPOAgent(OBS_DIM, ACTION_DIM)
    target.load(path)
    assert torch.allclose(target.net.mu.weight,
                          torch.full_like(target.net.mu.weight, 0.123))
    assert target.updates == source.updates


def test_load_policy_returns_usable_frozen_agent(tmp_path) -> None:
    agent = PPOAgent(OBS_DIM, ACTION_DIM)
    with torch.no_grad():
        agent.net.log_std.fill_(1.5)
    path = str(tmp_path / "frozen.pt")
    agent.save(path)
    frozen = load_policy(path)
    assert frozen.play_deterministic is True
    assert frozen.effective_std() <= MAX_ACTION_STD
