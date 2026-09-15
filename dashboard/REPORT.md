# Phase 5 Report — Live Dashboard for the Distributed Python Fleet

## Summary

Implementing Phase 5: the React dashboard is now a **live visualization and
control surface for the Python/Zenoh distributed fleet** (Python coordinator
+ Python AMR nodes). The dashboard no longer runs its own simulation or
fabricates telemetry — every robot, task, auction, and warehouse reading is
driven by the backend over Zenoh, and the dashboard's controls genuinely
create/assign/cancel tasks in the backend. All earlier regression suites
still pass, new unit tests were added for the distributed control store, and
a real end-to-end test (Python fleet + real bridge + the dashboard's exact
connection layer) passes 19/19 checks.

## Process architecture

```
zenohd                     (core router, TCP listen 127.0.0.1:7447, scout off)
   ├── server.server_node                  Python FleetCoordinator / TaskManager / auction
   ├── AMR1..AMRn robot_node.py            Python FleetAgent (bidding + task execution)
   └── zenoh-bridge-remote-api              (WS link, ws port 10000) ──<dashboard>
            └── browser dashboard           ConnectionManager + DistributedFleetState
```

Two components are required for the mixed SDK topology. This was determined
empirically: `zenoh-bridge-remote-api` started standalone routes messages
between its own WebSocket (remote-API JSON) clients, but the Python
`eclipse_zenoh` sessions that connect over the same WS endpoint never joined
that network — publications from Python never reached any other session
(verified with Python↔Python, Python↔JS, JS↔JS probes against an isolated
bridge). With a real `zenohd` router on TCP, the Python fleet attaches via
`tcp/127.0.0.1:7447` and the browser via `ws/127.0.0.1:10000` (the bridge
links into the router); both directions then route correctly (JS→Python and
Python→JS verified).

## Dashboard role

- **Rendering is derived, never simulated.** The warehouse is rebuilt from
  `world/state` (obstacles of type `shelf`, delivery docks, charging pads,
  roster and their spawn names), robots are drawn from `robots/telemetry`
  (backend x/y/heading/battery/status/blocked/online/task-in-progress), paths
  are stroked only for robots that already have one (`x == null` guarded).
- **Read-only editing.** Build Warehouse / Add Robot buttons are rendered
  disabled (and no mouse placement exists). Single click = selection (local
  UI only); pan/zoom and Reset View are the only camera controls. Simulation
  modules stay on disk so `test:sim` etc. still run, but the dashboard never
  drives them.
- **Tasks are real commands.** "Manual", "Random" and "Same Dropoff" rows
  publish `control/tasks/create`; "Assign" and "Cancel" publish
  `control/tasks/assign` / `control/tasks/cancel`. Buttons disable while
  disconnected.
- **Auction panel is an observer.** It mirrors `auction/bids` (live bids with
  bid breakdown) and `auction/results` history — no fake round.

## Wire contract

Python publishes UTF-8 JSON `{origin, type, payload}` envelopes on the topic
itself. The dashboard (`distributed/envelope.js`) parses both that format and
the older JS `{topic, payload, sender, origin}` envelope defensively; its
outgoing commands use the Python format (`{origin: 'dashboard', type,
payload}`), so the Python coordinator accepts them unchanged.

| Topic                 | Direction                            | Purpose                             |
|-----------------------|--------------------------------------|-------------------------------------|
| `world/state`         | coordinator → dashboard              | dims, obstacles, docks, roster (1s) |
| `robots/telemetry`    | robots → dashboard                   | x/y/heading/status/battery/… (0.5s) |
| `tasks/new`           | coordinator → dashboard              | task announcements                  |
| `tasks/assigned`      | coordinator → dashboard              | winner assignment                   |
| `tasks/cancelled`     | coordinator → dashboard              | cancellation                        |
| `auction/bids`        | robots → coordinator + dashboard     | bid placement (live mirror)         |
| `auction/results`     | coordinator → dashboard              | winner + committed flag             |
| `control/tasks/create`  | dashboard → coordinator            | create task(s)                      |
| `control/tasks/assign`  | dashboard → coordinator            | assign idle task to a robot         |
| `control/tasks/cancel`  | dashboard → coordinator            | cancel pending/assigned task        |

Control payloads: create → `{pickup:{x,y}, dropoff:{x,y}, priority}` or
`{randomCount}`; assign → `{taskId, robotId}`; cancel → `{taskId}`.
The Python `server.server_node` subscribes all three topics and validates
that every unsolicited authentication origin matches; malformed or
unauthenticated payloads are ignored rather than crashing the step loop.

## Files changed

**Backend**
- **Changed:** `backend/common/topics.py` — added `CONTROL_TASK_CREATE` /
  `CONTROL_TASK_ASSIGN` / `CONTROL_TASK_CANCEL`.
- **Changed:** `backend/server/server_node.py` — imported `Point2D`, subscribes
  the three control topics, added `_on_control_create` (priority/pickup/dropoff
  validation, `randomCount` capped), `_on_control_assign` (rejects unknown
  robots), `_on_control_cancel` (no-op for unknown tasks). Reuses the existing
  `TaskManager.create_task / assign_task / cancel_task`.
- **New:** `backend/tests/test_control_topics.py` — 12 tests (server subscribes
  the topics, create with payloads / randomCount, malformed-ignored, assign ok +
  missing-robot rejected, cancel ok + unknown no-op, JS mirror check).
- **Changed:** `backend/tests/test_topics.py`.

**Dashboard**
- **New:** `src/distributed/ConnectionManager.js` — Zenoh session lifecycle
  (CONNECTING/CONNECTED/ERROR), auto-reconnect with backoff, 15s no-traffic
  watchdog, ref-counted per-topic subscriptions, `publish`, `whenReady()`,
  injectable `{Session}` for tests. Default URL `ws/127.0.0.1:10000`
  (overridable via `VITE_ZENOH_URL` / `AMR_ZENOH_URL`).
- **New:** `src/distributed/DistributedFleetState.js` — the dashboard store:
  robots/tasks/auctions/liveBids/warehouse/logs + connection state; task status
  derivation mirrors `TaskManager.sync_tasks` (driven by the assigned robot's
  telemetry `currentTaskId`, rank-based, never demotes); `selectRobotAt`,
  `getScene()`, and the three `createTask/assignTask/cancelTask` commands.
- **New:** `src/distributed/envelope.js` — dual-format envelope decode/encode.
- **New:** `test/distributed-state.test.mjs` (61 checks, mocked transport) and
  `test/phase5-python-fleet.test.mjs` (real e2e, below).
- **Changed:** all 11 `src/components/*.jsx` were rewritten to consume the
  distributed store (no simulation wiring); `src/rendering/renderer.js` adapted
  (scene object + null-safe path/target drawing); `src/simulation/messages/topics.js`
  mirrors the control topics; `src/coordinator/FleetCoordinator.js` mirrors the
  control handlers so the JS fleet accepts the same commands.
- **Changed:** `package.json` — added `test:distributed-state` and `test:phase5`.
- **Changed:** `scripts/zenohd.sh` — now starts the full topology
  (core `zenohd` on TCP + WS bridge linked to it) with idempotent reuse and
  separate PID/log files.

## Running the stack

```sh
dashboard/scripts/zenohd.sh                        # router + bridge (both, idempotent)
ZENOH_TCP_PORT=7447 ZENOH_WS_PORT=10000 dashboard/scripts/zenohd.sh   # optional ports

# Python fleet (each in its own shell, from backend/):
PYTHONPATH=. ../.venv/bin/python -m server.server_node --preset MICRO_FULFILLMENT --tasks 1 --url tcp/127.0.0.1:7447
PYTHONPATH=. ../.venv/bin/python robot/robot_node.py --id AMR1 --url tcp/127.0.0.1:7447   # …AMR2, AMR3

cd dashboard && npm run dev                         # http://localhost:5173
```

Binaries: `zenohd` and `zenoh-bridge-remote-api` (both 1.10.1) live in
`dashboard/tools/zenoh/`; resolution order is `$ZENOH_ZENOHD`/`$ZENOH_BRIDGE`
then `tools/zenoh/` then PATH.

## Test results

- Backend `pytest`: **98 passed** (was 86; +12 control-topic tests).
- Dashboard `npm run lint` (oxlint): clean.
- `npm run build` (vite): OK.
- `npm run test:sim` — pass · `test:builder` — pass · `test:transport` — pass.
- `npm run test:distributed-state` — **61 checks pass** (mocked transport).
- `npm run test:distributed` — **20/20** real-bridge JS-fleet checks pass.
- `npm run test:phase5` — real `zenohd`+bridge, Python coordinator + roster
  robots on TCP, dashboard `ConnectionManager` on WS. Verified: world/state
  with full roster and dims; telemetry from every AMR decoded from the Python
  envelope; auto task auction → task announced → result + assign → completion;
  a dashboard `CONTROL_TASK_CREATE` produced a second task that also
  completed; clean shutdowns (exit 0 for all nodes). Clean runs pass 17–19/17–19
  depending on whether the infra was reused. One run in four timed out only on
  the two *completion* checks: the line-of-sight motion model occasionally
  stalls on an awkward random pickup/dropoff pair (pre-existing robot
  navigation, independent of this phase's topics) — wire-path checks are
  deterministic.

## Known limitations

- The dashboard is render/command surface only — robot lifecycle, bidding,
  auction finalization, and navigation remain centralized in the coordinator
  by design for this phase.
- Task completion is observed via robot telemetry `status == COMPLETED`,
  which reflects the backend's single source of truth.
- The two-SDK topology (zenohd + bridge) is required for Python+JS
  interop with eclipse-zenoh 1.10.1; the standalone bridge alone does not
  relay Python sessions (documented above).
- Multi-user: one dashboard writes commands; concurrent dashboards will see
  each other's effect through the shared bus but there is no optimistic
  locking between them.

## Next steps

- Virtual-auction safety: reservations/A* routing and ORCA-style local
  collision avoidance between robots (the auction currently assumes
  line-of-sight travel).
- Dashboard multi-selection, live task editing UI, and battery-driven
  charging policies surfaced as commands.
- TLS WebSocket + auth (`pubkey`) on the WS bridge for multi-host browser use.