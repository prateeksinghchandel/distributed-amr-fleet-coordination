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