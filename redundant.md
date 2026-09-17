# Redundancy Analysis — evaluated against PROJECT.md's intended design

This document re-evaluates the earlier "redundancy" findings by comparing each
one against the **intended architecture** described in `PROJECT.md` rather than
judging them on structure alone.

Guiding principles taken from `PROJECT.md`:

> **Centralized observe / Distributed decide / Local execute.**
> - The central server *observes*: it owns the warehouse, announces tasks,
>   aggregates telemetry, commits auction winners. It must **not** choose who
>   works a task.
> - The robots *decide* collectively: distributed lowest-bid auction, tie-broken
>   deterministically, winner selection computed by every fleet member.
> - Each AMR *executes* locally: A* planning, state machine, return-to-charge.

A secondary project decision (documented in code, not PROJECT.md) is that the
Python fleet and the browser/JS layer ship **two implementations of the same
wire contract** so the Python and JS nodes interoperate on one Zenoh network.

## Verdict categories

| Tag | Meaning |
|---|---|
| ✅ INTENDED | Behaviour the architecture actually requires. Keep. |
| ⚠️ INTENDED-BUT-DRIFTED | The duplication has an intended purpose (cross-language parity), but the copies disagree or lack verification. Keep the purpose, fix the drift. |
| ❌ REDUNDANT | No intended purpose in the current design. Remove / consolidate. |

---

## 1. Cross-language mirrors (Python ↔ JS)

**Findings:** near-identical modules on both sides (A*, FleetAgent, task
management, warehouse builder, robot controller/node, coordinator/node, topics,
envelope).

**Analysis vs PROJECT.md:**

- PROJECT.md does **not** mandate two implementations; it describes **one**
  architecture (one fleet, Python-authoritative, JS dashboard as observer).
  The Python↔JS mirrors exist for **interoperability verification** — docstrings
  explicitly state "MUST mirror JS exactly", "makes ... compatible ... decisions".
  That is a legitimate research purpose, not accidental redundancy.
- HOWEVER, the *dashboard* behaviour changed: PROJECT.md §15 describes a
  dashboard that owns a warehouse editor and generation, but the current
  dashboard (`DistributedFleetState.js`) is a **read-only observer** that only
  mirrors the live Python fleet and issues `control/*` commands. The JS *fleet*
  (coordinator/robot node scripts + in-browser simulation) no longer serves any
  production role.

**Corrected verdict: ⚠️ INTENDED-BUT-DRIFTED**

- Keep: one canonical consumer-side mirror of the *wire contract* and of the
  deterministic decision rules that must agree for interop (topics, envelope,
  bid formula, winner tie-break, robot/task states, A* behaviour).
- **Contract test is the missing piece**: there is no automated diff between
  `agent.py ↔ FleetAgent.js`, `astar.py ↔ AStarPlanner.js`, `topics.py ↔ topics.js`.
  Add a parity test (same inputs → same outputs) so the mirror can't silently
  drift.
- Drop: any JS **fleet-runner** module whose live role is gone — but note
  `Simulation.js` is **not** in this bucket: it is the offline training sandbox
  (see §9), so it stays.
- Training implication: because the sandbox (`Simulation.js`/`MessageBus`) and
  the deployed Python fleet must behave the same, the parity test is not just
  good hygiene — it is what makes a policy trained offline in the sandbox valid
  on the live fleet (sim-to-real transfer of the *environment*, not the robot).

---

## 2. `syncTasks` task-status derivation — 4 copies

**Findings:** `Simulation.js:497`, `FleetCoordinator.js:375`,
`DistributedFleetState.js:339`, `task_manager.py:283`.

**Analysis vs PROJECT.md:**

- PROJECT.md assigns *observation* (derive task lifecycle from telemetry) to the
  **server/observer**. So the **Python `sync_tasks` is the intended authority**.
- `FleetCoordinator.js` (JS coordinator) is the JS alternative-fleet mirror — same
  status as §1, keep only as parity/test surface.
- `Simulation.js` is the **offline training sandbox** (see §9) — it must keep its own `syncTasks` replica so the sandbox is self-contained and websocket-free; keep it, but the parity rule applies to it too (it must produce the same task states as the python server).
- `DistributedFleetState.js` re-derives task status **in the viewer**. This is
  the critical one: the server derives the task ledger but **never publishes the
  authoritative result on any topic** (only TASK_NEW/ASSIGNED/CANCELLED/
  AUCTION_RESULT). Every consumer is therefore forced to re-implement the
  server's rule. That is not "intended redundancy" — it is a **contract gap**:
  the observer isn't broadcasting its own observation.

**Corrected verdict: ⚠️ INTENDED-BUT-DRIFTED (with a real gap)**

- Python `sync_tasks` = intended authoritative implementation.
- Add a broadcast of the server-derived task state (e.g. task status in
  `WORLD_STATE` or a dedicated topic) and have the dashboard consume it instead
  of re-deriving — this matches "server observes, dashboard views."
- Keep the `Simulation.js` copy (training sandbox) but gate every remaining copy
  (`FleetCoordinator.js`, `Simulation.js`) behind the same parity test so the
  sandbox cannot drift from the authoritative task rule.

---

## 3. Auction queue / result / retry / defer bookkeeping — 3 copies

**Findings:** `FleetCoordinator.js`, `Simulation.js`, `task_manager.py`.

**Analysis vs PROJECT.md:**

- PROJECT.md §4–§7: robots **run the auction**; the server only **observes and
  commits**. The queue/retry/defer semantics live with the observer's commit
  bookkeeping → the **Python `TaskManager` copy is the intended one**.
- `select_winner` / `selectWinner` appears **once per fleet member by design**
  (JS `FleetAgent.js:26`, Python `agent.py:45`): every robot runs the same
  deterministic winner rule so the distributed auction converges. This is the
  *core* P2P mechanism, **not** redundant — do not collapse it.
- `Simulation.js` keeps its auction-bookkeeping replica because it is the
  **self-contained offline training sandbox** (no websockets) — intended, but it
  must sit behind the same parity test as `FleetCoordinator.js`.
- `FleetCoordinator.js` is the JS observer mirror → parity surface only.

**Corrected verdict: ⚠️ INTENDED-BUT-DRIFTED**

- Keep the winner-selection duplication (it is the design).
- Keep the Python queue/retry bookkeeping as authority.
- Remove the redundancy between the **dashboard**'s re-derivation and the server's
  own ledger (§2); the remaining `Simulation.js` and `FleetCoordinator.js` copies
  are intended surfaces — one is the training sandbox, one is the JS fleet
  mirror — and both are bounded by the parity test.

---

## 4. Status enums (`ROBOT_STATUS` / `Status` / `RobotStatus` / `TaskStatus`)

**Findings:** `Robot.js:3`, `controller.py:26`, `models.py:18`, `models.py:29`,
with runtime comparison against raw string literals.

**Analysis vs PROJECT.md:**

- PROJECT.md §11 fixes the robot state machine states; the enum *names* are a
  contract shared across the Zenoh network, so one definition per language is
  **intended**.
- The real problem is **internal inconsistency**: `task_manager.py` and
  `server_node.py` compare raw `"COMPLETED"` / `"PICKING"` strings, so the
  Pydantic enums in `models.py` are documentation-only and never enforced; and
  Python defines the **same** states twice (`Status` in controller + `RobotStatus`
  in models). None of this drift is a design goal — it is unintended.

**Corrected verdict: ⚠️ INTENDED-BUT-DRIFTED**

- One canonical enum module *per language*; use it everywhere (no raw strings).
- A parity test asserts Python enum values == JS `ROBOT_STATUS`/`TASK_STATUS` values.
- Delete the duplicate Python definitions.

---

## 5. Battery models — 3 inconsistent sets

**Findings:** `battery.py` (drain 0.05/m, warn 25, critical 10; unused in prod),
`controller.py` (drain 0.02/m, inline warn 25), `agent.py` (warn 20, ≤10 -> no bid).

**Analysis vs PROJECT.md:**

- PROJECT.md never fixes battery numbers; battery is a local-execution detail.
  Nothing *intends* three different thresholds.
- The divergence directly breaks the mirrors' own goal ("compatible decisions"):
  Python bids `low_battery` at ≤10 while the controller auto-returns to charge at
  ~25, and JS battery logic drains at a different rate. Two implementations that
  disagree are **unintended drift**, not architecture.
- `battery.py` is genuinely dead code (only `test_battery.py` touches it).

**Corrected verdict: ⚠️ INTENDED-BUT-DRIFTED (plus dead code)**

- Adopt `battery.py` (or one equivalent) as the single Python model, wire it into
  `controller.py`/`agent.py`, delete the inline reimplementations.
- Enforce one agreed threshold set across Python and JS via the parity test.

---

## 6. Telemetry snapshot builder — 4–5 copies

**Findings:** `FleetAgent.js:39`, `FleetCoordinator.js:18`, `agent.py:321`,
`controller.py:80`, inline objects in `robot_node.js:60`.

**Analysis vs PROJECT.md:**

- PROJECT.md §9 defines telemetry content and says robots continuously broadcast
  it. Every producer naturally builds its own snapshot — the *shape* is the wire
  contract. Small builders living next to each producer is **acceptable** and
  closely matches the role split (robot builds its own; coordinator doesn't build
  telemetry for others).
- The only true waste is Python defining the schema in `models.TelemetryPayload`
  yet rebuilding dicts by hand (`controller.to_dict`, `agent._publish_telemetry`)
  without using it; and the redundant `telemetrySnapshot` in the JS coordinator.

**Corrected verdict: ✅ INTENDED (mostly), ❌ REDUNDANT (the rest)**

- Keep the per-robot builders (that is the design).
- Make builders *use* the shared schema (`models.py`) so validation is enforced.
- Drop the unused snapshot builder in the JS coordinator.

---

## 7. Shared constants (`MAX_AUCTION_*`, `MAX_RANDOM_TASKS`, `AUCTION_CONSTANTS`, `ROBOT_COLORS`, `DEFAULT_ROSTER`)

**Findings:** duplicated 2–3× across `Simulation.js`, `FleetCoordinator.js`,
`FleetAgent.js`, `task_manager.py`, `agent.py`, `DistributedFleetState.js`.

**Analysis vs PROJECT.md:**

- Values are currently **equal**, but no code guarantees they stay equal.
- `ROBOT_COLORS` and `DEFAULT_ROSTER` have no architectural purpose at all —
  pure UI/seed duplication.

**Corrected verdict: ⚠️ INTENDED-BUT-DRIFTED / ❌ REDUNDANT**

- Single source per language for the behavioural constants
  (`MAX_AUCTION_*`, `AUCTION_CONSTANTS`) with a cross-language parity test.
- Move `ROBOT_COLORS`/`DEFAULT_ROSTER` to one shared UI/config module ❌.

---

## 8. Obstacle `inflated_contains` — 3 copies

**Findings:** `controller.py:49` (`ObstacleRect`), `warehouse.py:38` (`Rect`),
`Obstacle.js` in JS.

**Analysis vs PROJECT.md:**

- Geometry helpers are pure plumbing with no defined role split; Python defines
  the same interval test twice in the same language.

**Corrected verdict: ❌ REDUNDANT**

- One rect/geometry module per language; controller imports `Rect` instead of
  redefining `ObstacleRect`.

---

## 9. Dead / released code

**Findings / corrected verdict:**

| Item | Verdict | Why |
|---|---|---|
| `backend/robot/battery.py` | ❌ REDUNDANT (or adopt) | Referenced only by `test_battery.py`; runtime battery logic lives in `controller.py`. PROJECT.md requires no separate module — either wire it in (favoured, see §5) or delete. |
| `dashboard/src/simulation/Simulation.js` (571 lines) | ✅ INTENDED | Project intent: an **offline training sandbox** for future robot neural nets, runnable without any websocket/Zenoh dependency via the in-memory `MessageBus` transport (`transport.js`, `Simulation.js:32`). Today its only consumer is `dashboard/test/warehouse-builder.test.mjs`, but the sandbox purpose justifies keeping it. **Gap:** a headless/deterministic training runner (e.g. `reset()`/`step()`/obs/act API, fixed seed, no DOM) is not wired up yet — currently the sim is only driven from a test and was historically driven by the dashboard, and `Simulation.step(dt)` is already clock-independent, so a headless runner is a small addition. |
| `models.py` `Task` / `TaskRecord` split | ❌ REDUNDANT | `models.Task` is documented "not transmitted on the wire" and duplicates `task_manager.TaskRecord`; Pydantic universe should model the wire contract only. |

---

## 10. Fleet Manager boilerplate & URL resolution

**Findings / corrected verdict:**

| Item | Verdict | Why |
|---|---|---|
| `routes.py` 15× start/stop/restart pairs + `_run_command` | ❌ REDUNDANT | Pure HTTP plumbing not described by PROJECT.md; a loop/registry registering routes per `(name, action)` removes it with no behaviour change. |
| `FleetControl.jsx` 3 hand-copied `ProcessRow` blocks + repeated `isBusy` predicates | ❌ REDUNDANT | Same-purpose JSX; drive from a config array (Infrastructure / Backend / AMRs) to remove the repetition. |
| `transport.js zenohUrlOf` vs `ConnectionManager.resolveDashboardUrl` (same env var + default) | ❌ REDUNDANT | Both resolve the Zenoh WS URL; consolidate into one shared module. |

---

## Corrected bottom line (what is genuine, what is intended)

**Intended — do not touch, but add a parity/contract test:**
- Distributed `select_winner` duplicated across fleet members (the P2P auction design).
- One mirror of the wire contract & decision rules per language (topics, envelope,
  bid formula, A*, status enums) — the interop/research goal.
- Telemetry built by each robot producer (observer/execute split).

**Intended but currently drifted — fix the drift (values or enforcement):**
- Battery thresholds/drain rates differ across `battery.py`, `controller.py`,
  `agent.py` and JS (§5).
- Status enums defined twice in Python and bypassed by raw string literals (§4).
- Server derives task status but never publishes it, forcing viewer re-derivation (§2).
- No automated assertion that the Python↔JS mirrors really match (§1–§5).

**Truly redundant — remove/consolidate:**
- `battery.py` as-is (either adopt it or delete), `models.Task`,
  `ObstacleRect` in `controller.py`, `telemetrySnapshot` in `FleetCoordinator.js`,
  `ROBOT_COLORS`/`DEFAULT_ROSTER` duplication (§5–§9).
- `routes.py` generator-able endpoints, `FleetControl.jsx` data-driven rows,
  duplicated Zenoh URL resolution (§10).
- Note: `Simulation.js` is **kept** — it is the offline training sandbox (§2, §9),
  as is its `syncTasks`/auction-bookkeeping copy, so it is not in this list.

Suggested order of work: (1) parity test for the mirrors (also guarantees a
policy trained in the sandbox transfers to the Python fleet), (2) server
broadcasts authoritative task state §2, (3) unify battery + enum sources §4–§5,
(4) add a headless/deterministic training runner around `Simulation.js` §9,
(5) collapse boilerplate §10.