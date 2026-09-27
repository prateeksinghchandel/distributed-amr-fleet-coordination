"""
headless.py — fast training without the WebSocket server.

Runs the same RLTrainer/vector-env/P PO stack at maximum speed and streams
progress to stdout, then saves a checkpoint. Every few thousand steps a copy
of the policy is snapshotted to disk so long runs can be interrupted safely.

Usage::

    python -m rl.headless --scenario dense_traffic --steps 20000 --save-every 5000
    python -m rl.headless --level 4 --steps 60000 --n-envs 24 --seed 7
    python -m rl.headless --selfplay --steps 40000 --pool-size 4 --pool-every 3000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _max_steps_override(args) -> Optional[dict]:
    return {"max_steps": args.max_steps} if args.max_steps else None


def _reward_override(args) -> Optional[dict]:
    import json
    return json.loads(args.reward_json)


def run(args: argparse.Namespace) -> None:
    from rl.trainer import RLTrainer, TrainerState
    from rl.scenarios import resolve_scenario
    from rl.env import RewardConfig

    cfg, note = resolve_scenario(args.scenario, args.level,
                                 _max_steps_override(args))
    reward = RewardConfig.from_dict(
        _reward_override(args) if getattr(args, "reward_json", None) else None)
    print(f"[headless] {note} robots={cfg.get('n_robots')} "
          f"rl={cfg.get('n_rl')} max_steps={cfg.get('max_steps')} "
          f"n_envs={args.n_envs} safety={args.safety}", flush=True)
    print(f"[headless] reward={json.dumps(reward.to_dict())}", flush=True)

    ckpt_dir = args.checkpoint_dir or (Path(__file__).resolve().parents[1] /
                                       "rl" / "checkpoints")
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    opponents = _load_opponents(ckpt_dir, args.opponents or [])
    if opponents:
        # Align the scene with the opponent pool (n_opponents must be > 0 for
        # the env to attach frozen peer policies) and pass the agents in.
        cfg["n_opponents"] = min(len(opponents),
                                 max(0, cfg.get("n_robots", 1) - 1))
        print(f"[headless] practising against {len(opponents)} frozen "
              f"opponent(s) (n_opponents={cfg['n_opponents']})", flush=True)

    train = RLTrainer(
        cfg, n_envs=args.n_envs, rollout_steps=args.rollout_steps, seed=args.seed,
        safety=args.safety, device=args.device,
        checkpoint_dir=str(ckpt_dir), speed=0.0,
        lr=args.lr, gamma=args.gamma, lam=args.lam, clip=args.clip,
        ent_coef=args.ent_coef, val_coef=args.val_coef,
        update_epochs=args.update_epochs, minibatch=args.minibatch,
        hidden=args.hidden,
        autosave_every=args.save_every,
        opponents=opponents,
        reward=reward.to_dict(),
    )

    ppo = train.ppo.to_dict()
    print(f"[headless] ppo lr={ppo['lr']} gamma={ppo['gamma']} lam={ppo['lam']} "
          f"clip={ppo['clip']} ent={ppo['ent_coef']} val={ppo['val_coef']} "
          f"epochs={ppo['update_epochs']} minibatch={ppo['minibatch']} "
          f"hidden={ppo['hidden']}", flush=True)

    t_start = time.time()
    last_report = time.time()
    train.start()

    try:
        while True:
            train._wake.set()  # keep the loop busy even at speed=0
            time.sleep(0.05)

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
            train.evaluate("rl", num_episodes=args.eval_episodes,
                           scenario=args.scenario, level=args.level)
            wait_eval(train)
        saved = train.save_checkpoint(args.out or f"headless-{cfg['name']}")
        print(f"[headless] saved checkpoint: {saved.get('path')}", flush=True)
        print("[headless] final metrics:", json_metrics(train.metrics_brief()), flush=True)
        train.shutdown()


def run_selfplay(args: argparse.Namespace) -> None:
    """Self-play variant: champion vs a rotating pool of frozen champions."""
    from rl.league import LeagueTrainer
    from rl.scenarios import resolve_scenario
    from rl.trainer import RLTrainer
    from rl.env import RewardConfig

    cfg, note = resolve_scenario(args.scenario, args.level,
                                 _max_steps_override(args))
    reward = RewardConfig.from_dict(
        _reward_override(args) if getattr(args, "reward_json", None) else None)
    default_ckpt = Path(__file__).resolve().parents[1] / "rl" / "checkpoints"
    ckpt_dir = Path(args.checkpoint_dir or args.pool_dir or default_ckpt)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    train = RLTrainer(
        cfg, n_envs=args.n_envs, rollout_steps=args.rollout_steps, seed=args.seed,
        safety=args.safety, device=args.device,
        checkpoint_dir=str(ckpt_dir), speed=0.0,
        autosave_every=args.save_every,
        lr=args.lr, gamma=args.gamma, lam=args.lam, clip=args.clip,
        ent_coef=args.ent_coef, val_coef=args.val_coef,
        update_epochs=args.update_epochs, minibatch=args.minibatch,
        hidden=args.hidden,
        reward=reward.to_dict(),
    )
    print(f"[league] {note} n_envs={args.n_envs} "
          f"safety={args.safety} pool_size={args.pool_size} "
          f"pool_every={args.pool_every} checkpoint_dir={ckpt_dir}", flush=True)

    league = LeagueTrainer(train, pool_size=args.pool_size,
                           checkpoint_dir=ckpt_dir)
    seeded = league.seed_pool(ckpt_dir)
    if seeded:
        print(f"[league] seeded pool from {len(seeded)} checkpoint(s): "
              f"{', '.join(seeded)}", flush=True)
    league.run(steps=args.steps, pool_every=args.pool_every,
               report_every=args.report_every,
               vs_pool_episodes=args.vs_pool_episodes)


def _load_opponents(ckpt_dir: Path, names: list[str]):
    """Load frozen checkpoint policies to act as opponents.

    ``names`` are checkpoint filenames inside ``ckpt_dir``; a policy is built
    from the checkpoint's own obs/action dims and config so any saved net
    (single- or multi-RL) can be Practice targets.
    """
    from rl.rl_policy import PPOAgent
    agents = []
    for name in names:
        p = ckpt_dir / name
        if not p.exists():
            raise FileNotFoundError(f"opponent checkpoint not found: {name}")
        data = __import__("torch").load(str(p), map_location="cpu")
        agent = PPOAgent(int(data["obs_dim"]), int(data["action_dim"]),
                         device="cpu")
        agent.load(str(p))
        agents.append(agent)
    return agents


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
    parser.add_argument("--max-steps", type=int, default=None,
                        help="override per-episode max sim steps")
    parser.add_argument("--steps", type=int, default=20000)
    parser.add_argument("--n-envs", type=int, default=8)
    parser.add_argument("--rollout-steps", type=int, default=512)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--safety", default="guard",
                        choices=("off", "guard", "strict"))
    parser.add_argument("--device", default=None)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--lam", type=float, default=0.95)
    parser.add_argument("--clip", type=float, default=0.2)
    parser.add_argument("--ent-coef", type=float, default=0.01)
    parser.add_argument("--val-coef", type=float, default=0.5)
    parser.add_argument("--update-epochs", type=int, default=4)
    parser.add_argument("--minibatch", type=int, default=64)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--opponents", nargs="*", default=None,
                        help="frozen checkpoint names to practice against "
                             "(n_opponents set automatically)")
    parser.add_argument("--reward-json", default=None,
                        help="reward-config overrides as a JSON object, e.g. "
                             '{\\"clearance\\": 5, \\"path_return\\": 1.2}')
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--save-every", type=int, default=5000)
    parser.add_argument("--report-every", type=float, default=5.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--eval-episodes", type=int, default=20)
    parser.add_argument("--selfplay", action="store_true",
                        help="train a champion against a pool of frozen "
                             "former-champion policies")
    parser.add_argument("--pool-size", type=int, default=4)
    parser.add_argument("--pool-every", type=int, default=3000,
                        help="promote the champion into the pool every N sim steps")
    parser.add_argument("--pool-dir", default=None,
                        help="dir to seed the pool from (default: checkpoint dir)")
    parser.add_argument("--vs-pool-episodes", type=int, default=3)
    args = parser.parse_args()
    (run_selfplay(args) if args.selfplay else run(args))


if __name__ == "__main__":
    main()