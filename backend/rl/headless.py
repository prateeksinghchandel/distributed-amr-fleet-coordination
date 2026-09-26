"""
headless.py — fast training without the WebSocket server.

Runs the same RLTrainer/vector-env/P PO stack at maximum speed and streams
progress to stdout, then saves a checkpoint. Every few thousand steps a copy
of the policy is snapshotted to disk so long runs can be interrupted safely.

Usage::

    python -m rl.headless --scenario dense_traffic --steps 20000 --save-every 5000
    python -m rl.headless --level 4 --steps 60000 --n-envs 24 --seed 7
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def run(args: argparse.Namespace) -> None:
    from rl.trainer import RLTrainer, TrainerState
    from rl.scenarios import scenario_config, curriculum_config

    cfg = curriculum_config(args.level) if args.level else scenario_config(args.scenario)
    print(f"[headless] scenario '{cfg['name']}' robots={cfg.get('n_robots')} "
          f"rl={cfg.get('n_rl')} max_steps={cfg.get('max_steps')} "
          f"n_envs={args.n_envs} safety={args.safety}",
          flush=True)

    ckpt_dir = args.checkpoint_dir or (Path(__file__).resolve().parents[1] /
                                       "rl" / "checkpoints")
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    train = RLTrainer(
        cfg, n_envs=args.n_envs, rollout_steps=args.rollout_steps, seed=args.seed,
        safety=args.safety, device=args.device,
        checkpoint_dir=str(ckpt_dir), speed=0.0,
    )

    checkpoint_every = max(args.save_every, 1)
    steps_since_checkpoint = 0
    last_steps = 0
    t_start = time.time()
    last_report = time.time()
    train.start()

    try:
        while True:
            train._wake.set()  # keep the loop busy even at speed=0
            time.sleep(0.05)
            steps_since_checkpoint += train.total_steps - last_steps
            last_steps = train.total_steps
            if steps_since_checkpoint >= checkpoint_every:
                steps_since_checkpoint = 0
                saved = train.save_checkpoint("autosave")
                print(f"[headless] autosaved {saved.get('name')} @ "
                      f"{train.total_steps} steps", flush=True)

            if train.state() == TrainerState.ERROR:
                raise RuntimeError(f"headless training crashed: {train.status().get('error')}")

            if time.time() - last_report > args.report_every:
                last_report = time.time()
                m = train.metrics_brief()
                rate = int(train.total_steps / max(time.time() - t_start, 1e-6))
                print(f"[headless] steps={train.total_steps} episodes={m['episodes']} "
                      f"avg_reward={m['avg_reward']} success={m['success_rate']}% "
                      f"rate={rate} step/s", flush=True)

            if train.total_steps >= args.steps:
                break

        rate = int(train.total_steps / max(time.time() - t_start, 1e-6))
        print(f"[headless] target reached: {train.total_steps} steps in "
              f"{time.time() - t_start:.1f}s ({rate} step/s)", flush=True)
    finally:
        train.pause()
        if args.eval:
            train.evaluate("rl", num_episodes=args.eval_episodes)
            wait_eval(train)
        saved = train.save_checkpoint(args.out or f"headless-{cfg['name']}")
        print(f"[headless] saved checkpoint: {saved.get('path')}", flush=True)
        print("[headless] final metrics:", json_metrics(train.metrics_brief()), flush=True)
        train.shutdown()


def wait_eval(train, timeout: float = 60.0) -> None:
    from rl.trainer import TrainerState
    t0 = time.time()
    while train.state() == TrainerState.EVALUATING and time.time() - t0 < timeout:
        time.sleep(0.1)
    if train.last_evaluation:
        ev = train.last_evaluation
        print(f"[headless] evaluation: [{ev['controller']}] "
              f"success={ev['summary']['success_rate']}% "
              f"avg_time={ev['summary']['avg_time']} "
              f"avg_distance={ev['summary']['avg_distance']} "
              f"collisions={ev['summary']['total_collisions']}", flush=True)


def json_metrics(m: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in m.items())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("Usage:")[1].strip())
    parser.add_argument("--scenario", default="obstacle_avoidance")
    parser.add_argument("--level", type=int, default=None)
    parser.add_argument("--steps", type=int, default=20000)
    parser.add_argument("--n-envs", type=int, default=8)
    parser.add_argument("--rollout-steps", type=int, default=512)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--safety", default="guard",
                        choices=("off", "guard", "strict"))
    parser.add_argument("--device", default=None)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--save-every", type=int, default=5000)
    parser.add_argument("--report-every", type=float, default=5.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--eval-episodes", type=int, default=20)
    run(parser.parse_args())


if __name__ == "__main__":
    main()