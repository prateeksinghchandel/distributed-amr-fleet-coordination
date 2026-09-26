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
from rl.rl_policy import PPOAgent, RolloutBuffer, VectorEnv
from rl.scenarios import (CURRICULUM, SCENARIO_PRESETS, _clamp_scenario,
                          evaluate_algorithmic, scenario_config,
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
                 device: Optional[str] = None, speed: float = 1.0,
                 fast_snapshot_interval: int = 40,
                 checkpoint_dir: Path | str = CHECKPOINT_DIR,
                 autosave_every: int = 0,
                 eval_seeds: Sequence[int] = (42, 43, 44, 45, 46)):
        self._lock = threading.RLock()
        self._state = TrainerState.IDLE
        self._state_detail = ""
        self._error: Optional[str] = None
        self._commands: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.scenario_cfg = _clamp_scenario(scenario or scenario_config("obstacle_avoidance"))
        self.seed = int(seed)
        self.n_envs = int(n_envs)
        self.safety = safety
        self.rollout_steps = int(rollout_steps)
        self.fast_snapshot_interval = int(fast_snapshot_interval)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.eval_seeds = list(eval_seeds)
        self.autosave_every = max(0, int(autosave_every))
        self._last_autosave_steps = 0
        self.last_autosave = None

        self.speed = float(speed)
        self.speed_steps_per_sec = 10.0   # sim steps per wall second at 1x
        self._last_chunk_time = 0.0

        if device is None:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.agent = PPOAgent(OBS_DIM, ACTION_DIM, lr=lr, gamma=gamma, lam=lam,
                              clip=clip, ent_coef=ent_coef, val_coef=val_coef,
                              update_epochs=update_epochs, minibatch=minibatch,
                              device=device)
        self.vec_env: Optional[VectorEnv] = None
        self.buffer = RolloutBuffer(OBS_DIM, ACTION_DIM)

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
            self._state = TrainerState.PAUSED
            self._state_detail = "stepping"
            self._step_request = max(1, int(count))
        self._wake.set()
        return {"ok": True, "state": self._state.value, "steps": self._step_request}

    def step_episode(self, **_) -> dict:
        """Run the current episode to completion (display env), pausing after."""
        with self._lock:
            self._state = TrainerState.PAUSED
            self._state_detail = "stepping_episode"
            self._env_ep_finish = [0] * self.n_envs   # handshake: non-empty targets
            self._step_request = 10 ** 9   # run until envs finish
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
        self.buffer.reset()
        self._emit_snapshot()
        return {"ok": True, "state": self._state.value}

    def reset_training(self, **_) -> dict:
        """Reset model weights, optimizer and all counters (keeps scenario)."""
        with self._lock:
            self.agent.reset_weights()
            self.buffer.reset()
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
        self.agent.save(str(path))
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
        """Switch scenario / curriculum level and rebuild the environment."""
        cfg = self.scenario_cfg
        try:
            if scenario and scenario in SCENARIO_PRESETS:
                cfg = scenario_config(scenario, overrides)
            elif level:
                cfg = curriculum_config(int(level), overrides)
            elif overrides:
                cfg = _clamp_scenario({**cfg, **overrides})
            else:
                return {"ok": False, "error": "no scenario/level provided"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self.scenario_cfg = cfg
        self._reset_envs()
        self.buffer.reset()
        self._emit_snapshot()
        self._emit("metrics", self.metrics_brief())
        self._emit("status", self.status())
        return {"ok": True, "scenario": self.scenario_cfg["name"],
                "difficulty": self.scenario_cfg["difficulty"]}

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
                "safety": self.safety,
                "speed": self.speed,
                "steps_per_second": (self.speed_steps_per_sec if self.speed > 0
                                     else "max"),
                "total_steps": self.total_steps,
                "episodes": self.episode_count,
                "n_envs": self.n_envs,
                "rollout_steps": self.rollout_steps,
                "agent": self.agent.summary(),
                "checkpoint_dir": str(self.checkpoint_dir),
                "autosave_every": self.autosave_every,
                "last_autosave": self.last_autosave,
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
            return AMRCollisionEnv(self.scenario_cfg, seed=seed, dt=DT,
                                   safety=self.safety)
        return factory

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
                                     base_seed=self.seed)
            self._obs = self.vec_env.reset_all()
            self.buffer.reset()

    def _reset_envs(self) -> None:
        if self.vec_env is not None:
            self.vec_env.close()
        self.vec_env = VectorEnv(self._env_factory, self.n_envs,
                                 base_seed=self.seed + self.episode_count)
        self._env_ep_finish = [0] * self.n_envs
        self._obs = self.vec_env.reset_all()
        self.buffer.reset()
        self._ensure_ep_tracking()

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
                if self._step_request > 0:
                    steps = self._step_request
                    if steps >= 10 ** 8:
                        self._step_request = 0
                        self._run_to_episode_end()
                    else:
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

    def _collect_steps(self, count: int) -> None:
        if self.vec_env is None or self._obs is None:
            self._ensure_env()
        obs = self._obs
        for _ in range(count):
            actions = np.zeros((self.n_envs, ACTION_DIM), dtype=np.float32)
            logps = np.zeros((self.n_envs,), dtype=np.float32)
            values = np.zeros((self.n_envs,), dtype=np.float32)
            for i in range(self.n_envs):
                a, lp, v = self.agent.select_action(obs[i])
                actions[i] = a
                logps[i] = lp
                values[i] = v
            next_obs, rewards, infos = self.vec_env.step(actions)
            last_values = [self.agent.value_of(next_obs[i])
                           for i in range(self.n_envs)]
            for i in range(self.n_envs):
                done = bool(infos[i].get("terminated") or infos[i].get("truncated"))
                self.buffer.push(obs[i], actions[i], float(logps[i]),
                                 float(values[i]), float(rewards[i]), done)
                self._update_ep_tracking(i, rewards[i], infos[i])
            # batch unroll / update
            self._maybe_update(last_values)
            obs = next_obs
            self._obs = obs
            self.total_steps += self.n_envs

    def _update_ep_tracking(self, i: int, reward: float, info: dict) -> None:
        self._ensure_ep_tracking()
        tr = self._ep_tracking[i]
        tr["reward"] += float(reward)
        tr["length"] += 1
        for k, vals in (info.get("reward_components") or {}).items():
            v = vals if isinstance(vals, (int, float)) else (vals[0] if vals else 0.0)
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
        if len(self.buffer) >= self.rollout_steps:
            result = self.agent.train(self.buffer, last_values)
            self.buffer.reset()
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
            self.agent.save(str(path))
            self._last_autosave_steps = self.total_steps
            self.last_autosave = {
                "name": "autosave.pt",
                "steps": self.total_steps,
                "time": time.time(),
            }
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
        if job["scenario"]:
            cfg = scenario_config(job["scenario"], job["overrides"])
        elif job["level"]:
            cfg = curriculum_config(int(job["level"]), job["overrides"])
        else:
            cfg = self.scenario_cfg
        try:
            if controller == "algorithmic":
                results = [evaluate_algorithmic(cfg, seed=s) for s in seeds]
            else:
                if job["checkpoint"]:
                    p = self.checkpoint_dir / job["checkpoint"]
                    if p.exists():
                        self.agent.load(str(p))
                obs, info = self._eval_env_reset(cfg, seeds[0])
                results = []
                for s in seeds:
                    res = self._eval_one_rl(cfg, s)
                    results.append(res)
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

    def _eval_env_reset(self, cfg, seed):
        e = AMRCollisionEnv(cfg, seed=seed, dt=DT, safety=self.safety)
        return e.reset(options={"seed": seed})

    def _eval_one_rl(self, cfg: dict, seed: int) -> dict:
        env = AMRCollisionEnv(cfg, seed=seed, dt=DT, safety=self.safety)
        obs, _info = env.reset(options={"seed": seed})
        steps = 0
        dist = 0.0
        success = False
        collisions = 0
        safety_overrides = 0
        max_steps = cfg["max_steps"]
        while steps < max_steps:
            action, _lp, _v = self.agent.select_action(obs, deterministic=True)
            obs, _r, term, trunc, info = env.step(action)
            steps += 1
            for a in info.get("agents", []):
                if a.get("collided"):
                    collisions += 1
                if a.get("override", {}).get("overridden"):
                    safety_overrides += 1
            dist += float(np.hypot(env.rl_agents[0].state.vx,
                                   env.rl_agents[0].state.vy)) * DT
            if term or trunc:
                success = bool(info.get("success"))
                break
        return {
            "seed": seed,
            "success": success,
            "steps": steps,
            "time": round(steps * DT, 2),
            "distance": round(dist, 2),
            "collisions": int(collisions),
            "safety_overrides": int(safety_overrides),
            "model": self.agent.summary(),
        }

    def _summarize_eval(self, controller, seeds, results) -> dict:
        n = max(1, len(results))
        return {
            "controller": controller,
            "n": len(results),
            "success_rate": round(100.0 * sum(1 for r in results
                                              if r.get("success")) / n, 2),
            "avg_time": round(float(np.mean([r.get("time", 0) for r in results])), 2),
            "avg_distance": round(float(np.mean([r.get("distance", 0)
                                                 for r in results])), 2),
            "total_collisions": int(sum(r.get("collisions", 0) for r in results)),
        }

    # ------------------------------------------------------------------
    # Events / snapshots
    # ------------------------------------------------------------------

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
            k: round(v, 3) for k, v in
            (env.last_info.get("reward_components") or {}).items()}
        snap["reward"] = env.last_info.get("reward")
        snap["terminated"] = env.last_info.get("terminated", False)
        snap["truncated"] = env.last_info.get("truncated", False)
        self.last_snapshot = snap
        self._emit("snapshot", snap)

    def _step_info(self) -> dict:
        env = self.vec_env.envs[0]
        self._ensure_ep_tracking()
        return {
            "type": "step",
            "step": env.step_count,
            "time": round(env._time, 3),
            "episodes": self.episode_count,
            "total_steps": self.total_steps,
            "observation": env.render_state()["observation"],
            "action": env.render_state()["action"],
            "reward": env.last_info.get("reward"),
            "reward_components": (env.last_info.get("reward_components")
                                  or {}),
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