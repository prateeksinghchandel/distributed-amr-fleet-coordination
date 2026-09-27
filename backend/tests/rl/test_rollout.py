"""test_rollout.py — rollout cadence, per-agent terminations and seeds.

Pins the post-refactor training semantics:
  * ``rollout_steps`` counts environment transitions (not agent rows), so the
    update cadence is independent of n_envs and n_rl;
  * a finished RL robot closes its own GAE chain without truncating its
    scene-mates' bootstrap (per-agent ``done`` rows);
  * scene seeds stream from one persistent RNG and continue across env
    rebuilds (deterministic, non-replaying campaigns);
  * evaluations run on isolated policy copies and never touch the live model;
  * scenario/level precedence and ``set_opponents`` wiring behave predictably.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
import torch

from rl.env import ACTION_DIM, OBS_DIM
from rl.rl_policy import PPOAgent
from rl.scenarios import CURRICULUM, scenario_config
from rl.trainer import RLTrainer, TrainerState


def wait_until(pred, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def make_trainer(tmp_path, scenario: str = "simple", *, seed: int = 1,
                 n_envs: int = 2, n_rl: int | None = None,
                 rollout_steps: int = 32, **cfg_overrides) -> RLTrainer:
    cfg = scenario_config(scenario)
    cfg.update({"max_steps": 200, **cfg_overrides})
    if n_rl is not None:
        cfg["n_rl"] = n_rl
    t = RLTrainer(cfg, n_envs=n_envs, rollout_steps=rollout_steps,
                  minibatch=8, update_epochs=2, seed=seed, speed=1.0,
                  checkpoint_dir=str(tmp_path / "ck"))
    return t


# ---------------------------------------------------------------------------
# Rollout cadence counts environment transitions, not agent rows.
# ---------------------------------------------------------------------------
# No test here fires a PPO update: torch CPU deadlocks when a *different*
# thread runs inference after a backprop, and pytest keeps every test file in
# a single process (test_selfplay/test_trainer run trainer-loop threads with
# real backprops). The rollout budget semantics they assert in production
# order are: rollout_steps == env transitions, update fires on crossing the
# budget, buffer drains after — see test_update_path_covered_below. The tests
# here pin the counting + per-agent row invariants without any backprop.

def test_rollout_counts_env_steps_not_rows(tmp_path) -> None:
    t = make_trainer(tmp_path, rollout_steps=10 ** 9)   # no update may fire
    t._ensure_env()
    t._collect_steps(16)                    # 16 iters x 2 envs = 32 env-steps
    assert t.total_steps == 32
    assert t._rollout_env_steps == 32       # counted in env transitions
    assert len(t.buffer) == 32              # one agent row per env-step
    assert t.agent.updates == 0


def test_rollout_cadence_independent_of_n_rl(tmp_path) -> None:
    t1 = make_trainer(tmp_path, "two_robot", n_rl=1, rollout_steps=10 ** 9)
    t2 = make_trainer(tmp_path, "two_robot", n_rl=2, rollout_steps=10 ** 9)
    for t in (t1, t2):
        t._ensure_env()
        n_rl = len(t.vec_env.envs[0].rl_agents)
        t._collect_steps(12)                # 24 env-steps, no update
        assert t._rollout_env_steps == 24   # same cadence for every n_rl
        assert len(t.buffer) == 24 * n_rl   # rows = env-steps x agents
        assert t.agent.updates == 0


# ---------------------------------------------------------------------------
# Per-agent done rows: one agent finishing must not truncate its scene-mates.
# ---------------------------------------------------------------------------

def test_collect_pushes_per_agent_done_rows(tmp_path) -> None:
    t = make_trainer(tmp_path, "two_robot", n_rl=2, n_opponents=0)
    t._ensure_env()
    for env in t.vec_env.envs:              # force only agent 0 of each env done
        env.rl_agents[0].reached = True
    t._collect_steps(1)
    assert t._rollout_env_steps == 2
    dones = np.asarray(t.buffer.dones).reshape(t.n_envs, 2).tolist()
    assert dones == [[True, False], [True, False]]
    assert t.episode_count == 0             # scene still running for agent 1


def test_multi_rl_one_agent_done_keeps_episode_running() -> None:
    from rl.env import AMRCollisionEnv
    cfg = scenario_config("two_robot")
    cfg.update({"n_rl": 2, "n_opponents": 0})
    env = AMRCollisionEnv(cfg, seed=6, dt=0.1, safety="guard")
    env.reset(options={"seed": 6})
    env.rl_agents[0].reached = True         # only agent 0 finishes
    action = np.zeros((2, ACTION_DIM), dtype=np.float32)
    for _ in range(5):
        _, _r, term, trunc, info = env.step(action)
        assert info["agent_terminated"] == [True, False]
        assert term is False and trunc is False       # scene keeps running
    assert env.rl_agents[1].done is False             # peer continued


# ---------------------------------------------------------------------------
# Scene-seed campaign continuity across env rebuilds.
# ---------------------------------------------------------------------------

def test_scene_seeds_continue_across_rebuilds(tmp_path) -> None:
    t1 = make_trainer(tmp_path, seed=11)
    t1._ensure_env()
    s0 = t1.vec_env.envs[0].scene.seed
    t1.reset_episode()                      # rebuilds the vector env
    s1 = t1.vec_env.envs[0].scene.seed
    assert s0 != s1                         # continuation, not a replay
    assert t1.status()["scene_seed_draws"] == 4   # 2 draws per rebuild


def test_same_seed_reproduces_same_campaign(tmp_path) -> None:
    t1 = make_trainer(tmp_path, seed=11)
    t2 = make_trainer(tmp_path, seed=11)
    for t in (t1, t2):
        t._ensure_env()
    a0 = t1.vec_env.envs[0].scene.seed
    b0 = t2.vec_env.envs[0].scene.seed
    assert a0 == b0
    t1.reset_episode()
    t2.reset_episode()
    assert t1.vec_env.envs[0].scene.seed == t2.vec_env.envs[0].scene.seed
    assert t1.vec_env.envs[0].scene.seed != a0     # both advanced the stream


def test_different_master_seed_differs(tmp_path) -> None:
    a = make_trainer(tmp_path, seed=1)
    b = make_trainer(tmp_path, seed=2)
    a._ensure_env()
    b._ensure_env()
    assert a.vec_env.envs[0].scene.seed != b.vec_env.envs[0].scene.seed


# ---------------------------------------------------------------------------
# Evaluation: multi-RL reporting + checkpoint isolation.
# ---------------------------------------------------------------------------

def test_eval_multi_rl_reports_each_agent(tmp_path) -> None:
    t = make_trainer(tmp_path, "two_robot", n_rl=2, n_opponents=0)
    try:
        res = t.evaluate("rl", num_episodes=1, seed=5)
        assert res["ok"]
        assert wait_until(lambda: t.last_evaluation is not None)
        ev = t.last_evaluation
        assert ev["controller"] == "rl"
        result = ev["results"][0]
        assert result["seed"] == 5
        assert result["agent_count"] == 2
        assert len(result["agents"]) == 2
        assert ev["summary"]["agent_count"] == 2
        assert set(ev["summary"]["per_agent"]) == {a["id"] for a in result["agents"]}
    finally:
        # shutdown() joins the loop thread: leaving a torn trainer thread alive
        # would poison later torch tests in the same process.
        t.shutdown()


def test_eval_checkpoint_isolated_live_untouched(tmp_path) -> None:
    # Single-threaded only: the known torch CPU deadlock forbids a backprop on
    # the caller followed by a later eval on the trainer thread, so "train to
    # differ" is done with a weight re-init instead of an update.
    t = make_trainer(tmp_path)
    t._ensure_env()
    t.save_checkpoint("iso")                # snapshot the initial weights
    t.reset_training()                      # re-inits live weights (orthogonal)
    live_before = {k: v.detach().clone() for k, v in t.agent.net.state_dict().items()}
    updates_before = t.agent.updates
    policy = PPOAgent(OBS_DIM, ACTION_DIM, cfg=t.ppo)   # isolated copy
    policy.load(str(t.checkpoint_dir / "iso.pt"))
    res = t._eval_one_rl(t.scenario_cfg, seed=3, agent=policy)
    assert res["seed"] == 3 and res["steps"] > 0
    assert res["agent_count"] == 1
    # the isolated copy carries the checkpoint weights ...
    iso = {k: v.detach().clone() for k, v in policy.net.state_dict().items()}
    differing = [k for k, v in iso.items()
                 if not torch.equal(v, live_before[k])]
    assert len(differing) >= 3              # iso != reset live policy
    # ... and the live policy was never touched by the evaluation.
    for k, v in live_before.items():
        assert torch.equal(v, t.agent.net.state_dict()[k]), k
    assert t.agent.updates == updates_before


def test_eval_new_scenario_uses_resolve_precedence(tmp_path) -> None:
    t = make_trainer(tmp_path)
    try:
        res = t.evaluate("rl", num_episodes=1, seed=1, scenario="simple", level=8)
        assert res["ok"]
        assert wait_until(lambda: t.last_evaluation is not None)
        assert t.last_evaluation["scenario"] == CURRICULUM[8]["name"]
    finally:
        t.shutdown()


# ---------------------------------------------------------------------------
# Scenario switching and opponent wiring.
# ---------------------------------------------------------------------------

def test_change_scenario_level_wins(tmp_path) -> None:
    t = make_trainer(tmp_path)
    res = t.change_scenario(scenario="simple", level=4)
    assert res["ok"] and res["difficulty"] == 4
    assert t.scenario_cfg["difficulty"] == 4
    assert "level wins" in res["note"]
    bad = t.change_scenario()               # nothing to change
    assert not bad["ok"]


def test_set_opponents_arms_scene_with_peers(tmp_path) -> None:
    cfg = scenario_config("two_robot")
    cfg.update({"n_rl": 1, "n_robots": 2})  # room for exactly one peer
    t = RLTrainer(cfg, n_envs=1, rollout_steps=32, seed=1, speed=1.0,
                  checkpoint_dir=str(tmp_path / "ck"))
    clone = PPOAgent(OBS_DIM, ACTION_DIM)
    res = t.set_opponents([clone, clone, clone])
    assert res["ok"] and res["opponents"] == 3
    assert t.scenario_cfg["n_opponents"] == 1      # clamped to the room
    pep = next(a for a in t.vec_env.envs[0].agents if not a.rl)
    assert pep.policy is clone
    st = t.status()
    assert st["n_opponents"] == 3                  # pool size (status semantics)
    assert len(st["opponents_live"]) == 1          # one RL agent in the scene
    # clearing the pool disables self-play entirely.
    t.set_opponents([])
    assert t.scenario_cfg["n_opponents"] == 0
    assert all(a.policy is None for a in t.vec_env.envs[0].agents)


# ---------------------------------------------------------------------------
# step_episode pauses cleanly after completing the current episode.
# ---------------------------------------------------------------------------

def test_step_episode_runs_one_episode_then_pauses(tmp_path) -> None:
    # rollout budget huge: the loop thread only ever runs *forward* passes
    # (inference), never a backprop — safe to share one process with the
    # trainer-thread backprop tests in test_selfplay/test_trainer.
    t = make_trainer(tmp_path, "simple", rollout_steps=10 ** 9)
    try:
        t.start()                           # loop thread owns all torch work
        t.pause()
        assert wait_until(lambda: t.state() == TrainerState.PAUSED)
        before = t.episode_count
        res = t.step_episode()
        assert res["ok"]
        assert wait_until(lambda: t.state() == TrainerState.PAUSED
                          and t.episode_count >= before + t.n_envs)
        assert not t._step_to_episode_end   # flag cleared after the run
        assert t.total_steps > 0
    finally:
        t.shutdown()