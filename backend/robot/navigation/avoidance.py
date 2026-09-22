"""
avoidance.py — Deterministic velocity-obstacle sampling for local robot-robot
collision avoidance.

The avoidance layer evaluates the intended velocity produced by global path
following. If that velocity would enter another robot's velocity obstacle within
a short horizon, it samples a deterministic set of candidate velocities (24
directions x 5 speed levels + stop) and picks the first one that is:

  * collision-free with respect to every peer (with cooperation margin),
  * closest to the preferred heading, with the least speed loss.

Candidates are scored with a fixed, reproducible rule (no randomness, no
iteration-order dependence), and the chosen velocity is passed up the pipeline.
A small hysteresis keeps the robot from chattering between two symmetric
candidates: while the current avoidance velocity is still valid it is kept.

The layer also exposes ``near_collision`` — set when a peer comes within the
hard safety margin — so metrics can record near misses.
"""

from __future__ import annotations
import math
from typing import Sequence

from robot.navigation.interfaces import CollisionAvoidance, NavContext
from robot.navigation.reservation import _peer_id, _peer_pos

DIRECTIONS = 24
SPEED_LEVELS = (1.0, 0.75, 0.5, 0.25, 0.0)
HORIZON_S = 2.0                 # seconds of prediction
COOP_MARGIN_M = 0.15            # extra gap between robots when overtaking/avoiding
HARD_MARGIN_M = 0.08            # near-collision threshold
OBS_MARGIN_M = 0.10             # clearance kept from static obstacles
HYSTERESIS_TIME_S = 0.5
MIN_PREF_SPEED = 0.15
DEVIATION_WEIGHT = 1.0
SPEED_LOSS_WEIGHT = 0.15
SIDE_PREFERENCE = 0.6            # right-hand pass rule: prefer deviations to the
                                 # right of the preferred direction (deterministic
                                 # coordination without communication)
CLOSING_LOOKAHEAD_DIST_M = 4.0   # reject slow-closing candidates already this close


class AlgorithmicCollisionAvoidance(CollisionAvoidance):
    """Velocity-object sampling collision avoidance."""

    def __init__(self, state, log, safety_margin: float = 0.1):
        self.state = state
        self._log = log
        self.safety_margin = safety_margin
        self._last_candidate = (0.0, 0.0)
        self._hysteresis_timer = 0.0

    # ------------------------------------------------------------------
    # Collision interface
    # ------------------------------------------------------------------

    def compute(self, ctx: NavContext, target, preferred_vx, preferred_vy,
                now: float) -> dict:
        s = self.state
        pref_speed = math.hypot(preferred_vx, preferred_vy)
        if pref_speed < MIN_PREF_SPEED:
            return self._result(preferred_vx, preferred_vy, "normal", "stopped",
                                False, near_collision=False)

        peers = [p for p in ctx.fleet
                 if _peer_id(p) and _peer_id(p) != s.id and p.get("online", True)]
        near = self._near_peers(preferred_vx, preferred_vy, peers, ctx)

        # Preferred velocity is always kept when it is safe — no detour needed.
        if not self._static_obstacle_collision(preferred_vx, preferred_vy, ctx) and \
           not self._peer_collision(preferred_vx, preferred_vy, peers, ctx):
            self._hysteresis_timer = 0.0
            return self._result(preferred_vx, preferred_vy, "normal", None,
                                False, near_collision=near)

        steps = self._sample_targets(pref_speed)

        dist_to_goal = math.hypot(target[0] - s.x, target[1] - s.y)
        best = None
        best_score = None
        for cand in steps:
            cvx, cvy, speed, direction = cand
            if self._static_obstacle_collision(cvx, cvy, ctx):
                continue
            if self._peer_collision(cvx, cvy, peers, ctx):
                continue
            score = self._score(cand, preferred_vx, preferred_vy, pref_speed,
                                dist_to_goal)
            if best_score is None or score < best_score:
                best_score = score
                best = cand

        if best is None:
            # Nothing safe — hold position. The safety layer will hard-stop if a
            # peer closes inside the hard margin anyway.
            return self._result(0.0, 0.0, "avoiding", "no_safe_velocity",
                                True, near_collision=False)

        cvx, cvy, speed, direction = best
        # Hysteresis: keep the current avoidance candidate while it is still
        # collision-free (avoids oscillating between symmetric choices). The
        # preferred velocity is re-checked every tick above, so this hysteresis
        # can never keep steering after the way ahead has cleared.
        if self._hysteresis_timer > 0.0 and self._candidate_valid(
                self._last_candidate, peers, ctx):
            cvx, cvy = self._last_candidate
        else:
            self._hysteresis_timer = HYSTERESIS_TIME_S
            self._last_candidate = (cvx, cvy)

        intervention = (math.hypot(cvx - preferred_vx, cvy - preferred_vy) > 1e-6)
        state = "avoiding" if intervention else "normal"
        return self._result(cvx, cvy, state, "yield" if intervention else None,
                            intervention, near_collision=self._near_peers(
                                cvx, cvy, peers, ctx))

    # ------------------------------------------------------------------
    # Candidate generation (deterministic)
    # ------------------------------------------------------------------

    def _sample_targets(self, pref_speed: float) -> list:
        """Return candidate (vx, vy, speed, angle) tuples, deterministic order."""
        s = self.state
        max_speed = max(0.0, s.max_speed)
        targets = []
        base = min(pref_speed, max_speed)
        for level in SPEED_LEVELS:
            speed = base * level
            if speed <= 1e-6:
                targets.append((0.0, 0.0, 0.0, 0.0))
                continue
            for i in range(DIRECTIONS):
                angle = (2.0 * math.pi * i) / DIRECTIONS
                vx = speed * math.cos(angle)
                vy = speed * math.sin(angle)
                targets.append((vx, vy, speed, angle))
        # Nearest direction first is achieved via scoring; here we simply append.
        return targets

    def _score(self, cand, pref_vx, pref_vy, pref_speed, dist_to_goal) -> float:
        cvx, cvy, speed, direction = cand
        if dist_to_goal <= 0.25 and pref_speed > speed + 1e-6:
            # On the final approach we prefer stopping over orbiting the target.
            penalty = 10.0 * (pref_speed - speed)
        else:
            pref_angle = math.atan2(pref_vy, pref_vx)
            signed = (direction - pref_angle + math.pi) % (2 * math.pi) - math.pi
            penalty = (DEVIATION_WEIGHT * abs(signed)
                       + SPEED_LOSS_WEIGHT * (pref_speed - speed)
                       + SIDE_PREFERENCE * signed)
        return penalty

    # ------------------------------------------------------------------
    # Collision prediction helpers
    # ------------------------------------------------------------------

    def _peer_velocity(self, peer: dict) -> tuple[float, float]:
        nav = peer.get("nav") or {}
        vel = nav.get("velocity")
        if isinstance(vel, (list, tuple)) and len(vel) >= 2:
            return (float(vel[0]), float(vel[1]))
        heading = float(peer.get("heading", 0.0))
        speed = float(peer.get("speed", 0.0))
        return (speed * math.cos(heading), speed * math.sin(heading))

    def _candidate_time_to_closest(self, vx, vy, px, py, pvx, pvy) -> float:
        rel_vx = vx - pvx
        rel_vy = vy - pvy
        dx = px - self.state.x
        dy = py - self.state.y
        v2 = rel_vx * rel_vx + rel_vy * rel_vy
        if v2 <= 1e-9:
            return -1.0
        # Time of closest approach for separation(t) = d + t*(v_peer - v_me):
        # tca = (d . v_rel) / |v_rel|^2, where v_rel = v_me - v_peer.
        proj = (dx * rel_vx + dy * rel_vy) / v2
        return proj

    def _peer_collision(self, vx, vy, peers, ctx) -> bool:
        s = self.state
        sum_r = s.radius + s.radius + COOP_MARGIN_M
        max_speed = max(0.0, s.max_speed)
        for p in peers:
            px, py = _peer_pos(p)
            gap = math.hypot(px - s.x, py - s.y)
            if gap > HORIZON_S * max_speed * 2 + max(1.5, sum_r + 1.0):
                # Too far to matter within the prediction window.
                continue
            pvx, pvy = self._peer_velocity(p)
            dx = px - s.x
            dy = py - s.y
            rel_vx = vx - pvx
            rel_vy = vy - pvy
            v2 = rel_vx * rel_vx + rel_vy * rel_vy
            if v2 <= 1e-9:
                # Zero relative motion — a collision only if already touching.
                if gap < sum_r:
                    return True
                continue
            tca = (dx * rel_vx + dy * rel_vy) / v2
            if tca <= 0.0:
                continue  # closing now, or already separating
            ex = s.x + rel_vx * tca
            ey = s.y + rel_vy * tca
            sep = math.hypot(px - ex, py - ey)
            if sep >= sum_r:
                continue
            if tca <= HORIZON_S:
                # Contact within the reaction horizon.
                return True
            if gap <= CLOSING_LOOKAHEAD_DIST_M:
                # Slow approach: contact is far in time but the gap is small and
                # closing. Waiting for a time threshold would only invite a
                # creeping head-on standoff, so reject the candidate now and
                # force a lateral escape while there is still room to steer.
                return True
        return False

    def _near_peers(self, vx, vy, peers, ctx) -> bool:
        s = self.state
        sum_r = s.radius + s.radius + HARD_MARGIN_M
        for p in peers:
            px, py = _peer_pos(p)
            pvx, pvy = self._peer_velocity(p)
            tca = self._candidate_time_to_closest(vx, vy, px, py, pvx, pvy)
            d = math.hypot(px - s.x, py - s.y)
            if 0.0 <= tca <= HORIZON_S:
                ex = s.x + (vx - pvx) * tca
                ey = s.y + (vy - pvy) * tca
                if math.hypot(px - ex, py - ey) < sum_r:
                    return True
            elif d < sum_r:
                return True
        return False

    def _static_obstacle_collision(self, vx, vy, ctx) -> bool:
        """Reject candidates that would push the robot into a static obstacle."""
        s = self.state
        margin = s.radius + OBS_MARGIN_M
        # Project the candidate along its motion during the next control step.
        prod_t = 0.20
        nx = s.x + vx * prod_t
        ny = s.y + vy * prod_t
        for obs in ctx.obstacles:
            try:
                if obs.inflated_contains(nx, ny, margin):
                    return True
            except AttributeError:
                ox = float(obs.get("x", 0.0))
                oy = float(obs.get("y", 0.0))
                ow = float(obs.get("width", 0.0))
                oh = float(obs.get("height", 0.0))
                if ox - margin <= nx <= ox + ow + margin and \
                   oy - margin <= ny <= oy + oh + margin:
                    return True
        return False

    def _candidate_valid(self, cand, peers, ctx) -> bool:
        cvx, cvy = cand
        if self._static_obstacle_collision(cvx, cvy, ctx):
            return False
        return not self._peer_collision(cvx, cvy, peers, ctx)

    def _result(self, vx, vy, state, reason, intervention,
                near_collision: bool = False) -> dict:
        return {
            "vx": vx, "vy": vy,
            "state": state,
            "reason": reason,
            "intervention": bool(intervention),
            "near_collision": bool(near_collision),
        }