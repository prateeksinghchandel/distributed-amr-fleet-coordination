"""
test_selfplay.py — policy-clone opponents in scenes + LeagueTrainer mechanics.

Covers: opponent peers actually controlled by their frozen policy, legacy
parity when no opponents are configured, pool promote/evict/freeze semantics,
champion-vs-pool evaluation, and a CLI smoke run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import torch

from rl.env import OBS_DIM, ACTION_DIM, AMRCollisionEnv
from rl.league import LeagueTrainer
from rl.rl_policy import PPOAgent
from rl.scenarios import scenario_config
from rl.trainer import RLTrainer


def rand_policy() -> PPOAgent:
    p = PPOAgent(OBS_DIM, ACTION_DIM, device="cpu")
    p.play_deterministic = False   # stochastic play so it actually steers
    return p


def two_robot_with(opponents, n_opponents: int = 1) -> AMRCollisionEnv:
    cfg = scenario_config("two_robot", {"n_opponents": n_opponents})
    return AMRCollisionEnv(cfg, seed=0, dt=0.1, safety="guard",
                           opponents=opponents)


def test_opponent_peer_is_driven_by_its_policy() -> None:
    clone = rand_policy()
    env = two_robot_with([clone])
    env.reset(options={"seed": 3})
    peer = next(ag for ag in env.agents if not ag.rl)
    alg_env = two_robot_with([])
    alg_env.reset(options={"seed": 3})
    alg_peer = next(ag for ag in alg_env.agents if not ag.rl)
    x0, y0 = peer.state.x, peer.state.y
    action = np.zeros((1, ACTION_DIM), dtype=np.float32)  # learner idles
    for _ in range(80):
        obs, *_ = env.step(action)
        alg_env.step(action)
    # The clone peer moved under its own (random) policy ...
    assert (peer.state.x - x0) ** 2 + (peer.state.y - y0) ** 2 > 0.01
    # ... and diverged from the identical-scene algorithmic peer.
    assert (abs(peer.state.x - alg_peer.state.x) +
            abs(peer.state.y - alg_peer.state.y)) > 0.05
    # Learner observation still has the expected shape and peer info.
    assert obs.shape == (OBS_DIM,)
    assert "policy" in peer.__slots__ and peer.policy is clone


def test_no_opponents_keeps_legacy_behavior() -> None:
    env = two_robot_with([])                    # no pool passed at all
    env.reset(options={"seed": 3})
    assert all(ag.policy is None for ag in env.agents)
    cfg = scenario_config("two_robot", {"n_opponents": 1})
    env2 = AMRCollisionEnv(cfg, seed=0, safety="guard",
                           opponents=[])        # pool present but empty
    env2.reset(options={"seed": 3})
    assert all(ag.policy is None for ag in env2.agents)


@pytest.fixture(scope="function")
def league_trainer(tmp_path):
    cfg = scenario_config("simple")
    cfg["max_steps"] = 200
    t = RLTrainer(cfg, n_envs=2, rollout_steps=32, minibatch=8,
                  update_epochs=2, seed=1, speed=1.0,
                  checkpoint_dir=str(tmp_path / "ck"))
    yield t
    t.shutdown()


def test_pool_promote_evicts_oldest_and_freezes(league_trainer, tmp_path) -> None:
    league = LeagueTrainer(league_trainer, pool_size=3)
    for g in (1, 2, 3, 4):
        league.promote(f"gen{g}")
    assert [n for n, _ in league.pool] == ["gen2", "gen3", "gen4"]
    assert league.generations == 4
    assert (tmp_path / "ck" / "champion.pt").exists()
    clone = league.pool[0][1]
    before = {k: v.clone() for k, v in clone.net.state_dict().items()}
    # Let the learner keep training; the frozen clone must not move.
    league_trainer.start()
    deadline = time.time() + 8.0
    while league_trainer.total_steps < 40 and time.time() < deadline:
        time.sleep(0.02)
    league_trainer.pause()
    after = {k: v.clone() for k, v in clone.net.state_dict().items()}
    assert all(torch.equal(before[k], after[k]) for k in before)


def test_champion_vs_pool_reports_per_opponent(league_trainer) -> None:
    league = LeagueTrainer(league_trainer, pool_size=2)
    for g in (1, 2, 3):
        league.promote(f"g{g}")
    vs = league.champion_vs_pool(episodes=2)
    assert len(vs["opponents"]) == 2
    assert {r["opponent"] for r in vs["opponents"]} == {"g2", "g3"}
    for r in vs["opponents"]:
        assert 0.0 <= r["success_rate"] <= 100.0
    assert "avg_success_rate" in vs["summary"]
    assert league.history and league.history[-1]["generation"] == 3


def test_league_run_smoke(tmp_path) -> None:
    cfg = scenario_config("simple")
    cfg["max_steps"] = 200
    t = RLTrainer(cfg, n_envs=2, rollout_steps=32, minibatch=8,
                  update_epochs=2, seed=1, speed=0.0,
                  autosave_every=20, checkpoint_dir=str(tmp_path / "ck"))
    league = LeagueTrainer(t, pool_size=2)
    league.run(steps=150, pool_every=50, report_every=999, vs_pool_episodes=1)
    assert len(league.pool) == 2
    assert league.generations >= 2
    assert (tmp_path / "ck" / "champion.pt").exists()
    assert (tmp_path / "ck" / "autosave.pt").exists()


def test_headless_selfplay_cli_smoke(tmp_path) -> None:
    res = subprocess.run(
        [sys.executable, "-m", "rl.headless",
         "--selfplay", "--scenario", "simple",
         "--n-envs", "2", "--steps", "150",
         "--rollout-steps", "32",
         "--pool-size", "2", "--pool-every", "50",
         "--vs-pool-episodes", "1", "--report-every", "999",
         "--save-every", "20",
         "--checkpoint-dir", str(tmp_path / "ck")],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "ck" / "champion.pt").exists()
    assert (tmp_path / "ck" / "autosave.pt").exists()