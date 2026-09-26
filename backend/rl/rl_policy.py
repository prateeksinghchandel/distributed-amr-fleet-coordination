"""
rl_policy.py — Compact PPO (Proximal Policy Optimization) agent and vector
environment used to train local collision-avoidance policies.

The policy is a small MLP actor-critic over the fixed-size observation vector
(see ``rl/env.py``). Actions are a bounded 2-D Gaussian (Tanh squashed) so the
output lives inside the ``Box(-1, 1)^2`` action space without clipping.

Design choices:
  * PPO over the existing Gymnasium ``AMRCollisionEnv`` — chosen because it is
    robust, on-policy, parallelisable over the cheap vectorised environment,
    and lets us reuse the exact same environment in visual / headless /
    evaluation modes. No external RL dependency beyond ``gymnasium`` + ``torch``.
  * The trainer owns the rollout collection; this module only stores
    transitions and performs GAE + minibatch updates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Normal, TanhTransform, TransformedDistribution

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


@dataclass
class PPOConfig:
    """Explicit, validated PPO hyperparameters.

    This is the canonical configuration object: the trainer, headless runner
    and server CLI all funnel their PPO settings through it, it is embedded in
    every checkpoint, and it is surfaced in status/UI so a run's active values
    are never implicit.
    """

    lr: float = 3e-4
    gamma: float = 0.99
    lam: float = 0.95
    clip: float = 0.2
    ent_coef: float = 0.01
    val_coef: float = 0.5
    update_epochs: int = 4
    minibatch: int = 64
    hidden: int = 128

    def validate(self) -> "PPOConfig":
        if not (0.0 < self.lr < 1.0):
            raise ValueError(f"lr must be in (0, 1), got {self.lr}")
        if not (0.0 < self.gamma < 1.0):
            raise ValueError(f"gamma must be in (0, 1), got {self.gamma}")
        if not (0.0 < self.lam <= 1.0):
            raise ValueError(f"lam must be in (0, 1], got {self.lam}")
        if not (0.0 < self.clip <= 1.0):
            raise ValueError(f"clip must be in (0, 1], got {self.clip}")
        if self.ent_coef < 0.0:
            raise ValueError(f"ent_coef must be >= 0, got {self.ent_coef}")
        if self.val_coef < 0.0:
            raise ValueError(f"val_coef must be >= 0, got {self.val_coef}")
        if self.update_epochs < 1:
            raise ValueError(f"update_epochs must be >= 1, got {self.update_epochs}")
        if self.minibatch < 1:
            raise ValueError(f"minibatch must be >= 1, got {self.minibatch}")
        if self.hidden < 16:
            raise ValueError(f"hidden must be >= 16, got {self.hidden}")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "PPOConfig":
        cfg = cls()
        if data:
            for k, v in data.items():
                if k in ("lr", "gamma", "lam", "clip", "ent_coef", "val_coef"):
                    setattr(cfg, k, float(v))
                elif k in ("update_epochs", "minibatch", "hidden"):
                    setattr(cfg, k, int(v))
        return cfg.validate()


class ActorCritic(nn.Module):
    """MLP actor-critic: obs -> hidden -> (action mean | value)."""

    def __init__(self, obs_dim: int, action_dim: int, hidden: int = 128):
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.Tanh(),
            nn.Linear(hidden, hidden // 2), nn.Tanh(),
        )
        self.mu = nn.Linear(hidden // 2, action_dim)
        self.value = nn.Linear(hidden // 2, 1)
        self.log_std = nn.Parameter(torch.zeros(action_dim))
        self._obs_dim = obs_dim
        self._action_dim = action_dim

    def forward(self, obs: torch.Tensor):
        h = self.shared(obs)
        return self.mu(h), self.value(h)

    def dist(self, obs: torch.Tensor):
        mu, _val = self.forward(obs)
        std = torch.nn.functional.softplus(self.log_std) + 1e-3
        base = Normal(mu, std)
        return TransformedDistribution(base, [TanhTransform(cache_size=1)])

    def act(self, obs: torch.Tensor, deterministic: bool = False):
        dist = self.dist(obs)
        if deterministic:
            action = dist.base_dist.mean
            action = torch.tanh(action)
        else:
            action = dist.sample()
        value = self.value(self.shared(obs))
        logp = dist.log_prob(action).sum(dim=-1)
        return action, logp, value.squeeze(-1)

    def evaluate(self, obs: torch.Tensor, action: torch.Tensor):
        dist = self.dist(obs)
        logp = dist.log_prob(action).sum(dim=-1)
        entropy = dist.base_dist.entropy().sum(dim=-1)
        value = self.value(self.shared(obs)).squeeze(-1)
        return logp, entropy, value


class RolloutBuffer:
    """Flat buffer of transitions with GAE computation for PPO updates."""

    def __init__(self, obs_dim: int, action_dim: int):
        self.obs: list[np.ndarray] = []
        self.actions: list[np.ndarray] = []
        self.logp: list[float] = []
        self.values: list[float] = []
        self.rewards: list[float] = []
        self.dones: list[bool] = []   # True when terminated (no bootstrap)
        self.reset()

    def reset(self) -> None:
        self.obs.clear()
        self.actions.clear()
        self.logp.clear()
        self.values.clear()
        self.rewards.clear()
        self.dones.clear()

    def push(self, obs, action, logp, value, reward, done) -> None:
        self.obs.append(np.asarray(obs, dtype=np.float32))
        self.actions.append(np.asarray(action, dtype=np.float32))
        self.logp.append(float(logp))
        self.values.append(float(value))
        self.rewards.append(float(reward))
        self.dones.append(bool(done))

    def __len__(self) -> int:
        return len(self.obs)

    def _tensors(self, device):
        return (
            torch.tensor(np.asarray(self.obs, dtype=np.float32), device=device),
            torch.tensor(np.asarray(self.actions, dtype=np.float32), device=device),
            torch.as_tensor(self.logp, dtype=torch.float32, device=device),
            torch.as_tensor(self.values, dtype=torch.float32, device=device),
            torch.as_tensor(self.rewards, dtype=torch.float32, device=device),
            torch.as_tensor(self.dones, dtype=torch.float32, device=device),
        )

    def compute_gae(self, agent: PPOAgent, last_values: Sequence[float],
                    gamma: float = 0.99, lam: float = 0.95) -> tuple:
        """GAE over transitions stored step-major (one row per env each step).

        ``last_values[i]`` is the value of the current observation of env ``i``
        (used to bootstrap the tail of each env's truncated trajectory).
        """
        obs, actions, logp, values, rewards, dones = self._tensors(agent.device)
        n = len(self)
        n_envs = max(1, len(last_values))
        if n == 0:
            return obs, actions, logp, values, values
        rows = n // n_envs
        if rows == 0:
            return obs, actions, logp, values, values
        vals = values.view(rows, n_envs)
        rews = rewards.view(rows, n_envs)
        dons = dones.view(rows, n_envs)
        last_values_t = torch.as_tensor(
            [float(v) for v in last_values], dtype=torch.float32, device=agent.device)
        gae = torch.zeros_like(rews)
        acc = torch.zeros(n_envs, device=agent.device)
        for t in reversed(range(rows)):
            nxt = vals[t + 1] if t + 1 < rows else last_values_t
            delta = rews[t] + gamma * nxt * (1.0 - dons[t]) - vals[t]
            acc = delta + gamma * lam * (1.0 - dons[t]) * acc
            gae[t] = acc
        returns = (vals + gae).reshape(-1)
        gae = gae.reshape(-1)
        return obs, actions, logp, gae, returns


class PPOAgent:
    """PPO actor-critic agent with save/load checkpoint support."""

    def __init__(self, obs_dim: int, action_dim: int, *,
                 device: str = DEVICE, cfg: Optional[PPOConfig] = None,
                 lr: Optional[float] = None, gamma: Optional[float] = None,
                 lam: Optional[float] = None, clip: Optional[float] = None,
                 ent_coef: Optional[float] = None, val_coef: Optional[float] = None,
                 update_epochs: Optional[int] = None,
                 minibatch: Optional[int] = None, hidden: Optional[int] = None):
        if cfg is None:
            cfg = PPOConfig()
        # Backwards-compatible inline overrides win over the config object.
        override = dict(lr=lr, gamma=gamma, lam=lam, clip=clip,
                        ent_coef=ent_coef, val_coef=val_coef,
                        update_epochs=update_epochs, minibatch=minibatch,
                        hidden=hidden)
        for k, v in override.items():
            if v is not None:
                setattr(cfg, k, v)
        self.cfg = cfg.validate()
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.device = device
        self.lr = cfg.lr
        self.gamma = cfg.gamma
        self.lam = cfg.lam
        self.clip = cfg.clip
        self.ent_coef = cfg.ent_coef
        self.val_coef = cfg.val_coef
        self.update_epochs = cfg.update_epochs
        self.minibatch = cfg.minibatch
        self.net = ActorCritic(obs_dim, action_dim, hidden=cfg.hidden).to(device)
        self.optimizer = torch.optim.Adam(self.net.parameters(), lr=cfg.lr)
        self.updates = 0
        self.meta: Optional[dict] = None

    def select_action(self, obs: np.ndarray, deterministic: bool = False):
        """Sample actions for one or more agents (rows of ``obs``).

        Returns action ``(B, action_dim)``, logp ``(B,)`` and value ``(B,)``
        where ``B`` is the batch/agent count.
        """
        obs_t = torch.as_tensor(np.asarray(obs, dtype=np.float32),
                                device=self.device)
        with torch.no_grad():
            action, logp, value = self.net.act(obs_t, deterministic=deterministic)
        return (action.cpu().numpy(), logp.cpu().numpy(),
                value.cpu().numpy())

    def value_of(self, obs: np.ndarray) -> np.ndarray:
        """Critic values for one or more agents (one per obs row)."""
        with torch.no_grad():
            obs_t = torch.as_tensor(np.asarray(obs, dtype=np.float32),
                                    device=self.device)
            _, val = self.net(obs_t)
            return np.asarray(val.squeeze(-1).cpu().numpy(), dtype=np.float32)

    def train(self, buffer: RolloutBuffer, last_value: float) -> dict:
        obs, actions, old_logp, gae, returns = buffer.compute_gae(
            self, last_value, gamma=self.gamma, lam=self.lam)
        n = len(buffer)
        if n < self.minibatch:
            return {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0,
                    "updates": self.updates, "samples": n}
        adv = (gae - gae.mean()) / (gae.std() + 1e-8)
        idx = np.arange(n)
        pi_losses = []
        v_losses = []
        entropies = []
        for _ in range(self.update_epochs):
            np.random.shuffle(idx)
            for start in range(0, n, self.minibatch):
                b = idx[start:start + self.minibatch]
                b = torch.as_tensor(b, device=self.device)
                logp, entropy, value = self.net.evaluate(obs[b], actions[b])
                ratio = torch.exp(logp - old_logp[b])
                surr1 = ratio * adv[b]
                surr2 = torch.clamp(ratio, 1.0 - self.clip, 1.0 + self.clip) * adv[b]
                pi_loss = -torch.min(surr1, surr2).mean()
                v_loss = torch.nn.functional.mse_loss(value, returns[b])
                ent = entropy.mean()
                loss = pi_loss + self.val_coef * v_loss - self.ent_coef * ent
                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), 0.5)
                self.optimizer.step()
                pi_losses.append(float(pi_loss.detach()))
                v_losses.append(float(v_loss.detach()))
                entropies.append(float(ent.detach()))
        self.updates += 1
        return {
            "policy_loss": float(np.mean(pi_losses)),
            "value_loss": float(np.mean(v_losses)),
            "entropy": float(np.mean(entropies)),
            "updates": self.updates,
            "samples": n,
        }

    def save(self, path: str, meta: Optional[dict] = None) -> None:
        torch.save({
            "obs_dim": self.obs_dim,
            "action_dim": self.action_dim,
            "device": self.device,
            "ppo": self.cfg.to_dict(),       # training configuration
            "meta": meta,                    # caller-supplied run metadata
            "net_state": self.net.state_dict(),
            "optimizer_state": self.optimizer.state_dict(),
            "updates": self.updates,
        }, path)

    def load(self, path: str) -> dict:
        data = torch.load(path, map_location=self.device)
        if data.get("obs_dim") != self.obs_dim or \
           data.get("action_dim") != self.action_dim:
            raise ValueError("checkpoint obs/action dims do not match env")
        if data.get("ppo"):
            self.cfg = PPOConfig.from_dict(data["ppo"])
        self.meta = data.get("meta")
        self.net.load_state_dict(data["net_state"])
        self.optimizer.load_state_dict(data["optimizer_state"])
        self.updates = int(data.get("updates", 0))
        return data

    def reset_weights(self) -> None:
        for m in self.net.modules():
            if isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight, gain=0.6 if m is self.net.mu else 1.0)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
        with torch.no_grad():
            self.net.log_std.fill_(-0.9)
        self.updates = 0
        for g in self.optimizer.param_groups:
            g["lr"] = self.lr

    def summary(self) -> dict:
        return {
            "obs_dim": self.obs_dim,
            "action_dim": self.action_dim,
            "device": self.device,
            "updates": self.updates,
            "params": sum(p.numel() for p in self.net.parameters()),
            "log_std": float(np.exp(self.net.log_std.detach().cpu().numpy()).mean()),
        }

    def clone_for_play(self, deterministic: bool = True) -> "PPOAgent":
        """Return a frozen copy of this policy for use as an opponent.

        The clone shares no parameters (fresh ``net`` + copied state_dict), has
        no optimizer, is in eval mode, and is never trained — it exists to run
        former champs as stationary peers inside training scenes.
        """
        clone = PPOAgent(self.obs_dim, self.action_dim, device=self.device)
        clone.net.load_state_dict(self.net.state_dict())
        clone.net.eval()
        clone.play_deterministic = bool(deterministic)
        return clone

    @property
    def play_deterministic(self) -> bool:
        return getattr(self, "_play_deterministic", True)

    @play_deterministic.setter
    def play_deterministic(self, value: bool) -> None:
        self._play_deterministic = bool(value)

    def play_action(self, obs: np.ndarray) -> np.ndarray:
        """Action for an opponent, sampled from its frozen policy."""
        action, _, _ = self.select_action(obs, deterministic=self.play_deterministic)
        return np.asarray(action, dtype=np.float32)


def load_policy(path: str, device: Optional[str] = None) -> PPOAgent:
    """Build a PPOAgent from a saved checkpoint (weights + dims only)."""
    data = torch.load(path, map_location=(device or DEVICE))
    agent = PPOAgent(int(data["obs_dim"]), int(data["action_dim"]),
                     device=device or DEVICE)
    agent.net.load_state_dict(data["net_state"])
    agent.net.eval()
    agent.updates = int(data.get("updates", 0))
    agent.play_deterministic = True
    return agent


class VectorEnv:
    """Vectorised wrapper around multiple identical ``AMRCollisionEnv``s.

    Episodes that finish are automatically re-seeded and reset (Stable-Baselines
    convention): parallel PPO gathers whole episodes without manual plumbing.
    The first environment is the visual "reference" env used for snapshots.

    Scene seeds come from ``seed_source`` (a callable returning the next scene
    seed) — normally a trainer-owned ``SceneSeedGen`` that survives env
    rebuilds, so the campaign's scene sequence never restarts. When no seed
    source is supplied a private RNG is used (back-compat for direct use).
    """

    def __init__(self, env_factory, n_envs: int, base_seed: int = 0,
                 seed_source=None):
        self.envs = [env_factory(seed=base_seed + i) for i in range(n_envs)]
        self.n_envs = n_envs
        self._seed_source = seed_source if callable(seed_source) else None
        self._rs = np.random.RandomState(base_seed + n_envs * 997)

    def _next_seed(self) -> int:
        if self._seed_source is not None:
            return int(self._seed_source())
        return int(self._rs.randint(0, 2 ** 31 - 1))

    def reset_all(self) -> np.ndarray:
        obs = []
        for e in self.envs:
            o, _ = e.reset(options={"seed": self._next_seed()})
            obs.append(o)
        return np.asarray(obs, dtype=np.float32)

    def reset_env(self, idx: int) -> np.ndarray:
        o, _ = self.envs[idx].reset(options={"seed": self._next_seed()})
        return np.asarray(o, dtype=np.float32)

    def step(self, actions: np.ndarray):
        obs_list, rew_list, note_list, info_list = [], [], [], []
        actions = np.asarray(actions, dtype=np.float32)
        for i, e in enumerate(self.envs):
            o, r, term, trunc, info = e.step(actions[i])
            if term or trunc:
                o = self.reset_env(i)
            obs_list.append(np.asarray(o, dtype=np.float32))
            rew_list.append(r)
            note_list.append((bool(term), bool(trunc)))
            info_list.append(info)
        # Single-RL scenes give one scalar per env; multi-RL scenes give one
        # reward per agent, so keep the returned array shaped (n_envs, ...).
        return np.asarray(obs_list), np.asarray(rew_list, dtype=np.float32), \
            info_list

    def close(self) -> None:
        for e in self.envs:
            try:
                e.close()
            except Exception:
                pass