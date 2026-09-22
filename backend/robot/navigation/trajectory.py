"""
trajectory.py — Deterministic, point-mass trajectory controller.

Applies kinematic limits (maximum speed, maximum tangential acceleration,
maximum angular acceleration) so the velocity vector changes smoothly even
under aggressive avoidance inputs. The output is the actual velocity that will
be applied to the robot for this tick.
"""

from __future__ import annotations
import math

from robot.navigation.interfaces import TrajectoryController, NavContext

MAX_ACCEL = 2.0                  # m/s^2 tangential
MAX_ANGULAR_ACCEL = 5.0          # rad/s^2


def _clamp(value, limit):
    if limit <= 0:
        return value
    return max(-limit, min(limit, value))


class AlgorithmicTrajectoryController(TrajectoryController):
    """Apply acceleration and angular-rate limits to a desired velocity."""

    def __init__(self, state, log):
        self.state = state
        self._log = log

    def step(self, ctx: NavContext, desired_vx, desired_vy, dt: float) -> tuple:
        s = self.state
        vx = float(desired_vx)
        vy = float(desired_vy)
        if dt <= 1e-9:
            return (s.vx if hasattr(s, 'vx') else 0.0,
                    s.vy if hasattr(s, 'vy') else 0.0,
                    s.speed,
                    s.heading)

        # Current velocity (seeded from the state on the very first tick if
        # the new pipeline fields are not yet populated).
        cur_vx = getattr(s, 'vx', 0.0)
        cur_vy = getattr(s, 'vy', 0.0)

        # Tangential acceleration limit.
        dvx = _clamp(vx - cur_vx, MAX_ACCEL * dt)
        dvy = _clamp(vy - cur_vy, MAX_ACCEL * dt)
        vx = cur_vx + dvx
        vy = cur_vy + dvy

        # Angular rate limit — only meaningful if current speed > 0.
        cur_speed = math.hypot(cur_vx, cur_vy)
        desired_angle = math.atan2(vy, vx)
        cur_angle = math.atan2(cur_vy, cur_vx)
        if cur_speed > 0.01:
            # Convert angular acceleration to a maximum angle change that
            # scales with the current speed (wider turns at higher speed).
            max_angle = MAX_ANGULAR_ACCEL * dt * (0.25 / max(cur_speed, 0.25))
            angle_diff = desired_angle - cur_angle
            angle_diff = (angle_diff + math.pi) % (2 * math.pi) - math.pi
            clamped_diff = _clamp(angle_diff, max_angle)
            new_angle = cur_angle + clamped_diff
        else:
            new_angle = desired_angle

        speed = math.hypot(vx, vy)
        if speed > s.max_speed:
            vx = vx / speed * s.max_speed
            vy = vy / speed * s.max_speed
            speed = s.max_speed
            new_angle = math.atan2(vy, vx)

        return (vx, vy, speed, new_angle)