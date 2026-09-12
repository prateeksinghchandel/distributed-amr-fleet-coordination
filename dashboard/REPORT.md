# Stage Report — Distributed AMR Fleet Coordination

## Summary

Implemented Stage 4: the fleet (1 coordinator + 3 robots) now runs as
independent Node.js processes coordinated over a real Zenoh router
(`zenoh-bridge-remote-api`), replacing the in-process message bus for the
distributed path. The in-browser `Simulation`/dashboard is unchanged and all
previous regression suites still pass.

## Process architecture

```
zenoh-bridge-remote-api             (router, ws port 10000, multicast scout off)
   ├── coordinator_node.js          FleetCoordinator  — sole auction finalizer
   ├── AMR1..AMR3 robot_node.js     FleetAgent        — bidding + task execution
   └── (dashboard browser, optional, same bus)
```

- Coordinator only: announces pending tasks, collects bids, commits the
  winner (`coordinatorCommit → assignTask`), dedupes via
  `syncTasks(telemetry)`, queues/retries with the same round/retry semantics
  as the in-memory stage. Winner notification goes out through
  `auction/results` with the committed flag set.
- Robots: all decisions preserved (bid price, min-bid selection, no
  finalize), `disableFinalize: true` so only the coordinator commits.
- Everything is addressed by the transport id, which is now unique per
  process (UUID-based). A `Date.now()`+counter id collided when coordinator
  and a robot spawned in the same millisecond, so the robot filtered the
  coordinator's `world/state` as its own echo and silently received nothing —
  the source of the intermittent flake. Fixed; 14/14 consecutive distributed
  runs pass.

## Topics (all JSON envelopes with `origin`/`type`)

| Topic                  | Direction               | Purpose                  |
|------------------------|-------------------------|--------------------------|
| `tasks/new`            | coordinator → fleet     | auction announcement     |
| `tasks/assigned`       | coordinator → winner    | winner assignment        |
| `tasks/cancelled`      | coordinator → fleet     | cancellation             |
| `auction/bids`         | robots → coordinator    | bid placement            |
| `auction/results`      | coordinator → fleet     | winner + committed flag  |
| `robots/telemetry`     | robots → coordinator    | position/status, 0.5s    |
| `world/state` (NEW)    | coordinator → fleet     | dims, obstacles, roster  + spawn points, 1s heartbeat |

Only `world/state` is new; all other topics are unchanged so the browser
simulation and the distributed fleet share the exact same message contract.

## Files changed

- **New:** `src/coordinator/FleetCoordinator.js`, `src/coordinator/coordinator_node.js`
- **New:** `src/robot/robot_node.js`
- **New:** `src/simulation/messages/transport.js` (transport factory + `remoteQueue`),
  `src/simulation/messages/zenoh/ZenohTransport.js`
- **Changed:** `src/simulation/messages/MessageBus.js`, `src/simulation/messages/topics.js`,
  `src/simulation/Simulation.js`, `src/simulation/fleet/FleetAgent.js`
- **New:** `src/lib/proc_log.js`, `scripts/zenohd.sh`, `scripts/start-fleet.sh`
- **Changed:** `package.json` (`test:distributed`, `fleet`, `fleet:stop`, `zenohd`),
  `.gitignore` (`tools`, `*.local`)
- **New:** `test/distributed-fleet.test.mjs` (20-check e2e), `transport.test.mjs`

## Router / launch commands

```sh
# router (Zenoh bridge, remote API over WS)
dashboard/scripts/zenohd.sh
#   or directly:
#   zenoh-bridge-remote-api --no-multicast-scouting --ws-port 10000

# full fleet (start / inspect / stop)
dashboard/scripts/start-fleet.sh          # router + coordinator + AMR1..3, tail logs
dashboard/scripts/start-fleet.sh --no-tail # launch + demonstration check
dashboard/scripts/start-fleet.sh --stop   # stop everything
#   env: ZENOH_BRIDGE (binary path), FLEET_ROBOTS, FLEET_TASKS, FLEET_CHECK_WAIT
```

`start-fleet.sh` looks for the bridge in `dashboard/tools/zenoh/` (a symlink
is provided there) or `$ZENOH_BRIDGE`.

## Test results

- `npm run lint` (oxlint) — clean
- `npm run build` (vite/rolldown) — OK (zenoh-ts WASM chunked lazily)
- `npm run test:sim` — ALL SIMULATION CHECKS PASSED
- `npm run test:transport` — ALL TRANSPORT CHECKS PASSED
- `npm run test:distributed` — 20/20 checks; 14/14 consecutive clean runs
  after the id-uniqueness fix (artifact on failure: `/tmp/opencode/fleetdbg/last-*.log`)
- CDP UI regression (dev server + Chromium): ALL UI CHECKS PASSED,
  auction disabled/re-enabled/reset behavior, obstacles, resize, no console errors
- `start-fleet.sh --no-tail` demonstration: bots placed bids, coordinator
  committed single lowest-bid winner (`T1`/`T2` → AMR2), AMR2 started and
  completed, coordinator observed `Task T2 completed by AMR2`

## Remaining centralized dependencies

- The dashboard browser and messages still rely on the shared `MessageBus`
  contract; the distributed/flaky-code path is fully replaced by Zenoh, but
  a Node process can still fall back to in-process transport for single-node
  runs (used by `test:sim`).
- Auction authority is intentionally centralized in the coordinator (by
  design for this stage); robots await the coordinator's committed result.
- On start, robots receive spawn positions from the coordinator's
  `world/state` heartbeat (single source of truth for roster).

## Next steps

- Multi-node host deployment: run coordinator/robots on separate machines
  pointed at one router (`--url ws/<host>:10000`), TLS/multicast for larger
  fleets.
- Persist auction rounds / add leader election if the coordinator must be
  HA.
- Rate-limit `world/state` heartbeat frequency for larger fleets.