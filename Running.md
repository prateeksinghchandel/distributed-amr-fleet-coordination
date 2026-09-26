# Running Instructions (Windows & Linux)

## 1. Quick Start (2 Terminals)

The system works identically across Windows (PowerShell / Command Prompt), Linux, and macOS.

### Terminal 1: Fleet Manager
```bash
cd dashboard
npm run fleet-manager
```
*Tip: On Windows, `npm run fleet-manager` automatically finds your Python virtual environment (`.venv\Scripts\python.exe`) or system Python, configures `PYTHONPATH`, and starts the process manager.*

### Terminal 2: Web Dashboard
```bash
cd dashboard
npm run dev
```
Open [http://localhost:5173](http://localhost:5173) in your browser.

In the **Fleet Control** strip at the top:
- Click **START ALL**: Brings up Zenoh Router → WebSocket Bridge → Coordinator → AMRs.
- Use **Add AMR**: Enter just a name (e.g. `AMR4`). Charge spots (charging pad centroid or standby slot) are auto-assigned immediately.
- Use per-component **START / STOP / RESTART / REMOVE** controls.
  *(Note: An AMR must be stopped before it can be removed from the fleet).*

---

## 2. Zenoh Binaries Setup (One-Time / Automatic)

To install or refresh the native `zenohd` router and WebSocket bridge binaries for your operating system:
```bash
cd dashboard
npm run setup:zenoh
```
This automatically detects your OS (`windows`, `linux`, `darwin`) and architecture, downloads the official Zenoh release binaries (v1.10.1), and places them into `dashboard/tools/zenoh/`.

---

## 3. Alternative: Running Directly via Python

If you prefer to launch the Fleet Manager backend without npm:

### Windows (PowerShell):
```powershell
cd backend
python -m fleet_manager
```

### Linux / macOS:
```bash
cd backend
PYTHONPATH=. python3 -m fleet_manager
```

---

## 4. Running the Test Suites

### Backend Unit Tests (176 tests):
```bash
cd backend
python -m pytest tests/ -v
```

### Dashboard Tests:
```bash
cd dashboard
npm run test:builder
npm run test:distributed-state
npm run test:transport
npm run test:sim
```

---

## 5. Dynamic Roster & Charge Points Behavior

- **Dynamic Roster Updates:** AMRs created or removed while the coordinator is running dynamically update the coordinator's active roster over Zenoh (`control/roster/update`) immediately without needing stack restarts.
- **Centroid Positioning & Charging:** AMRs navigate and rest directly at the charging pad centroid (`spawnPoint`) rather than corner boundaries, with charging effective within `0.6m`.
- **Turnover & Yielding:**
  - Pad owners trickle-park on their assigned pads, maintaining 100% battery.
  - If any off-pad AMR drops below 25% battery (`BATTERY_WARN_THRESHOLD`), the pad owner yields its pad and moves to standby.
  - Overflow AMRs wait at standby slots when pads are busy, seek free pads when needy, and vacate immediately upon reaching 100% battery.

---

## 6. Auction Modes (Server-Auction vs. P2P-Auction)

- **Default (`SERVER_AUCTION`):** The coordinator runs the auction and publishes `tasks/assigned`.
- **Peer-to-Peer (`P2P_AUCTION`):** Set `auctionMode` to `"P2P_AUCTION"` via the Fleet Control strip or coordinator configuration. In P2P mode, robots bid directly among themselves, the winner self-commits on `auction/commit`, and the coordinator acts as a passive audit ledger.

---

## 7. RL Training Environment (Visual UI + Headless)

The reinforcement-learning training stack runs standalone from the fleet (live AMR control remains fully algorithmic). Python holds all state; the dashboard is a viewer/controller.

### 7a. Visual Training UI (2 Terminals)

**Terminal 1: RL server**
```bash
cd dashboard
npm run rl-server
```
Serves the training API + WebSocket on `http://127.0.0.1:8370`. While training, the policy auto-saves to `autosave.pt` every 5000 sim steps (`--save-every`).

**Terminal 2: Web dashboard**
```bash
cd dashboard
npm run dev
```
Open [http://localhost:5173](http://localhost:5173) and click **RL TRAINING** (header) to open the training studio — no fleet needed. The UI proxies `/rl/*` to the server.

In the studio:
- **TrainingControls:** START / PAUSE / RESUME, STEP n, STEP EPISODE, RESET EPISODE, RESET TRAINING (wipes weights — careful), and speed 0.25x–MAX.
- **Scenario:** switch presets or curriculum levels 1–8. Weights persist across switches; only the rollout buffer resets, and auto-save continues.
  The **OVERRIDES** set includes *Robots* / *RL agents* — any number of RL
  agents share the single policy brain: training scenes run per-agent
  observations/rewards with one batched action tensor (snapshots show the
  per-agent reward array; reward components are the scene average).
- **Checkpoints:** manual SAVE / LOAD / DELETE (names never overwrite, `_1` suffix on collision); the rolling `autosave.pt` file and its status update automatically.
- **Evaluation:** run RL or algorithmic baseline seeds side-by-side.

**RL server options** (`python -m rl --help`):
```bash
python -m rl --scenario dense_traffic --level 8 --n-envs 24 --safety guard \
             --save-every 5000 --checkpoint-dir ./rl/checkpoints
```
- `--save-every N` — autosave `autosave.pt` every N sim steps while training (`0` disables; default 5000).
- `--checkpoint-dir` — where `.pt` files go (default `backend/rl/checkpoints/`).
- `--level N` / `--scenario NAME` — pick the starting curriculum level or named preset; `--seed`, `--device`.

### 7b. Headless Training (fast, no UI)

```bash
cd dashboard
npm run rl-server:headless        # dense_traffic + curriculum 8, 24 envs, 60k steps
npm run rl:headless
```
Or directly:
```bash
cd backend
python -m rl.headless --scenario dense_traffic --steps 20000 --n-envs 8 --save-every 5000
```
Headless saves `autosave.pt` periodically plus a final `headless-<scenario>.pt`.

### 7c. Self-Play Training (champion vs frozen opponent pool)

Headless:
```bash
cd backend
python -m rl.headless --selfplay --scenario simple --steps 40000 \
  --n-envs 8 --rollout-steps 512 --pool-size 4 --pool-every 3000 \
  --vs-pool-episodes 3 --save-every 5000 --checkpoint-dir rl/checkpoints
```
- Every `pool-every` sim steps the current weights are frozen into a pool of
  former champions (oldest evicted); training scenes then spawn up to
  `n_opponents` (scenario-dependent) policy-driven peers to dodge. A
  deterministic 2-robot match against each pool member is run per promotion and
  its success rate printed.
- Promotions write a rolling `champion.pt`; periodic weights go to `autosave.pt`.
- All cloning/saving/evaluation is executed on the trainer's single loop thread
  (`RLTrainer.run_job` / `wait_paused`): once backprop has run, torch's eager
  CPU runtime can deadlock if another thread runs inference concurrently.

Visual UI with the *same* league schedule (Self-Play tab):
```bash
cd dashboard
npm run rl-server:league   # server with --league --pool-size 4 --pool-every 3000
npm run dev                # second terminal; open the studio's Self-Play tab
```
With server `--league`, the studio's **Self-Play** tab arm/stops the promotion
schedule, promotes the current agent manually, runs an on-demand vs-pool match,
and shows the pool roster, last-match results, and the promotion history table
(`/rl/status` reports the league state; WS `league` events drive the live view).

**One-click launcher:** the **SELF-PLAY** buttons in the header and startup
screen open the studio straight onto the Self-Play tab. There the **LAUNCH
SERVER · --league** toggle starts the Python server for you (no manual
terminal): the Vite dev/preview server exposes `/_studio/rl-launch`,
`/_studio/rl-stop` and `/_studio/rl-launcher`, which spawn/kill/report
`scripts/run-rl-server.mjs --league --pool-size 4 --pool-every 3000`
(stdout/stderr → `.rl-server.log.txt` at the repo root). The toggle flips back
to STOP when the process is up; **STOP SERVER** kills it.

### 7d. Checkpoints & Tests

- Model files live in `backend/rl/checkpoints/` by default. Every save/load/delete is a trainer command issued from the Checkpoints panel.
- Backend RL tests (including autosave and self-play):
```bash
cd backend
../.venv/bin/python -m pytest tests/rl/ -q
```