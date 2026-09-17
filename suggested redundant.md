Here's my redundancy analysis. I read the backend, dashboard JS, and tests. The project deliberately maintains two mirrored implementations (Python backend + JS dashboard) which is the dominant source of duplication, but there are also several within-language redundancies and dead code.
1. Cross-language mirrors (Python ↔ JS) — the biggest source
Entire modules are near-identical copies kept in sync manually. Docstrings even say "Mirrors X exactly":
Python	JS mirror
backend/robot/planning/astar.py	dashboard/src/simulation/planning/AStarPlanner.js (same inflation, moves, smoothing, ~identical code)
backend/robot/agent.py	dashboard/src/simulation/fleet/FleetAgent.js (bid formula, round state, constants)
backend/server/task_manager.py	dashboard/src/coordinator/FleetCoordinator.js + dashboard/src/simulation/Simulation.js
backend/server/warehouse.py	dashboard/src/simulation/WarehouseBuilder.js + Warehouse.js (presets + builder algorithm line-for-line)
backend/robot/controller.py	dashboard/src/simulation/Robot.js (state machine, waypoint following)
backend/robot/robot_node.py	dashboard/src/robot/robot_node.js (main loop, subscriptions)
backend/server/server_node.py	dashboard/src/coordinator/coordinator_node.js
backend/common/topics.py	dashboard/src/simulation/messages/topics.js
backend/robot/communication.py (ZenohBus envelope)	dashboard/src/distributed/envelope.js
Since the Python side is now the authoritative live fleet (per REPORT.md), the JS coordinator/robot/simulation copies only serve tests and the (now read-only) dashboard. Change: keep the JS side as a thin types-only contract (topics + payload shapes) and delete or freeze the JS domain mirrors (Simulation.js, WarehouseBuilder.js, AStarPlanner.js, JS FleetAgent/FleetCoordinator execution logic). If the cross-language sync must stay, add a codegen/contract test that diffs the two implementations.
2. Triple/quadruple duplication within a single language
- Task-status derivation (syncTasks) is copy-pasted 4×: Simulation.js:497, FleetCoordinator.js:375, DistributedFleetState.js:339 (_syncTaskStatuses), and task_manager.py:283 (sync_tasks). Extract one shared helper per language (or derive dashboard status purely from TASK_ASSIGNED/telemetry instead of re-deriving).
- Auction queue/result handling (advanceAuction, handleAuctionResult, recordAuction, setAuctionEnabled, reconsiderWaitingTasks) is duplicated in FleetCoordinator.js, Simulation.js, and task_manager.py — near verbatim. Same for assignTask/cancelTask.
- Status enums: ROBOT_STATUS in Robot.js, Status in controller.py, RobotStatus/TaskStatus in models.py all define the same strings; worse, most runtime code compares raw string literals ("COMPLETED", "PICKING") instead of the enums, so the enums are documentation-only (task_manager.py:134, server_node.py). Unify on one canonical enum.
- Battery models are inconsistent and split across 3 files: battery.py (drain 0.05/m, charge 8/s, warn 25, critical 10 — but unused in prod), controller.py (drain 0.02/m, inline charge 8/s, inline warn 25), and agent.py (warn 20, ≤10 ineligible). Single source of truth + use one.
- Telemetry snapshot builder appears 4–5×: FleetAgent.js:39 (telemetryOf), FleetCoordinator.js:18 (telemetrySnapshot), agent.py:321 (_publish_telemetry), controller.py:80 (RobotState.to_dict), plus the inline objects in robot_node.js:60. One shared schema (already exists in models.py) would do.
- Constants duplicated across files: MAX_AUCTION_RETRIES/MAX_AUCTION_HISTORY/MAX_RANDOM_TASKS in Simulation.js, FleetCoordinator.js, task_manager.py; AUCTION_CONSTANTS in FleetAgent.js + agent.py; ROBOT_COLORS in Simulation.js:17 + DistributedFleetState.js:26; DEFAULT_ROSTER/spawn coords in FleetCoordinator.js:12 + Simulation.js:255.
- Obstacle "inflated_contains" reimplemented in controller.py:49 (ObstacleRect) and warehouse.py:38 (Rect) in Python, plus Obstacle.js in JS — the Python one arguably subsumes the other.
3. Dead / unused code
- backend/robot/battery.py — BatteryState is referenced only by test_battery.py; production robot logic lives inside controller.py. Either wire it in or delete it.
- dashboard/src/simulation/Simulation.js (571 lines) — only imported by dashboard/test/warehouse-builder.test.mjs; the dashboard now runs on DistributedFleetState. Large dead module duplicating FleetCoordinator.js.
- models.py:149 Task — "internal task record (not transmitted)" duplicates task_manager.TaskRecord; only tests use it.
4. Repetitive boilerplate
- routes.py — 15 near-identical start/stop/restart route pairs (zenoh, bridge, coordinator, per-AMR) plus a 15-route pattern in _run_command. Collapse via a loop/generator registering routes for each endpoint.
- FleetControl.jsx — 3 hand-written ProcessRow blocks (Infrastructure/Bridge, Backend, per-AMR) and repeated isBusy predicates; a data-driven config array would remove ~100 lines.
- Zenoh URL resolution duplicated: transport.js:31 (zenohUrlOf) and ConnectionManager.js:27 (resolveDashboardUrl) both read AMR_ZENOH_URL/same default. Consolidate into one module.