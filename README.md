# ACE: AMR Collision Avoidance and Efficiency

ACE is a Python simulation and visualization framework for coordinating autonomous mobile robots (AMRs) in a smart warehouse. It explores decentralized task allocation, path planning, time-indexed reservations, local communication, collision avoidance, deadlock recovery, obstacle response, battery management, and fleet-level performance measurement.

The project addresses Smart India Hackathon problem statement **SIH26123**, "Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots (AMRs) in Smart Warehouses." Read the complete problem statement in [`SIH_PS.md`](SIH_PS.md).

## What The Project Demonstrates

- Distributed task auctions with robot-local bids and claim/acknowledgement messages.
- A* grid path planning with congestion and task-priority costs.
- Time-indexed vertex and edge reservations for shared aisles and intersections.
- Physical occupancy checks and optional ORCA-based local movement guidance.
- Deadlock detection and priority-based recovery.
- Local dynamic-obstacle detection, path invalidation, and replanning.
- Battery-aware availability, charging, task release, and reassignment.
- A Streamlit fleet dashboard with warehouse, robot, task, reservation, conflict, and metric views.
- Reproducible baseline-versus-distributed benchmark runs.

## Repository Layout

```text
.
├── auction/          Task model, bids, auctioneer, and auction protocol
├── communication/    In-process peer-to-peer network and message types
├── dashboard/        Streamlit UI, simulation engine, snapshots, and renderers
├── planning/         A*, reservations, collision detection, deadlock, and ORCA
├── robots/           Robot state, behavior, movement, battery, and coordination
├── scenarios/        Reusable test scenarios and benchmark generation
├── simulation/       Warehouse, simulator loop, and metrics
├── tests/            Unit, integration, safety, renderer, and benchmark tests
├── benchmark.py      Benchmark CLI and JSON/CSV export
├── benchmark_report.py  Human-readable benchmark report CLI
├── config.py         Frozen simulation configuration defaults
├── main.py           Minimal sample simulation entry point
└── run_dashboard.py  Streamlit dashboard launcher
```

## Requirements

- Python 3.10 or newer is recommended because the code uses modern type annotations.
- `pytest` for the test suite.
- `streamlit` for the dashboard.

Install the project dependencies in a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Quick Start

Run the sample simulation:

```bash
python main.py
```

Start the interactive Streamlit dashboard at `http://localhost:8501`:

```bash
python run_dashboard.py
```

The launcher passes additional Streamlit flags through to the child process. For example:

```bash
python run_dashboard.py --server.port=8502
```

## Tests

Run the complete test suite from the repository root:

```bash
python -m pytest -q
```

The tests cover core task and planning behavior, auctions, communication protocol regressions, obstacles, battery availability, ORCA guidance, deadlock scenarios, physical safety, dashboard rendering, integration, and benchmark calculations.

## Benchmarks

The benchmark compares the distributed simulator with a sequential stop-and-wait baseline using the same generated task sets and seeds. The default matrix uses 3 robots, 20/50/100 tasks, and seeds 42 through 46.

Run a smaller smoke benchmark:

```bash
python benchmark.py --tasks 5 --seeds 42 --max-steps 500
```

Run the default matrix:

```bash
python benchmark.py
```

This writes `benchmark_results.json` and `benchmark_results.csv` in the current directory. Choose explicit paths with `--json` and `--csv` when needed.

Format an existing JSON result file:

```bash
python benchmark_report.py benchmark_results.json
```

The benchmark status is `PASS` only when the distributed result has zero collisions and at least 20% improvement over the baseline. The benchmark measures this criterion; it does not claim that every workload or configuration will pass it.

## Configuration

The default simulation settings are defined in [`config.py`](config.py), including grid dimensions, robot count, speed, battery behavior, reservation horizon, auction mode, obstacle sensing, congestion penalties, and ORCA parameters. `main.build_simulation()` constructs a simulation from a `Config` instance, so experiments can provide a customized configuration without changing the core modules.

See [`documentation.md`](documentation.md) for the parameter reference, architecture, message protocol, simulator lifecycle, dashboard details, and extension guidance.

## Contribution Workflow

Create a branch for your work, run the tests before sharing changes, and keep feature changes isolated from generated benchmark output. The project's expected integration flow is:

1. Develop and test on a personal branch.
2. Pull the latest `dev` branch before integration.
3. Send tested changes to `dev` for team testing.
4. Review and merge from `dev` to `main` when the release is ready.

## License And Project Status

No license file is currently included in the repository. Add project licensing before distributing the software outside the team. This repository is a simulation and research/prototyping implementation; deployment to physical Raspberry Pi, Jetson Nano, or warehouse hardware requires an additional hardware integration and validation layer.
