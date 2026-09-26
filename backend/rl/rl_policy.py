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

from typing import Optional, Sequence

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Normal, TanhTransform, TransformedDistribution

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


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
                 device: str = DEVICE, lr: float = 3e-4, gamma: float = 0.99,
                 lam: float = 0.95, clip: float = 0.2, ent_coef: float = 0.01,
                 val_coef: float = 0.5, update_epochs: int = 4,
                 minibatch: int = 64, hidden: int = 128):
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.device = device
        self.lr = lr
        self.gamma = gamma
        self.lam = lam
        self.clip = clip
        self.ent_coef = ent_coef
        self.val_coef = val_coef
        self.update_epochs = update_epochs
        self.minibatch = minibatch
        self.net = ActorCritic(obs_dim, action_dim, hidden=hidden).to(device)
        self.optimizer = torch.optim.Adam(self.net.parameters(), lr=lr)
        self.updates = 0

    def select_action(self, obs: np.ndarray, deterministic: bool = False):
        obs_t = torch.as_tensor(np.asarray(obs, dtype=np.float32),
                                device=self.device)
        with torch.no_grad():
            action, logp, value = self.net.act(obs_t, deterministic=deterministic)
        return (action.cpu().numpy(), logp.cpu().numpy(),
                value.cpu().numpy().item())

    def value_of(self, obs: np.ndarray) -> float:
        with torch.no_grad():
            obs_t = torch.as_tensor(np.asarray(obs, dtype=np.float32),
                                    device=self.device)
            _, val = self.net(obs_t)
            return float(val.squeeze(-1).cpu().numpy().item())

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

    def save(self, path: str) -> None:
        torch.save({
            "obs_dim": self.obs_dim,
            "action_dim": self.action_dim,
            "device": self.device,
            "net_state": self.net.state_dict(),
            "optimizer_state": self.optimizer.state_dict(),
            "updates": self.updates,
        }, path)

    def load(self, path: str) -> None:
        data = torch.load(path, map_location=self.device)
        if data.get("obs_dim") != self.obs_dim or \
           data.get("action_dim") != self.action_dim:
            raise ValueError("checkpoint obs/action dims do not match env")
        self.net.load_state_dict(data["net_state"])
        self.optimizer.load_state_dict(data["optimizer_state"])
        self.updates = int(data.get("updates", 0))

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


class VectorEnv:
    """Vectorised wrapper around multiple identical ``AMRCollisionEnv``s.

    Episodes that finish are automatically re-seeded and reset (Stable-Baselines
    convention): parallel PPO gathers whole episodes without manual plumbing.
    The first environment is the visual "reference" env used for snapshots.
    """

    def __init__(self, env_factory, n_envs: int, base_seed: int = 0):
        self.envs = [env_factory(seed=base_seed + i) for i in range(n_envs)]
        self.n_envs = n_envs
        self._rs = np.random.RandomState(base_seed + n_envs * 997)

    def reset_all(self) -> np.ndarray:
        obs = []
        for e in self.envs:
            o, _ = e.reset(options={"seed": int(self._rs.randint(0, 2 ** 31 - 1))})
            obs.append(o)
        return np.asarray(obs, dtype=np.float32)

    def reset_env(self, idx: int) -> np.ndarray:
        o, _ = self.envs[idx].reset(
            options={"seed": int(self._rs.randint(0, 2 ** 31 - 1))})
        return np.asarray(o, dtype=np.float32)

    def step(self, actions: np.ndarray):
        obs_list, rew_list, note_list, info_list = [], [], [], []
        actions = np.asarray(actions, dtype=np.float32)
        for i, e in enumerate(self.envs):
            o, r, term, trunc, info = e.step(actions[i])
            if term or trunc:
                o = self.reset_env(i)
            obs_list.append(np.asarray(o, dtype=np.float32))
            rew_list.append(float(r))
            note_list.append((bool(term), bool(trunc)))
            info_list.append(info)
        return np.asarray(obs_list), np.asarray(rew_list), info_list

    def close(self) -> None:
        for e in self.envs:
            try:
                e.close()
            except Exception:
                pass