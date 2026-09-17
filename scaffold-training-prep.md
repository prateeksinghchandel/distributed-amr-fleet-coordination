You're already 80% there. test_phase2_integration.py proves the whole fleet (server + 3 agents + controllers) runs in-process with no Zenoh — just a FakeBus. And because deploy = Python, training environments can reuse the exact production modules (controller.py, agent.py, astar.py, task_manager.py, warehouse.py). That means no sim-to-real transfer problem at all.
Key insight on timing (why this is easy)
The clock is already injectable:
- MotionController.update(dt, obstacles, bounds) — pure step, no time.time() inside.
- FleetAgent.tick(dt, now) and on_task_new(payload, timestamp) take now from the caller (the node passes time.time(); you pass a virtual clock instead).
- The only time.time() leaks are cosmetic (TaskRecord.created_at/completed_at in task_manager.py).
So a training env = the existing modules + an in-memory bus + a virtual clock. No refactoring of the robots' logic.
Phased plan
Phase 1 — headless, deterministic FleetEnv, no NN yet
- backend/training/clock.py — trivial virtual clock.
- backend/training/bus.py — a FakeBus-style in-memory bus (synchronous delivery is faithful to deployed Python, which has no delays — the delays only exist in the JS MessageBus).
- backend/training/env.py — FleetEnv that, on reset(seed): builds a seeded build_from_preset(...) layout, spawns N RobotState+MotionController, runs the coordinator core in-process (TaskManager + server FleetAgent, exactly like the test), then step(dt=0.05) advances virtual time → deliver bus → controller.update → agent.tick → route telemetry → sync_tasks → auction advance.
- Goal: reproducible end-to-end fleet runs (auction → assign → A* → pickup/dropoff → complete) with a fixed seed. This is your training/eval substrate.
Phase 2 — the one design decision that matters: which layer is the NN?
Pick one action space, they need very different architectures:
Layer	NN action	Pros / cons
A. Learned bidder (recommended)	bid scalar / eligibility for an auction	Tiny action space, sample-efficient, safe; deterministic A* + state machine stay as the safety layer (matches PROJECT.md §14). Multi-agent handled by fixed heuristic opponent agents.
B. Learned motion	target waypoint / velocity toward it	Continuous control, far more samples; MotionController left as constraint layer.
C. Task/charge decisions	accept task? return to charge?	Discrete, easy first milestone; good for curriculum.
For your research this maps perfectly onto PROJECT.md's "deterministic safety + AI optimization layer": I'd start with A/C (the distributed decide layer — that's the actual contribution) and defer B.
Phase 3 — plug the policy in + train
- Ego robot's compute_bid calls policy.act(obs); opponents stay heuristic FleetAgents. Winner selection stays deterministic on all sides.
- obs: normalized A* path cost to pickup+dropoff, battery, peer congestion/positions, queue depth. reward: sparse (+ on task complete, - battery-depleted/stall), then shape.
- Stack: pip install gymnasium torch stable-baselines3 in a separate requirements-train.txt (keep runtime deps lean). PPO + make_vec_env for parallel warehouses; curriculum from 1-2 tasks → full load.
- Evaluate vs. the heuristic FleetAgent on held-out seeds.
Deploy: the trained bidder becomes a callable in robot_node.py's FleetAgent.compute_bid — same Python, no port, no parity test needed. The JS Simulation.js can then stay purely as a visualization of the live fleet.
Note: because training uses random.* heavily (warehouse.random_pick_point, random.choice), seed every source per env. Also add the now_fn param to TaskManager so created_at/completed_at use virtual time.