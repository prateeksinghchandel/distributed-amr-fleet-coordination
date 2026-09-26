"""
trainer.py — Training loop, state machine, metrics and checkpoints.

The trainer owns the PPO loop and the display environment. Training runs in a
dedicated background thread so the aiohttp frontend never blocks; controls
(``start/pause/resume/step/reset_episode/reset_training/save_checkpoint/
load_checkpoint/change_speed/change_scenario/evaluate``) are thread-safe
commands processed by the loop. Pausing is an explicit state transition — the
process is never killed and the model/environment survive.

Explicit trainer states
    IDLE -> TRAINING <-> PAUSED (-> EVALUATING -> PAUSED) -> STOPPING -> IDLE
    any state -> ERROR (reported to the UI)
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from rl.env import ACTION_DIM, AMRCollisionEnv, DT, OBS_DIM
from rl.rl_policy import PPOAgent, PPOConfig, RolloutBuffer, VectorEnv
from rl.scenarios import (CURRICULUM, SCENARIO_PRESETS, SceneSeedGen,
                          _clamp_scenario, evaluate_algorithmic,
                          resolve_scenario, scenario_config,
                          curriculum_config)

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"


class TrainerState(str, Enum):
    IDLE = "IDLE"
    TRAINING = "TRAINING"
    PAUSED = "PAUSED"
    EVALUATING = "EVALUATING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"

    def label(self) -> str:
        return self.value


class RLTrainer:
    """Threaded PPO trainer with a Gymnasium vector environment."""

    def __init__(self, scenario: Optional[dict] = None, *, seed: int = 0,
                 n_envs: int = 8, safety: str = "guard",
                 rollout_steps: int = 512, lr: float = 3e-4,
                 gamma: float = 0.99, lam: float = 0.95, clip: float = 0.2,
                 ent_coef: float = 0.01, val_coef: float = 0.5,
                 update_epochs: int = 4, minibatch: int = 64,
                 hidden: int = 128,
                 device: Optional[str] = None, speed: float = 1.0,
                 fast_snapshot_interval: int = 40,
                 checkpoint_dir: Path | str = CHECKPOINT_DIR,
                 autosave_every: int = 0,
                 eval_seeds: Sequence[int] = (42, 43, 44, 45, 46),
                 opponents: Optional[Sequence] = None):
        self._lock = threading.RLock()
        self._state = TrainerState.IDLE
        self._state_detail = ""
        self._error: Optional[str] = None
        self._commands: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._settled = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.scenario_cfg = _clamp_scenario(scenario or scenario_config("obstacle_avoidance"))
        self.seed = int(seed)
        self.n_envs = int(n_envs)
        self.safety = safety
        # ``rollout_steps`` means environment transitions collected per
        # environment (not per agent row): the PPO update cadence is therefore
        # independent of n_envs and n_rl.
        self.rollout_steps = int(rollout_steps)
        self.fast_snapshot_interval = int(fast_snapshot_interval)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.eval_seeds = list(eval_seeds)
        self.autosave_every = max(0, int(autosave_every))
        self._last_autosave_steps = 0
        self.last_autosave = None
        self.last_checkpoint = None
        self.opponents = list(opponents or [])

        self.speed = float(speed)
        self.speed_steps_per_sec = 10.0   # sim steps per wall second at 1x
        self._last_chunk_time = 0.0

        self.ppo = PPOConfig(lr=lr, gamma=gamma, lam=lam, clip=clip,
                             ent_coef=ent_coef, val_coef=val_coef,
                             update_epochs=update_epochs, minibatch=minibatch,
                             hidden=hidden).validate()
        if device is None:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.agent = PPOAgent(OBS_DIM, ACTION_DIM, cfg=self.ppo, device=device)
        self.vec_env: Optional[VectorEnv] = None
        self.buffer = RolloutBuffer(OBS_DIM, ACTION_DIM)
        self._rollout_env_steps = 0      # env-transitions in the current rollout
        # Persistent campaign scene-seed stream: shared across env rebuilds.
        self._seed_gen = SceneSeedGen(self.seed)
        self.safety_override_count = 0

        self._obs: np.ndarray | None = None
        self.total_steps = 0
        self.episode_count = 0
        self.episode_rewards: deque = deque(maxlen=200)
        self.episode_lengths: deque = deque(maxlen=200)
        self.episode_success: deque = deque(maxlen=200)
        self.episode_times: deque = deque(maxlen=200)
        self.episode_collisions: deque = deque(maxlen=200)
        self.episode_near_misses: deque = deque(maxlen=200)
        self.component_history: deque = deque(maxlen=200)
        self.component_totals: dict = {}
        self.metrics_history: list[dict] = []
        self._ep_tracking: dict = {}
        self._env_ep_finish: list[int] = []   # finished episodes per env
        self.last_snapshot = None
        self.last_evaluation = None

        self._listeners: list[Callable[[str, dict], None]] = []
        self._log_buffer: deque = deque(maxlen=400)

        self._step_request = 0
        self._step_to_episode_end = False
        self._eval_job = None

        self._env_factory = self._make_env_factory()
        self._ensure_env()
        self._emit_snapshot()

    # ------------------------------------------------------------------
    # Public control API (thread-safe)
    # ------------------------------------------------------------------

    def submit(self, command: str, **kwargs) -> dict:
        """Queue a control command; returns an ack dict."""
        if command in ("start",):
            return self.start(**kwargs)
        self._commands.put((command, kwargs or {}))
        self._wake.set()
        return {"queued": command}

    def start(self, **_) -> dict:
        with self._lock:
            if self._state == TrainerState.ERROR:
                self._state = TrainerState.IDLE
                self._error = None
            self._state = TrainerState.TRAINING
            self._state_detail = "training"
        self._ensure_thread()
        self._wake.set()
        self._emit("status", self.status())
        return {"ok": True, "state": self._state.value}

    def pause(self, **_) -> dict:
        with self._lock:
            deets = self._state_detail
            if self._state in (TrainerState.TRAINING, TrainerState.EVALUATING):
                self._state = TrainerState.PAUSED
                self._state_detail = "paused"
        self._wake.set()
        self._emit("status", self.status())
        return {"ok": True, "state": self._state.value, "from": deets}

    def wait_paused(self, timeout: float = 5.0) -> bool:
        """Block until the training loop has fully parked in PAUSED (the
        trainer thread finished any in-flight chunk and drained commands),
        so the caller can safely run torch work without racing it."""
        deadline = time.time() + timeout
        with self._lock:
            if not self._state == TrainerState.PAUSED or self._step_request > 0:
                self._settled.clear()
        while time.time() < deadline:
            with self._lock:
                parked = (
                    self._state == TrainerState.PAUSED
                    and self._step_request == 0
                    and not self._step_to_episode_end
                    and self._commands.empty()
                )
            if parked and self._settled.is_set():
                break
            self._settled.wait(0.05)
        with self._lock:
            return (self._state == TrainerState.PAUSED
                    and self._step_request == 0
                    and not self._step_to_episode_end)

    def run_job(self, fn, timeout: Optional[float] = None):
        """Execute ``fn`` on the trainer's loop thread and block for the result.

        Torch's eager CPU runtime serialises poorly across threads after a
        backprop has started (an idle autograd worker racing torch ops from
        another thread can deadlock). Channeling every heavier torch call —
        policy cloning, eval loops — through this single thread keeps all
        autograd and inference on the ``rl-trainer`` thread. Falls back to
        running inline when the loop thread is not alive.
        """
        if not callable(fn):
            raise TypeError("run_job expects a callable")
        with self._lock:
            thread = self._thread
            alive = thread is not None and thread.is_alive()
        if not alive:
            return fn()
        done = threading.Event()
        box: dict = {}

        def _wrap() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # surfaced to the caller below
                box["error"] = exc
            finally:
                done.set()

        with self._lock:
            self._commands.put(("_exec_job", {"wrap": _wrap}))
        self._wake.set()
        deadline = None if timeout is None else time.time() + timeout
        while True:
            if done.wait(0.2):
                break
            with self._lock:
                err_state = self._state == TrainerState.ERROR
                err_msg = self._state_detail
            if err_state:
                raise RuntimeError(f"trainer ERROR while running job: {err_msg}")
            if deadline is not None and time.time() > deadline:
                raise TimeoutError("trainer job timed out")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _exec_job(self, *, wrap) -> None:
        wrap()

    def resume(self, **_) -> dict:
        with self._lock:
            self._state = TrainerState.TRAINING
            self._state_detail = "training"
        self._ensure_thread()
        self._wake.set()
        self._emit("status", self.status())
        return {"ok": True, "state": self._state.value}

    def step(self, count: int = 1, **_) -> dict:
        """Advance exactly ``count`` environment steps, then stay paused."""
        with self._lock:
            if self._state == TrainerState.EVALUATING:
                return {"ok": False, "error": "cannot step while evaluating"}
            self._state = TrainerState.PAUSED
            self._state_detail = "stepping"
            self._step_request = max(1, int(count))
            self._step_to_episode_end = False
        self._wake.set()
        return {"ok": True, "state": self._state.value,
                "steps": self._step_request}

    def step_episode(self, **_) -> dict:
        """Run the current episode to completion (display env), pausing after."""
        with self._lock:
            if self._state == TrainerState.EVALUATING:
                return {"ok": False, "error": "cannot step while evaluating"}
            self._state = TrainerState.PAUSED
            self._state_detail = "stepping_episode"
            self._env_ep_finish = [0] * self.n_envs   # handshake: non-empty targets
            self._step_request = 0
            self._step_to_episode_end = True
        self._wake.set()
        # Handshake: ensure the per-env episode counters are materialised
        # before the paused thread tries to consume the request (it may be
        # reset to [] by _reset_envs; an empty counter would make the
        # episode-end loop vacuous-return and the freeze would be misread
        # as a lost wake). Re-set so no wake is dropped.
        self._ensure_ep_tracking()
        self._wake.set()
        return {"ok": True, "state": self._state.value}

    def reset_episode(self, **_) -> dict:
        """Reset the environment(s) with fresh seeds; trained model is kept."""
        self._reset_envs()
        self._emit_snapshot()
        return {"ok": True, "state": self._state.value}

    def reset_training(self, **_) -> dict:
        """Reset model weights, optimizer and all counters (keeps scenario)."""
        with self._lock:
            self.agent.reset_weights()
            self._reset_rollout()
            self._rollout_env_steps = 0
            self.total_steps = 0
            self.episode_count = 0
            for d in (self.episode_rewards, self.episode_lengths,
                      self.episode_success, self.episode_times,
                      self.episode_collisions, self.episode_near_misses,
                      self.component_history):
                d.clear()
            self.component_totals = {}
            self.metrics_history.clear()
            self._ep_tracking = {}
            self._last_autosave_steps = 0
            self.last_autosave = None
        self._log("training reset ({})".format(self.agent.summary()))
        self._emit("metrics", self.metrics_brief())
        self._emit("status", self.status())
        return {"ok": True, "state": self._state.value}

    def save_checkpoint(self, name: Optional[str] = None, **_) -> dict:
        """Save a checkpoint; never overwrites an existing file."""
        name = (name or "").strip() or "ppo_amr"
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        path = self.checkpoint_dir / f"{safe}.pt"
        n = 1
        while path.exists():
            path = self.checkpoint_dir / f"{safe}_{n}.pt"
            n += 1
        meta = {
            "scenario": self.scenario_cfg["name"],
            "difficulty": self.scenario_cfg["difficulty"],
            "level": self.scenario_cfg.get("difficulty"),
            "n_envs": self.n_envs,
            "n_rl": self.scenario_cfg.get("n_rl", 1),
            "n_opponents": len(self.opponents)
            if self.scenario_cfg.get("n_opponents", 0) > 0 else 0,
            "steps": int(self.total_steps),
            "episodes": int(self.episode_count),
            "safety": self.safety,
            "seed": int(self.seed),
            "rollout_steps": int(self.rollout_steps),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.agent.save(str(path), meta=meta)
        self.last_checkpoint = {"name": path.name, "steps": int(self.total_steps)}
        self._log(f"checkpoint saved: {path.name} (steps={self.total_steps})")
        self._emit("checkpoint", {"saved": path.name})
        return {"ok": True, "name": path.name, "path": str(path)}

    def load_checkpoint(self, name: Optional[str] = None, path: Optional[str] = None, **_) -> dict:
        """Load a checkpoint by filename (inside the checkpoint dir) or path."""
        if path:
            p = Path(path)
        else:
            name = (name or "").strip()
            if not name:
                return {"ok": False, "error": "checkpoint name required"}
            if "/" in name or ".." in name:
                p = None
            else:
                p = self.checkpoint_dir / name
        if p is None or not p.exists():
            return {"ok": False, "error": f"checkpoint not found: {name}"}
        try:
            self.agent.load(str(p))
        except Exception as exc:
            return {"ok": False, "error": f"load failed: {exc}"}
        self._log(f"checkpoint loaded: {p.name} (updates={self.agent.updates})")
        self._emit("checkpoint", {"loaded": p.name})
        self._emit("status", self.status())
        return {"ok": True, "name": p.name}

    def delete_checkpoint(self, name: Optional[str] = None, **_) -> dict:
        name = (name or "").strip()
        p = self.checkpoint_dir / name if name else None
        if p is None or "/" in name or ".." in name or not p.exists():
            return {"ok": False, "error": f"checkpoint not found: {name}"}
        p.unlink()
        self._log(f"checkpoint deleted: {name}")
        return {"ok": True, "name": name}

    def set_speed(self, speed: Optional[float] = None, **_) -> dict:
        with self._lock:
            if isinstance(speed, str):
                speed = 0.0 if speed.lower() in ("max", "fast") else float(speed or 1.0)
            self.speed = float(speed or 0.0)
            if self.speed < 0:
                self.speed = 0.0
        self._emit("status", self.status())
        return {"ok": True, "speed": self.speed}

    def change_scenario(self, scenario: Optional[str] = None, *,
                        level: Optional[int] = None,
                        overrides: Optional[dict] = None, **_) -> dict:
        """Switch scenario / curriculum level and rebuild the environment.

        When both a named scenario and a curriculum ``level`` are given the
        level explicitly wins (see ``resolve_scenario``) — matching CLI/UI
        precedence so behaviour is identical everywhere.
        """
        try:
            cfg, note = resolve_scenario(scenario, level, overrides)
            if not (scenario or level or overrides):
                return {"ok": False, "error": "no scenario/level provided"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self.scenario_cfg = cfg
        self._reset_rollout()
        self._emit_snapshot()
        self._emit("metrics", self.metrics_brief())
        self._emit("status", self.status())
        self._log(f"scenario changed: {note}")
        return {"ok": True, "scenario": self.scenario_cfg["name"],
                "difficulty": self.scenario_cfg["difficulty"], "note": note}

    def evaluate(self, controller: str = "rl", *, checkpoint: Optional[str] = None,
                 seed: Optional[int] = None, num_episodes: Optional[int] = None,
                 scenario: Optional[str] = None, level: Optional[int] = None,
                 overrides: Optional[dict] = None, **_) -> dict:
        """Run evaluation; state becomes EVALUATING, results stream on finish."""
        with self._lock:
            if self._state == TrainerState.EVALUATING:
                return {"ok": False, "error": "evaluation already running"}
            self._eval_job = {
                "controller": controller,
                "checkpoint": checkpoint,
                "seed": seed,
                "num_episodes": int(num_episodes or len(self.eval_seeds)),
                "scenario": scenario,
                "level": level,
                "overrides": overrides,
            }
            self._state = TrainerState.EVALUATING
            self._state_detail = f"evaluating ({controller})"
        self._ensure_thread()
        self._wake.set()
        self._emit("status", self.status())
        return {"ok": True, "state": self._state.value}

    def stop(self, **_) -> dict:
        with self._lock:
            self._state = TrainerState.STOPPING
            self._state_detail = "stopping"
        self._wake.set()
        self._emit("status", self.status())
        return {"ok": True, "state": self._state.value}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def state(self) -> TrainerState:
        with self._lock:
            return self._state

    def status(self) -> dict:
        with self._lock:
            return {
                "type": "status",
                "state": self._state.value,
                "detail": self._state_detail,
                "error": self._error,
                "scenario": self.scenario_cfg,
                "seed": self.seed,
                "scene_seed_draws": self._seed_gen.draws,
                "safety": self.safety,
                "state_detail": self._state_detail,
                "speed": self.speed,
                "steps_per_second": (self.speed_steps_per_sec if self.speed > 0
                                     else "max"),
                "total_steps": self.total_steps,
                "episodes": self.episode_count,
                "updates": self.agent.updates,
                "env_step": self.vec_env.envs[0].step_count
                if self.vec_env else 0,
                "n_envs": self.n_envs,
                "n_rl": self.scenario_cfg.get("n_rl", 1),
                "n_opponents": len(self.opponents)
                if self.scenario_cfg.get("n_opponents", 0) > 0 else 0,
                "opponents_live": [int(getattr(a, "step_count", 0))
                                   for a in (self.vec_env.envs[0].rl_agents
                                             if self.vec_env else [])],
                "safety_override_count": self.safety_override_count,
                "rollout_steps": self.rollout_steps,
                "rollout_env_steps": self._rollout_env_steps,
                "agent": self.agent.summary(),
                "ppo": self.ppo.to_dict(),
                "checkpoint_dir": str(self.checkpoint_dir),
                "autosave_every": self.autosave_every,
                "last_autosave": self.last_autosave,
                "last_checkpoint": self.last_checkpoint,
            }

    def metrics_brief(self) -> dict:
        with self._lock:
            n = max(1, len(self.episode_rewards))
            rolling = getattr(self, "_rolling_distances", None)
            goal_times = [t for t, ok in zip(self.episode_times,
                                             self.episode_success) if ok]
            return {
                "type": "metrics",
                "total_steps": self.total_steps,
                "episodes": self.episode_count,
                "avg_reward": round(float(np.mean(self.episode_rewards)), 3)
                if self.episode_rewards else 0.0,
                "avg_length": round(float(np.mean(self.episode_lengths)), 2)
                if self.episode_lengths else 0.0,
                "success_rate": round(100.0 * sum(self.episode_success) / n, 2)
                if self.episode_success else 0.0,
                "collision_rate": round(100.0 * sum(self.episode_collisions) / n, 2)
                if self.episode_collisions else 0.0,
                "near_collision_rate":
                    round(100.0 * sum(1 for m in self.episode_near_misses if m > 0) / n, 2)
                    if self.episode_near_misses else 0.0,
                "avg_time": round(float(np.mean(self.episode_times)), 2)
                if self.episode_times else 0.0,
                "avg_goal_time": round(float(np.mean(goal_times)), 2)
                if goal_times else 0.0,
                "avg_distance": round(float(np.mean(rolling)), 2)
                if rolling else 0.0,
                "components": {k: round(float(np.mean([c[k] for c in
                                   self.component_history])), 3)
                               for k in ("progress", "goal", "collision",
                                         "danger", "stopping", "path_deviation",
                                         "oscillation")
                               if self.component_history},
            }

    def list_checkpoints(self) -> list[dict]:
        out = []
        for p in sorted(self.checkpoint_dir.glob("*.pt")):
            out.append({"name": p.name,
                        "path": str(p),
                        "timestamp": p.stat().st_mtime,
                        "bytes": p.stat().st_size})
        out.sort(key=lambda d: d["timestamp"], reverse=True)
        return out

    def attach_listener(self, cb: Callable[[str, dict], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def detach_listener(self, cb: Callable[[str, dict], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_env_factory(self):
        def factory(seed: int) -> AMRCollisionEnv:
            _ = seed  # scene seed now comes from _seed_gen; kept for compat
            return AMRCollisionEnv(self.scenario_cfg, dt=DT,
                                   safety=self.safety,
                                   opponents=self.opponents
                                   if self.scenario_cfg.get("n_opponents", 0) > 0
                                   else None)
        return factory

    def set_opponents(self, opponents: Optional[Sequence]) -> dict:
        """Replace the frozen opponent pool and rebuild the environments.

        Clears the pool when ``opponents`` is empty or None (disables
        self-play in the next env rebuild) — this matches the league's pool
        lifecycle, where passing no agents means "no opponents from now on".
        """
        with self._lock:
            self.opponents = list(opponents or [])
        self._reset_envs()
        self._emit("status", self.status())
        return {"ok": True, "opponents": len(self.opponents)}

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._running = True
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="rl-trainer")
                self._thread.start()

    def _ensure_env(self) -> None:
        if self.vec_env is None:
            self.vec_env = VectorEnv(self._env_factory, self.n_envs,
                                     base_seed=self.seed,
                                     seed_source=self._seed_gen.next)
            self._obs = self.vec_env.reset_all()
            self._reset_rollout()

    def _reset_envs(self) -> None:
        # base_seed stays constant: VectorEnv's scene seeds now come from the
        # persistent _seed_gen, so rebuilds continue the scene campaign instead
        # of replaying (`base_seed + episode_count` did exactly that).
        if self.vec_env is not None:
            self.vec_env.close()
        self.vec_env = VectorEnv(self._env_factory, self.n_envs,
                                 base_seed=self.seed,
                                 seed_source=self._seed_gen.next)
        self._env_ep_finish = [0] * self.n_envs
        self._obs = self.vec_env.reset_all()
        self._reset_rollout()
        self._ensure_ep_tracking()

    def _reset_rollout(self) -> None:
        self.buffer.reset()
        self._rollout_env_steps = 0

    def _ensure_ep_tracking(self) -> None:
        for i in range(self.n_envs):
            if i not in self._ep_tracking:
                self._ep_tracking[i] = {"reward": 0.0, "length": 0,
                                        "success": False, "collision": False,
                                        "seed": self.scenario_cfg.get("seed", 0),
                                        "components": {
                                            k: 0.0 for k in
                                            ("progress", "goal", "collision",
                                             "danger", "stopping",
                                             "path_deviation", "oscillation")}}

    def _run(self) -> None:
        self._log(f"trainer thread started (n_envs={self.n_envs}, "
                  f"rollout={self.rollout_steps})")
        try:
            self._run_loop()
        except Exception as exc:
            self._transition_error(f"trainer crashed: {exc}")
            try:
                import traceback
                self._log(traceback.format_exc())
            except Exception:
                pass

    def _run_loop(self) -> None:
        while self._running:
            with self._lock:
                st = self._state
            if st in (TrainerState.IDLE, TrainerState.STOPPING):
                self._wake.wait(0.2)
                self._drain_commands()
                if st == TrainerState.STOPPING:
                    # Stop processed: settle back into IDLE (thread stays alive).
                    with self._lock:
                        if self._state == TrainerState.STOPPING:
                            self._state = TrainerState.IDLE
                            self._state_detail = ""
                    self._emit("status", self.status())
                continue
            if st == TrainerState.ERROR:
                self._wake.wait(0.3)
                continue
            if st == TrainerState.PAUSED:
                with self._lock:
                    to_ep_end = self._step_to_episode_end
                    steps = self._step_request
                if to_ep_end:
                    with self._lock:
                        self._step_to_episode_end = False
                    self._run_to_episode_end()
                    self._emit_snapshot()
                    self._emit("step", self._step_info())
                    with self._lock:
                        self._state = TrainerState.PAUSED
                        self._state_detail = "paused"
                    self._emit("status", self.status())
                elif steps > 0:
                    with self._lock:
                        self._step_request = 0
                    self._collect_steps(steps)
                    self._emit_snapshot()
                    self._emit("step", self._step_info())
                    with self._lock:
                        self._state = TrainerState.PAUSED
                        self._state_detail = "paused"
                    self._emit("status", self.status())
                else:
                    self._wake.wait(0.2)
                    self._drain_commands()
                    self._settled.set()
                continue
            if st == TrainerState.EVALUATING:
                self._run_eval_step()
                continue
            if st == TrainerState.TRAINING:
                if self._drain_commands():
                    continue
                self._run_training_iteration()
                continue

    def _drain_commands(self) -> bool:
        """Process queued commands; return True if state may have changed."""
        changed = False
        while True:
            try:
                command, kwargs = self._commands.get_nowait()
            except queue.Empty:
                break
            handler = getattr(self, command, None)
            if not callable(handler):
                self._log(f"unknown command: {command}")
                continue
            try:
                handler(**kwargs)
                changed = True
            except Exception as exc:
                self._transition_error(f"{command}: {exc}")
        return changed

    def _transition_error(self, message: str) -> None:
        with self._lock:
            self._state = TrainerState.ERROR
            self._state_detail = "error"
            self._error = message
        self._log(f"ERROR: {message}")
        self._emit("status", self.status())
        self._emit("error", {"message": message})

    def _as_agent_obs(self, obs):
        """Normalise vectorised obs to ``(n_envs, n_rl, obs_dim)``.

        Single-agent environments historically carried per-env obs of shape
        ``(obs_dim,)`` (stacked to ``(n_envs, obs_dim)``); multi-RL scenes
        stack per-agent rows ``(n_rl, obs_dim)`` per env. This helper makes
        both flow through the same per-agent loop below.
        """
        obs = np.asarray(obs, dtype=np.float32)
        if obs.ndim == 2:
            return obs[:, None, :]
        return obs

    def _collect_steps(self, count: int) -> None:
        if self.vec_env is None or self._obs is None:
            self._ensure_env()
        obs = self._as_agent_obs(self._obs)
        n_rl = obs.shape[1]
        for _ in range(count):
            actions = np.zeros((self.n_envs, n_rl, ACTION_DIM),
                               dtype=np.float32)
            logps = np.zeros((self.n_envs, n_rl), dtype=np.float32)
            values = np.zeros((self.n_envs, n_rl), dtype=np.float32)
            for i in range(self.n_envs):
                a, lp, v = self.agent.select_action(obs[i])
                actions[i] = a
                logps[i] = lp
                values[i] = v
            next_obs, rewards, infos = self.vec_env.step(actions)
            rewards = np.asarray(rewards, dtype=np.float32).reshape(
                self.n_envs, n_rl)
            next_obs_a = self._as_agent_obs(next_obs)
            last_values = np.concatenate([
                self.agent.value_of(next_obs_a[i]) for i in range(self.n_envs)])
            for i in range(self.n_envs):
                info = infos[i]
                terms = info.get("agent_terminated") or []
                truncs = info.get("agent_truncated") or []
                # Per-agent termination: each RL robot closes its own GAE chain
                # on its own goal/collision/truncation; in shared multi-RL
                # scenes one agent finishing must never truncate (zero out) the
                # bootstrap of an agent that is still driving.
                for k in range(n_rl):
                    done = bool(terms[k]) if k < len(terms) else \
                        bool(info.get("terminated"))
                    done = done or (bool(truncs[k]) if k < len(truncs)
                                    else bool(info.get("truncated")))
                    self.buffer.push(obs[i][k], actions[i][k],
                                     float(logps[i][k]), float(values[i][k]),
                                     float(rewards[i][k]), done)
                self._update_ep_tracking(i, float(np.sum(rewards[i])), info)
                for a in info.get("agents", []):
                    if a.get("override", {}).get("overridden"):
                        self.safety_override_count += 1
            self._rollout_env_steps += self.n_envs
            # batch unroll / update
            self._maybe_update(last_values)
            obs = next_obs_a
            self._obs = next_obs
            self.total_steps += self.n_envs

    def _update_ep_tracking(self, i: int, reward: float, info: dict) -> None:
        self._ensure_ep_tracking()
        tr = self._ep_tracking[i]
        tr["reward"] += float(reward)
        tr["length"] += 1
        for k, vals in (info.get("reward_components") or {}).items():
            if isinstance(vals, (int, float)):
                v = float(vals)
            elif isinstance(vals, (list, tuple)) and len(vals):
                v = float(np.mean([float(x) for x in vals]))
            else:
                v = 0.0
            tr["components"][k] = tr["components"].get(k, 0.0) + float(v)
        if info.get("terminated") or info.get("truncated"):
            self._finish_episode(i, info)

    def _finish_episode(self, i: int, info: dict) -> None:
        tr = self._ep_tracking.pop(i, None)
        if tr is None:
            return
        if i < len(self._env_ep_finish):
            self._env_ep_finish[i] += 1
        success = bool(info.get("success"))
        collision = bool(info.get("collision")) or any(
            a.get("collided") for a in info.get("agents", []))
        self.episode_count += 1
        self.episode_rewards.append(tr["reward"])
        self.episode_lengths.append(tr["length"])
        self.episode_success.append(success)
        self.episode_times.append(round(tr["length"] * DT, 2))
        self.episode_collisions.append(collision)
        self.episode_near_misses.append(int(info.get("near_collisions", 0)))
        self.component_history.append(dict(tr["components"]))
        self._rolling_distances = getattr(self, "_rolling_distances", deque(maxlen=200))
        self._rolling_distances.append(round(sum(
            math.hypot(a.get("vx", 0), a.get("vy", 0)) for a in
            info.get("agents", []) if a.get("rl")) * DT, 2))
        self._emit("episode", {
            "type": "episode",
            "episode": self.episode_count,
            "total_steps": self.total_steps,
            "reward": round(tr["reward"], 3),
            "length": tr["length"],
            "success": success,
            "collision": collision,
            "near_collisions": int(info.get("near_collisions", 0)),
            "time": round(tr["length"] * DT, 2),
            "seed": info.get("scene", {}).get("seed"),
            "scenario": info.get("scene", {}).get("name"),
            "components": {k: round(v, 3) for k, v in tr["components"].items()},
        })
        if self.episode_count % 5 == 0:
            self._emit("metrics", self.metrics_brief())

    def _maybe_update(self, last_values) -> None:
        # Rollout length is counted in environment transitions, not agent rows:
        # the update cadence is independent of n_envs and n_rl.
        if self._rollout_env_steps >= self.rollout_steps:
            result = self.agent.train(self.buffer, last_values)
            self._reset_rollout()
            self.metrics_history.append({
                "total_steps": self.total_steps,
                "episodes": self.episode_count,
                **result,
                **{k: v for k, v in self.metrics_brief().items()
                   if k in ("avg_reward", "success_rate", "collision_rate",
                            "avg_length", "avg_time")},
                "components": {k: round(float(np.mean([
                    c[k] for c in self.component_history])), 3)
                    for k in ("progress", "goal", "collision", "danger",
                              "stopping", "path_deviation", "oscillation")
                    if self.component_history},
            })
            self.metrics_history = self.metrics_history[-500:]
            self._emit("metrics", self.metrics_brief())
            self._emit("update", result)
            self._maybe_autosave()

    def _maybe_autosave(self) -> None:
        """Persist the policy to a rolling ``autosave.pt`` during training.

        Mirror of the headless runner's periodic snapshots: saves when the
        sim-step counter has advanced ``autosave_every`` since the last save,
        evaluated right after a PPO update so the file matches fresh weights.
        Unlike ``save_checkpoint`` (which never overwrites and accumulates
        suffixed copies) this overwrites the single ``autosave.pt``, so the
        checkpoints list stays clean. Enabled only when ``autosave_every > 0``;
        failures are logged and never disturb the training loop.
        """
        if self.autosave_every <= 0:
            return
        if self.total_steps - self._last_autosave_steps < self.autosave_every:
            return
        try:
            path = self.checkpoint_dir / "autosave.pt"
            meta = {
                "scenario": self.scenario_cfg["name"],
                "difficulty": self.scenario_cfg["difficulty"],
                "steps": int(self.total_steps),
                "episodes": int(self.episode_count),
                "safety": self.safety,
                "seed": int(self.seed),
                "rollout_steps": int(self.rollout_steps),
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.agent.save(str(path), meta=meta)
            self._last_autosave_steps = self.total_steps
            self.last_autosave = {
                "name": "autosave.pt",
                "steps": self.total_steps,
                "time": time.time(),
            }
            self.last_checkpoint = dict(self.last_autosave)
            self._log(f"autosaved {path.name} (steps={self.total_steps}, "
                      f"updates={self.agent.updates})")
            self._emit("checkpoint", {"autosaved": path.name,
                                      "steps": self.total_steps})
        except Exception as exc:
            self._log(f"autosave failed: {exc}")

    def _run_training_iteration(self) -> None:
        """Run one pace-limited chunk of env steps; snapshot in visual mode."""
        speed = self.speed
        if speed > 0:
            # Cap chunk so the UI advances ~ one wall-clock second per second.
            chunk = max(1, int(round(speed)))
            steps_per_wall_sec = self.speed_steps_per_sec * speed
            t0 = time.perf_counter()
            self._collect_steps(chunk)
            elapsed = time.perf_counter() - t0
            # wall time we should spend for `chunk` steps at target rate
            target = max(0.0, chunk / max(1e-3, steps_per_wall_sec))
            if self._state == TrainerState.TRAINING and elapsed < target:
                time.sleep(target - elapsed)
            self._emit_snapshot()
        else:
            # Fast/headless-like: no pacing, snapshot sparsely.
            self._collect_steps(self.fast_snapshot_interval)
            self._emit_snapshot()

    def _run_to_episode_end(self) -> None:
        """Advance the vector env until every env completes one episode."""
        counts = list(self._env_ep_finish)   # finished episodes per env
        targets = [c + 1 for c in counts[:len(self.vec_env.envs)]]
        guard = 0
        while guard < 50000:
            if all(self._env_ep_finish[i] >= targets[i]
                   for i in range(len(targets))):
                break
            self._collect_steps(1)
            guard += 1
        self._emit_snapshot()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def _run_eval_step(self) -> None:
        job = self._eval_job
        if not job:
            self._transition_error("evaluation job missing")
            return
        controller = job["controller"]
        seeds = list(self.eval_seeds if job["seed"] is None
                     else [int(job["seed"])])
        n = max(1, job["num_episodes"])
        if job["seed"] is not None:
            seeds = [int(job["seed"])] * n          # deterministic per episode
        seeds = seeds[:n]
        # Scenario/level precedence matches CLI/UI: level wins when both given.
        try:
            if job["scenario"] or job["level"] or job["overrides"]:
                cfg, _note = resolve_scenario(job["scenario"], job["level"],
                                              job["overrides"])
            else:
                cfg = self.scenario_cfg
        except Exception as exc:
            self._transition_error(f"evaluation failed: {exc}")
            return
        try:
            if controller == "algorithmic":
                results = [evaluate_algorithmic(cfg, seed=s) for s in seeds]
            else:
                policy = PPOAgent(OBS_DIM, ACTION_DIM, cfg=self.ppo,
                                  device=self.agent.device)
                if job["checkpoint"]:
                    p = self.checkpoint_dir / job["checkpoint"]
                    if not p.exists():
                        raise FileNotFoundError(
                            f"checkpoint not found: {job['checkpoint']}")
                    # Load into the isolated copy — the live training policy
                    # is never touched by an evaluation.
                    policy.load(str(p))
                else:
                    policy.net.load_state_dict(self.agent.net.state_dict())
                results = [self._eval_one_rl(cfg, s, policy) for s in seeds]
            summary = self._summarize_eval(controller, seeds, results)
            self.last_evaluation = {
                "type": "evaluation",
                "controller": controller,
                "scenario": cfg.get("name"),
                "seed": seeds,
                "results": results,
                "summary": summary,
            }
            self._log(f"evaluation ({controller}) finished: "
                      f"{len(results)} seeds, "
                      f"success={summary.get('success_rate')}%")
            self._emit("evaluation", self.last_evaluation)
        except Exception as exc:
            self._transition_error(f"evaluation failed: {exc}")
            return
        with self._lock:
            self._state = TrainerState.PAUSED
            self._state_detail = "paused"
            self._eval_job = None
        self._emit("status", self.status())

    def _eval_one_rl(self, cfg: dict, seed: int, agent: PPOAgent) -> dict:
        """Run one deterministic episode of ``agent`` on a fresh environment.

        Explicit seeds → reproducible evaluation. Every RL robot in the scene
        is measured (multi-RL) and reported individually via per-agent metrics;
        the summary aggregates across robots. The policy passed in is an
        isolated copy, so this never mutates a live training policy.
        """
        env = AMRCollisionEnv(cfg, seed=seed, dt=DT, safety=self.safety,
                              opponents=self.opponents
                              if cfg.get("n_opponents", 0) > 0 else None)
        obs, _info = env.reset(options={"seed": seed})
        obs = self._as_agent_obs(obs)          # (n_rl, obs_dim) rows
        n_rl = obs.shape[1]
        steps = 0
        dists = np.zeros(n_rl)
        collisions = 0
        safety_overrides = 0
        max_steps = cfg["max_steps"]
        while steps < max_steps:
            actions = np.zeros((n_rl, ACTION_DIM), dtype=np.float32)
            for k in range(n_rl):
                a, _lp, _v = agent.select_action(obs[k], deterministic=True)
                actions[k] = a
            obs, _r, _term, _trunc, info = env.step(actions)
            steps += 1
            for a in info.get("agents", []):
                if a.get("collided"):
                    collisions += 1
                if a.get("override", {}).get("overridden"):
                    safety_overrides += 1
            for k in range(n_rl):
                s = env.rl_agents[k].state
                dists[k] += float(np.hypot(s.vx, s.vy)) * DT
            if _term or _trunc:
                break
        per_agent = []
        for k in range(n_rl):
            ag = env.rl_agents[k]
            m = ag.metrics
            per_agent.append({
                "id": ag.state.id,
                "distance": round(float(dists[k]), 2),
                "time": round(steps * DT, 2),
                "waiting_time": round(float(m["waiting_time"]), 2),
                "path_deviation": round(float(m["path_deviation"]), 2),
                "safety_overrides": int(m["safety_overrides"]),
                "stuck_steps": int(m["stuck_steps"]),
            })
        return {
            "seed": seed,
            "success": bool(info.get("success")),
            "steps": steps,
            "time": round(steps * DT, 2),
            "distance": round(float(np.sum(dists)), 2),
            "collisions": int(collisions),
            "safety_overrides": int(safety_overrides),
            "agent_count": n_rl,
            "agents": per_agent,
        }

    def _summarize_eval(self, controller, seeds, results) -> dict:
        n = max(1, len(results))
        summary = {
            "controller": controller,
            "n": len(results),
            "success_rate": round(100.0 * sum(1 for r in results
                                              if r.get("success")) / n, 2),
            "avg_time": round(float(np.mean([r.get("time", 0) for r in results])), 2),
            "avg_distance": round(float(np.mean([r.get("distance", 0)
                                                 for r in results])), 2),
            "total_collisions": int(sum(r.get("collisions", 0) for r in results)),
            "avg_safety_overrides": round(
                float(np.mean([r.get("safety_overrides", 0) for r in results])), 2)
                if results else 0.0,
            "agent_count": max((r.get("agent_count", 1) for r in results),
                               default=1),
        }
        per = {}
        for r in results:
            for a in r.get("agents", []):
                per.setdefault(a["id"], []).append(a)
        if per:
            summary["per_agent"] = {}
            for aid, rows in per.items():
                m = max(1, len(rows))
                summary["per_agent"][aid] = {
                    "avg_time": round(float(np.mean([x["time"] for x in rows])), 2),
                    "avg_distance": round(float(np.mean([x["distance"]
                                                         for x in rows])), 2),
                    "avg_waiting_time": round(float(np.mean([x["waiting_time"]
                                                             for x in rows])), 2),
                    "avg_path_deviation": round(float(np.mean([x["path_deviation"]
                                                               for x in rows])), 2),
                    "avg_safety_overrides": round(float(np.mean(
                        [x["safety_overrides"] for x in rows])), 2),
                }
        return summary

    # ------------------------------------------------------------------
    # Events / snapshots
    # ------------------------------------------------------------------

    def emit_event(self, kind: str, payload: dict) -> None:
        """Broadcast an application-level event (e.g. league status) to every
        attached WebSocket bridge."""
        self._emit(kind, payload)

    def _emit(self, kind: str, payload: dict) -> None:
        payload = dict(payload)
        payload.setdefault("type", kind)
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(kind, payload)
            except Exception:
                pass

    @staticmethod
    def _jsonable_reward(value):
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, (list, tuple)):
            return [float(v) for v in value]
        return value

    def _emit_snapshot(self) -> None:
        if self.vec_env is None:
            return
        env = self.vec_env.envs[0]
        try:
            snap = env.render_state()
        except Exception as exc:
            self._log(f"snapshot error: {exc}")
            return
        snap["status"] = self.status()
        snap["reward_components"] = {
            k: round(float(np.mean(v)), 3) if isinstance(v, (list, tuple))
            else round(v, 3)
            for k, v in (env.last_info.get("reward_components") or {}).items()}
        snap["reward"] = self._jsonable_reward(env.last_info.get("reward"))
        snap["terminated"] = env.last_info.get("terminated", False)
        snap["truncated"] = env.last_info.get("truncated", False)
        self.last_snapshot = snap
        self._emit("snapshot", snap)

    def _step_info(self) -> dict:
        env = self.vec_env.envs[0]
        self._ensure_ep_tracking()
        rcomps = env.last_info.get("reward_components") or {}
        return {
            "type": "step",
            "step": env.step_count,
            "time": round(env._time, 3),
            "episodes": self.episode_count,
            "total_steps": self.total_steps,
            "observation": env.render_state()["observation"],
            "action": env.render_state()["action"],
            "reward": self._jsonable_reward(env.last_info.get("reward")),
            "reward_components": {
                k: round(float(np.mean(v)), 3) if isinstance(v, (list, tuple))
                else round(v, 3) for k, v in rcomps.items()},
        }

    def _transition(self, new_state: TrainerState, detail: str = "") -> None:
        with self._lock:
            self._state = new_state
            self._state_detail = detail

    def _log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self._log_buffer.append(line)
        self._emit("log", {"type": "log", "message": message, "ts": ts})

    def shutdown(self) -> None:
        with self._lock:
            self._running = False
            self._state = TrainerState.STOPPING
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self.vec_env is not None:
            self.vec_env.close()