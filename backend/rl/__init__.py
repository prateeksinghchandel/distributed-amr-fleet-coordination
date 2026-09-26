"""rl — Reinforcement-learning training environment for AMR collision avoidance.

Isolated from the live Zenoh fleet. Imports the existing algorithmic core
(MotionController, AStar planners, safety, geometry) as a shared simulation
core and wraps it in a Gymnasium environment plus a PPO trainer.
"""

__all__ = ["scenarios", "env", "rl_policy", "trainer", "server"]