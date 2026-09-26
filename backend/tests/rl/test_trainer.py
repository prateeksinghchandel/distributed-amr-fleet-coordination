"""
test_trainer.py — trainer state machine, controls, checkpoints, evaluation.

Uses a tiny vector environment (n_envs=2, short rollouts) and the visual
pacing mode (speed=1.0 => one env-step chunk per iteration) so step counts
advance deterministically between control calls.
"""

from __future__ import annotations

import time

import pytest

from rl.scenarios import scenario_config
from rl.trainer import RLTrainer, TrainerState


def small_cfg(**over):
    cfg = scenario_config("simple")
    cfg.update({"max_steps": 200})
    cfg.update(over)
    return cfg


@pytest.fixture(scope="function")
def trainer(tmp_path):
    t = RLTrainer(small_cfg(), n_envs=2, rollout_steps=32, minibatch=8,
                  update_epochs=2, seed=1, speed=1.0,
                  checkpoint_dir=str(tmp_path / "checkpoints"))
    yield t
    t.shutdown()


def wait_until(predicate, timeout=8.0, interval=0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_initial_state_and_snapshot(trainer) -> None:
    assert trainer.state() == TrainerState.IDLE
    assert trainer.last_snapshot is not None
    assert "robots" in trainer.last_snapshot


def test_start_pause_resume(trainer) -> None:
    trainer.start()
    assert wait_until(lambda: trainer.state() == TrainerState.TRAINING)
    assert wait_until(lambda: trainer.total_steps > 0)
    trainer.pause()
    assert wait_until(lambda: trainer.state() == TrainerState.PAUSED)
    s0 = trainer.total_steps
    time.sleep(0.3)
    assert trainer.total_steps == s0, "steps must freeze while paused"
    trainer.resume()
    assert wait_until(lambda: trainer.state() == TrainerState.TRAINING)
    assert wait_until(lambda: trainer.total_steps > s0)


def test_step_advances_exactly(trainer) -> None:
    trainer.start()
    wait_until(lambda: trainer.total_steps > 0)
    trainer.pause()
    wait_until(lambda: trainer.state() == TrainerState.PAUSED)
    s0 = trainer.total_steps
    trainer.step(3)
    assert wait_until(lambda: trainer.total_steps == s0 + 3 * trainer.n_envs)
    assert trainer.state() == TrainerState.PAUSED


def frozen_steps(trainer, window: float = 0.25) -> bool:
    a = trainer.total_steps
    time.sleep(window)
    b = trainer.total_steps
    return a == b


def test_step_episode_runs_to_completion_and_pauses(trainer) -> None:
    trainer.start()
    wait_until(lambda: trainer.total_steps > 0)
    trainer.pause()
    wait_until(lambda: trainer.state() == TrainerState.PAUSED)
    s0 = trainer.total_steps
    trainer.step_episode()
    # episodes finish (envs auto-reset, so watch the step counter advance, then
    # wait until the trainer settles back into a frozen pause — the run-to-end
    # phase completes asynchronously so a fixed sleep is not reliable).
    assert wait_until(lambda: trainer.total_steps > s0)
    assert wait_until(lambda: frozen_steps(trainer), timeout=12), \
        "trainer should freeze in PAUSED after the episode completes"
    assert trainer.state() == TrainerState.PAUSED


def test_reset_episode_rebuilds_env(trainer) -> None:
    trainer.start()
    wait_until(lambda: trainer.total_steps > 20)
    trainer.pause()
    s0 = trainer.total_steps
    env0 = trainer.vec_env.envs[0]
    env0.rl_agents[0].state.x += 0.5  # diverge from a fresh reset
    trainer.reset_episode()
    env1 = trainer.vec_env.envs[0]
    assert env1 is not env0
    assert env1.rl_agents[0].state.x != pytest.approx(
        env0.rl_agents[0].state.x, abs=0.45)
    assert trainer.total_steps == s0  # reset does not touch the step counter


def test_reset_training_zeroes_counters_and_weights(trainer) -> None:
    trainer.start()
    wait_until(lambda: trainer.total_steps > 10)
    trainer.pause()
    assert trainer.total_steps > 0
    trainer.reset_training()
    assert trainer.total_steps == 0
    assert trainer.episode_count == 0
    assert trainer.metrics_brief()["total_steps"] == 0


def test_checkpoint_save_load_never_overwrite_list_delete(trainer, tmp_path) -> None:
    trainer.save_checkpoint("ck_a")
    trainer.save_checkpoint("ck_a")  # collision -> suffix appended
    names = {c["name"] for c in trainer.list_checkpoints()}
    assert {"ck_a.pt", "ck_a_1.pt"} <= names
    res = trainer.load_checkpoint("ck_a.pt")
    assert res["ok"]
    res = trainer.load_checkpoint("nope.pt")
    assert not res["ok"]
    res = trainer.load_checkpoint("../../evil.pt")  # path traversal blocked
    assert not res["ok"]
    res = trainer.delete_checkpoint("ck_a.pt")
    assert res["ok"]
    assert "ck_a.pt" not in {c["name"] for c in trainer.list_checkpoints()}


def test_load_checkpoint_by_absolute_path(trainer, tmp_path) -> None:
    saved = trainer.save_checkpoint("abs_ck")
    res = trainer.load_checkpoint(path=saved["path"])
    assert res["ok"]


def test_autosave_during_training(tmp_path) -> None:
    t = RLTrainer(small_cfg(), n_envs=2, rollout_steps=32, minibatch=8,
                  update_epochs=2, seed=1, speed=1.0, autosave_every=64,
                  checkpoint_dir=str(tmp_path / "ck"))
    autosave = tmp_path / "ck" / "autosave.pt"
    try:
        assert t.status()["autosave_every"] == 64
        assert t.status()["last_autosave"] is None
        t.start()
        # 64 sim steps == one 32-step rollout (n_envs=2); the PPO update that
        # fills the rollout triggers the first autosave.
        assert wait_until(lambda: autosave.exists(), timeout=15), \
            "autosave.pt should appear after ~one rollout"
        first = t.last_autosave
        assert first is not None and first["name"] == "autosave.pt"
        assert first["steps"] >= 64
        # Second rollover overwrites the same file (no suffixed copies).
        n0 = len(t.list_checkpoints())
        assert wait_until(
            lambda: (t.last_autosave or {}).get("steps", 0) > first["steps"],
            timeout=15), "autosave must keep firing on later rollovers"
        assert len(t.list_checkpoints()) == 1, \
            "autosave must overwrite its single rolling file"
    finally:
        t.shutdown()


def test_change_scenario_swaps_env(trainer) -> None:
    trainer.change_scenario("dense_traffic")
    assert trainer.scenario_cfg["name"] == "dense_traffic"
    snap = trainer.last_snapshot
    assert len(snap["robots"]) == trainer.scenario_cfg["n_robots"]
    trainer.change_scenario(level=4)
    assert trainer.scenario_cfg["difficulty"] == 4


def test_set_speed_modes(trainer) -> None:
    trainer.set_speed("max")
    assert trainer.speed == 0.0
    trainer.set_speed(3)
    assert trainer.speed == 3.0
    trainer.set_speed(-1)
    assert trainer.speed == 0.0


def test_evaluate_rl_controller(trainer) -> None:
    trainer.start()
    wait_until(lambda: trainer.total_steps >= 16)
    trainer.pause()
    trainer.evaluate("rl", num_episodes=2)
    assert wait_until(lambda: trainer.state() == TrainerState.PAUSED)
    ev = trainer.last_evaluation
    assert ev is not None
    assert ev["controller"] == "rl"
    assert len(ev["results"]) == 2
    assert set(ev["summary"]) >= {"success_rate", "avg_time", "avg_distance",
                                  "total_collisions"}
    for name in ("steps", "time", "distance", "collisions"):
        assert name in ev["results"][0]


def test_evaluate_algorithmic_controller(trainer) -> None:
    trainer.evaluate("algorithmic", num_episodes=2, seed=7)
    assert wait_until(lambda: trainer.state() == TrainerState.PAUSED)
    ev = trainer.last_evaluation
    assert ev is not None and ev["controller"] == "algorithmic"
    assert len(ev["results"]) == 2


def test_evaluate_rejects_while_running(trainer) -> None:
    trainer.evaluate("algorithmic", num_episodes=1, seed=1)
    res = trainer.evaluate("algorithmic", num_episodes=1)
    assert not res["ok"]


def test_stop_returns_to_idle(trainer) -> None:
    trainer.start()
    wait_until(lambda: trainer.total_steps > 0)
    trainer.stop()
    assert wait_until(lambda: trainer.state() == TrainerState.IDLE)


def test_submit_routes_commands(trainer) -> None:
    res = trainer.submit("start")
    assert res["ok"]
    wait_until(lambda: trainer.total_steps > 0)
    trainer.submit("pause")
    wait_until(lambda: trainer.state() == TrainerState.PAUSED)
    res = trainer.submit("bogus_command")
    assert res.get("queued") == "bogus_command"
    trainer.resume()
    wait_until(lambda: trainer.total_steps > 0)
    trainer.stop()


def test_listeners_get_status_events(trainer) -> None:
    seen: list[tuple[str, dict]] = []
    trainer.attach_listener(lambda k, p: seen.append((k, p)))
    trainer.start()
    wait_until(lambda: any(k == "status" for k, _ in seen))
    assert any(k == "status" for k, _ in seen)
    trainer.evaluate("algorithmic", num_episodes=1)
    wait_until(lambda: any(k == "evaluation" for k, _ in seen))
    trainer.detach_listener(lambda *_: None)  # no-op detach must not raise
    trainer.stop()


def test_metrics_brief_shape(trainer) -> None:
    m = trainer.metrics_brief()
    assert m["type"] == "metrics"
    assert m["total_steps"] == trainer.total_steps
    for key in ("avg_reward", "avg_length", "success_rate", "collision_rate",
                "avg_time", "avg_distance", "components"):
        assert key in m, key


def test_error_state_is_observable(trainer) -> None:
    # Force an exception inside the loop by poisoning the policy.
    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    trainer.agent.select_action = boom
    trainer.start()
    assert wait_until(lambda: trainer.state() == TrainerState.ERROR)
    assert "boom" in (trainer.status().get("error") or "")