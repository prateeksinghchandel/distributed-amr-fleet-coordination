"""
league.py — self-play training: one champion policy vs a pool of frozen
former champions.

The single learner (the shared "brain" every AMR would run) is an ``RLTrainer``
constructed in max-speed mode. Every ``pool_every`` sim steps its weights are
frozen into the opponent pool (oldest evicted); the training scenes then engage
up to ``n_opponents`` peers that execute those frozen policies, so the policy
practises dodging skilled, diverse opponents instead of only dumb algorithmic
walkers.

Headless-first: no server or UI; progress prints to stdout. Drive it from the
``rl.headless`` entrypoint with ``--selfplay``, or construct directly.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from rl.env import AMRCollisionEnv, DT
from rl.rl_policy import load_policy
from rl.scenarios import scenario_config
from rl.trainer import RLTrainer, TrainerState


class LeagueTrainer:
    """Manages the opponent pool and the champion-vs-pool schedule."""

    def __init__(self, trainer: RLTrainer, pool_size: int = 4,
                 checkpoint_dir: Optional[Path | str] = None):
        self.trainer = trainer
        self.pool_size = max(1, int(pool_size))
        self.checkpoint_dir = Path(checkpoint_dir or trainer.checkpoint_dir)
        self.pool: deque[tuple[str, "PPOAgent"]] = deque()
        self.pool_meta: deque[dict] = deque()
        self.generations = 0
        self.last_promotion = 0
        self.history: list[dict] = []
        self.running = False
        self.pool_every = 0
        self.vs_pool_episodes = 0
        self._lock = threading.Lock()
        self._manager: Optional[threading.Thread] = None
        self._manager_stop = threading.Event()
        self._error: Optional[str] = None

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------

    def seed_pool(self, directory: Optional[Path | str] = None) -> list[str]:
        """Load existing checkpoints as frozen opponents (capped at pool_size)."""
        d = Path(directory) if directory else self.checkpoint_dir
        added: list[str] = []
        for path in sorted(d.glob("*.pt")):
            if len(self.pool) >= self.pool_size:
                break
            try:
                clone = load_policy(str(path))
            except Exception:
                continue
            self.pool.append((path.name, clone))
            self.pool_meta.append({"name": path.name, "generation": None,
                                   "source": "seed", "steps": None})
            added.append(path.name)
        return added

    def promote(self, name: Optional[str] = None) -> dict:
        """Freeze current weights into the pool and rebuild training scenes.

        The weights are snapshotted on the trainer's own loop thread (it also
        runs the autograd backprops, so cloning/saving there keeps all torch
        work single-threaded); the environment rebuild is queued to run in the
        same thread too.
        """
        running = self.trainer.state() == TrainerState.TRAINING
        with self._lock:
            self.generations += 1
            gen = self.generations
        name = name or f"gen{gen}"
        if running:
            self.trainer.pause()
            self.trainer.wait_paused(8.0)
        try:
            save_path = str(self.checkpoint_dir / "champion.pt")

            def _freeze():
                clone = self.trainer.agent.clone_for_play(deterministic=False)
                try:
                    self.trainer.agent.save(save_path)
                except Exception as exc:
                    self.trainer._log(f"champion save failed: {exc}")
                return clone

            clone = self.trainer.run_job(_freeze, timeout=30.0)
            self.pool.append((name, clone))
            while len(self.pool) > self.pool_size:
                self.pool.popleft()
            with self._lock:
                self.pool_meta.append({"name": name, "generation": gen,
                                       "source": "promotion",
                                       "steps": self.trainer.total_steps})
                while len(self.pool_meta) > self.pool_size:
                    self.pool_meta.popleft()
            self.last_promotion = self.trainer.total_steps
            self.trainer.submit("set_opponents",
                                opponents=[c for _, c in self.pool])
            self.trainer._log(f"promoted generation {gen}: "
                              f"pool=[{', '.join(n for n, _ in self.pool)}]")
            self.trainer.emit_event("league", {
                **self.status(), "type": "league",
                "event": "promote", "name": name, "generation": gen})
        finally:
            if running:
                self.trainer.resume()
        return {"generation": gen, "pool": [n for n, _ in self.pool]}

    # ------------------------------------------------------------------
    # Champion vs pool evaluation
    # ------------------------------------------------------------------

    def champion_vs_pool(self, episodes: int = 3) -> dict:
        """Beat-the-opponent on a deterministic 2-robot scene.

        The learner (deterministic actions) races one frozen opponent per pool
        member; success = reached goal without colliding. Training is paused
        for the duration so weights stay stable, and the whole match — every
        inference — runs on the trainer's loop thread to keep torch
        single-threaded (see ``RLTrainer.run_job``).
        """
        pool = list(self.pool)
        if not pool:
            return {"opponents": [], "summary": {}}
        n = max(1, int(episodes))
        cfg = scenario_config("two_robot", {"n_opponents": 1})
        was_training = self.trainer.state() == TrainerState.TRAINING
        if was_training:
            self.trainer.pause()
            self.trainer.wait_paused(8.0)
        try:
            results = self.trainer.run_job(
                lambda: self._evaluate_pool(pool, n, cfg), timeout=None)
        finally:
            if was_training:
                self.trainer.resume()
        summary = {
            "opponents": len(results),
            "avg_success_rate": round(
                float(np.mean([r["success_rate"] for r in results])), 1),
        }
        self.history.append({
            "generation": self.generations,
            "steps": self.trainer.total_steps,
            **summary,
        })
        self.history = self.history[-200:]
        self.trainer.emit_event("league", {
            **self.status(), "type": "league", "event": "vs_pool",
            "opponents": results, "summary": summary,
            "generation": self.generations})
        return {"opponents": results, "summary": summary}

    def _evaluate_pool(self, pool, n: int, cfg: dict) -> list[dict]:
        results = []
        for name, clone in pool:
            wins = 0
            for ep in range(n):
                env = AMRCollisionEnv(cfg, seed=ep, dt=DT,
                                      safety=self.trainer.safety,
                                      opponents=[clone])
                obs, _ = env.reset(options={"seed": ep})
                won = False
                steps = 0
                while steps < cfg["max_steps"]:
                    a, _lp, _v = self.trainer.agent.select_action(
                        obs, deterministic=True)
                    obs, _r, term, trunc, info = env.step(a)
                    steps += 1
                    if info.get("success"):
                        won = True
                        break
                    if term or trunc:
                        break
                wins += int(won)
            results.append({
                "opponent": name,
                "success_rate": round(100.0 * wins / n, 1),
                "episodes": n,
            })
        return results

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Snapshot for the server/dashboard (thread-safe)."""
        with self._lock:
            return {
                "running": self.running,
                "generations": self.generations,
                "pool_size": self.pool_size,
                "pool": list(self.pool_meta),
                "last_promotion_steps": self.last_promotion,
                "scheduled_pool_every": self.pool_every,
                "scheduled_vs_pool_episodes": self.vs_pool_episodes,
                "history": self.history[-20:],
                "error": self._error,
            }

    def start(self, pool_every: int = 3000, vs_pool_episodes: int = 3,
              report_every: float = 5.0) -> dict:
        """Start a continuous self-play schedule on a manager thread.

        Training runs on the (already running) trainer; every ``pool_every``
        sim steps the weights are frozen into the pool and a champion-vs-pool
        match is run. Progress is emitted over the trainer WebSocket as
        ``league`` events.
        """
        with self._lock:
            if self.running:
                return {"ok": False, "error": "league already running",
                        **self.status()}
        self._manager_stop.clear()
        with self._lock:
            self.pool_every = max(1, int(pool_every))
            self.vs_pool_episodes = max(1, int(vs_pool_episodes))
            self._error = None
        if not self.pool:
            self.promote()   # baseline member so early scenes have variety
        self.trainer.start()
        self._manager = threading.Thread(
            target=self._drive_until_stopped, name="league-manager",
            args=(report_every,), daemon=True)
        with self._lock:
            self.running = True
        self._manager.start()
        self.trainer.emit_event("league", {**self.status(),
                                           "type": "league", "event": "start"})
        return {"ok": True, **self.status()}

    def stop(self) -> dict:
        """Park the league: stop the schedule and pause training."""
        self._manager_stop.set()
        manager = self._manager
        if manager is not None and manager.is_alive():
            manager.join(timeout=10.0)
        with self._lock:
            self.running = False
        self.trainer.pause()
        self.trainer.emit_event("league", {**self.status(),
                                           "type": "league", "event": "stop"})
        return {"ok": True, **self.status()}

    def _drive_until_stopped(self, report_every: float) -> None:
        t_start = time.time()
        last_report = time.time()
        try:
            while not self._manager_stop.is_set():
                self.trainer._wake.set()
                time.sleep(0.05)
                if self.trainer.state() == TrainerState.ERROR:
                    with self._lock:
                        self._error = self.trainer.status().get("error")
                    self.trainer.emit_event("league", {**self.status(),
                                                       "type": "league",
                                                       "event": "error"})
                    return
                if (self.trainer.total_steps > 0 and
                        self.trainer.total_steps - self.last_promotion >=
                        self.pool_every):
                    self.promote()
                    vs = self.champion_vs_pool(self.vs_pool_episodes)
                    s = vs["summary"]
                    print(f"[league] generation {self.generations} @ "
                          f"{self.trainer.total_steps} steps: "
                          f"vs pool avg success {s['avg_success_rate']}% "
                          f"({s['opponents']} opponents)", flush=True)
                if report_every and time.time() - last_report > report_every:
                    last_report = time.time()
                    m = self.trainer.metrics_brief()
                    rate = int(self.trainer.total_steps /
                               max(time.time() - t_start, 1e-6))
                    print(f"[league] steps={self.trainer.total_steps} "
                          f"episodes={m['episodes']} win={m['success_rate']}% "
                          f"gen={self.generations} rate={rate} step/s",
                          flush=True)
                    self.trainer.emit_event("league", {
                        **self.status(), "type": "league",
                        "event": "status", "metrics": m, "step_rate": rate})
        finally:
            with self._lock:
                self.running = False
            print(f"[league] schedule stopped @ {self.trainer.total_steps} "
                  f"steps, {self.generations} generations", flush=True)

    def run(self, steps: int = 20000, pool_every: int = 3000,
            report_every: float = 5.0, vs_pool_episodes: int = 3) -> None:
        """Single-shot headless run: train, promote every ``pool_every`` sim
        steps, then pause, freeze the final champion and shutdown."""
        self._manager_stop.clear()
        with self._lock:
            self.pool_every = max(1, int(pool_every))
            self.vs_pool_episodes = max(1, int(vs_pool_episodes))
            self._error = None
        if not self.pool:
            self.promote()   # baseline member so early scenes have variety
        self.trainer.start()
        t_start = time.time()
        last_report = time.time()
        try:
            while True:
                self.trainer._wake.set()
                time.sleep(0.05)
                if self.trainer.state() == TrainerState.ERROR:
                    raise RuntimeError(
                        f"league training crashed: "
                        f"{self.trainer.status().get('error')}")
                if (self.trainer.total_steps > 0 and
                        self.trainer.total_steps - self.last_promotion >= pool_every):
                    self.promote()
                    vs = self.champion_vs_pool(vs_pool_episodes)
                    s = vs["summary"]
                    print(f"[league] generation {self.generations} @ "
                          f"{self.trainer.total_steps} steps: "
                          f"vs pool avg success {s['avg_success_rate']}% "
                          f"({s['opponents']} opponents)", flush=True)
                if time.time() - last_report > report_every:
                    last_report = time.time()
                    m = self.trainer.metrics_brief()
                    rate = int(self.trainer.total_steps /
                               max(time.time() - t_start, 1e-6))
                    print(f"[league] steps={self.trainer.total_steps} "
                          f"episodes={m['episodes']} win={m['success_rate']}% "
                          f"gen={self.generations} rate={rate} step/s",
                          flush=True)
                if self.trainer.total_steps >= steps:
                    break
            print(f"[league] target reached: {self.trainer.total_steps} steps "
                  f"in {time.time() - t_start:.1f}s, "
                  f"{self.generations} generations", flush=True)
            self.promote()   # freeze the final champion too
        finally:
            self.trainer.pause()
            self.trainer.shutdown()