# Distributed AI Fleet Coordination for Autonomous Mobile Robots

## 1. Project Vision

This project implements a **distributed multi-robot fleet coordination system for Autonomous Mobile Robots (AMRs) operating in a dynamic warehouse environment**.

The system is built around three fundamental layers:

1. **Central Server → knows the warehouse and announces tasks**
2. **Fleet/P2P Layer → robots collectively decide who performs each task**
3. **Individual AMR → decides how to physically execute its task safely**

The central server should **not continuously control the robots**. Robots communicate with one another and with the server through the distributed communication layer, allowing the fleet to coordinate tasks and movement without requiring every movement decision to pass through a central controller.

The final system supports **three interchangeable operating modes**:

* **Algorithm-Only Mode** — conventional deterministic algorithms perform planning, coordination, and collision avoidance.
* **Robot AI Mode** — each AMR uses its own neural-network policy for local decision-making, while deterministic safety and imminent-collision avoidance act as higher-priority guard rails.
* **AI Global Planning Mode** — AI assists with global path planning, path correction, congestion-aware rerouting, and route optimization, while deterministic local collision avoidance and emergency safety remain authoritative.

The communication, fleet-management, telemetry, dashboard, simulation, and safety infrastructure should remain common across all three modes.

---

# 2. Overall Architecture

```text
                         ┌─────────────────────────┐
                         │      WEB DASHBOARD       │
                         │                         │
                         │ • Warehouse editor      │
                         │ • Robot visualization   │
                         │ • Task generation       │
                         │ • Fleet status          │
                         │ • Experiment controls   │
                         │ • Operating mode        │
                         │ • Telemetry             │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │      FLEET SERVER       │
                         │                         │
                         │ • Warehouse state       │
                         │ • Task manager          │
                         │ • Task generator        │
                         │ • Telemetry collector   │
                         │ • Robot health monitor  │
                         │ • Logging               │
                         │ • Mode/configuration    │
                         └────────────┬────────────┘
                                      │
                              ═══ ZENOH NETWORK ═══
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
              ▼                       ▼                       ▼
       ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
       │    AMR 1     │◄──────►│    AMR 2     │◄──────►│    AMR 3     │
       │              │        │              │        │              │
       │ P2P Agent    │        │ P2P Agent    │        │ P2P Agent    │
       │ Task Manager │        │ Task Manager │        │ Task Manager │
       │ Planner      │        │ Planner      │        │ Planner      │
       │ AI Policy    │        │ AI Policy    │        │ AI Policy    │
       │ Safety       │        │ Safety       │        │ Safety       │
       │ Controller   │        │ Controller   │        │ Controller   │
       └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
              │                       │                       │
              ▼                       ▼                       ▼
           Robot 1                 Robot 2                 Robot 3
```

There are therefore two major communication directions.

### Server ↔ Robots

Used mainly for:

* Task announcements
* Telemetry
* Warehouse/environment information
* Configuration
* Robot monitoring
* Fleet status
* System mode

### Robot ↔ Robot

Used mainly for:

* Auctions
* Task allocation
* Position/state sharing
* Trajectory/intent sharing
* Priority negotiation
* Collision coordination
* Reservations
* Failure information

The second communication path is fundamental to the **peer-to-peer fleet coordination architecture**.

---

# 3. System Principles

The architecture follows four primary principles.

### 3.1 Centralized organization, distributed decision-making

The server knows the warehouse and manages task availability, but does not continuously decide which robot should perform every movement.

```text
Server
  │
  └── "Task 42 exists."
          │
          ▼
       Robots
          │
          ├── AMR1 calculates bid
          ├── AMR2 calculates bid
          └── AMR3 calculates bid
                    │
                    ▼
             Fleet decision
```

### 3.2 Robots are independent agents

Every AMR has its own:

* State
* Communication
* Fleet agent
* Task state
* Planner
* Decision system
* Collision avoidance
* Safety system
* Controller

A robot should be able to reason about its own execution using information received from other robots.

### 3.3 Planning and collision avoidance are separate

Global planning answers:

> "How should I get from A to B?"

Local collision avoidance answers:

> "Given what is happening right now, can I safely continue along that plan?"

The distinction remains valid in all three operating modes.

### 3.4 AI does not replace deterministic safety

AI can make decisions or propose routes, but it must operate within deterministic safety constraints.

The final authority over an imminent physical collision belongs to the safety layer.

---

# 4. Fleet Server

The fleet server is **not the fleet's decision-making brain**.

It acts primarily as the warehouse supervisor, task organizer, telemetry collector, and system monitor.

Its responsibilities include:

```text
                     SERVER
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     Warehouse       Tasks       Telemetry
      Manager        Manager       Manager
          │            │            │
          ▼            ▼            ▼
     Environment    Task Queue   Robot States
```

The server is responsible for:

* Warehouse state
* Warehouse configuration
* Task generation
* Task announcements
* Telemetry collection
* Robot health monitoring
* System configuration
* Operating-mode configuration
* Logging
* Dashboard communication

The server should not become a centralized low-level motion controller.

---

# 5. Warehouse Manager

The warehouse manager maintains:

```text
warehouse dimensions
obstacles
pickup locations
dropoff locations
```

For example:

```text
Warehouse = 30m × 20m

Obstacles:

    [5,5]  → [8,10]
    [15,3] → [18,12]
```

The dashboard should be able to modify the environment while the simulation is running.

Changes must propagate to the robots so that their planning and decision systems can react to the new environment.

---

# 6. Task Manager

The server creates and announces tasks.

A task contains information such as:

```text
Task 42

Pickup:  (4.5, 7.2)
Dropoff: (24.2, 15.8)
Priority: NORMAL
Status: UNASSIGNED
```

The server publishes a new task:

```text
NEW_TASK
    │
    ▼
 Zenoh
    │
 ┌──┼──┐
 ▼  ▼  ▼
R1 R2 R3
```

The server **does not directly choose the robot**.

The task enters the distributed fleet-allocation process.

---

# 7. Distributed P2P Fleet Coordination

Every AMR runs a fleet agent.

Suppose Task 42 is announced:

```text
                    TASK 42
                       │
              ┌────────┼────────┐
              ▼        ▼        ▼
            AMR1     AMR2     AMR3
              │        │        │
              ▼        ▼        ▼
            bid=32   bid=18   bid=25
              │        │        │
              └────────┼────────┘
                       ▼
                 AMR2 selected
```

Each robot calculates its own bid.

A bid may consider:

* Distance to pickup
* Distance to dropoff
* Current workload
* Battery
* Estimated travel time
* Current congestion
* Task priority

For example:

```text
bid =
      travel_cost
    + congestion_cost
    + battery_cost
    + workload_cost
```

The exact bid function can evolve independently of the rest of the architecture.

The important architectural property is:

> **The robot determines its own cost and participates in the distributed allocation process.**

---

# 8. Task Execution

After a robot wins a task:

```text
Task
  ↓
Winning AMR
  ↓
Decision / Planning Layer
  ↓
Path
  ↓
Local execution
  ↓
Safety validation
  ↓
Controller
  ↓
Robot
```

The planning/decision layer depends on the currently selected operating mode.

---

# 9. Operating Modes

The fleet has three system-wide operating modes selectable from the dashboard.

```text
                    OPERATING MODE
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
       Algorithm Only   Robot AI   AI Global Planning
```

All three modes share:

* Zenoh communication
* Fleet/P2P coordination
* Task management
* Robot telemetry
* Warehouse state
* Collision safety
* Emergency handling
* Dashboard
* Simulation/hardware interface

Only the decision/planning strategy changes.

---

# 10. Mode 1 — Algorithm-Only

Algorithm-Only Mode is the deterministic baseline.

No neural network is required for robot navigation decisions.

The system uses conventional algorithms for:

* Task allocation
* Global path planning
* Local planning
* Collision avoidance
* Deadlock handling
* Rerouting
* Priority resolution
* Motion control

A baseline execution pipeline is:

```text
Task
  ↓
Distributed Auction
  ↓
Winning Robot
  ↓
A*
  ↓
Local Planner
  ↓
Collision Avoidance
  ↓
Controller
  ↓
Robot
```

This mode provides the baseline for evaluating the AI-based modes.

---

# 11. Mode 2 — Robot AI

In Robot AI Mode, **each AMR contains its own neural-network policy**.

The AI policy receives an observation representing the robot's current situation and proposes an action or local decision.

Conceptually:

```text
                 Robot State
                     +
             Other Robot State
                     +
              Environment
                     │
                     ▼
             ┌───────────────┐
             │ Neural Network│
             │ Robot Policy  │
             └───────┬───────┘
                     │
              Proposed Action
                     │
                     ▼
             ┌───────────────┐
             │ Safety Guard  │
             └───────┬───────┘
                     │
             Safe/Modified Action
                     │
                     ▼
                Controller
                     │
                     ▼
                   Robot
```

The neural network must not have unrestricted authority over physical movement.

The AI produces a **proposed action**.

The deterministic safety layer validates that action before it reaches the controller.

---

# 12. Robot AI Responsibilities

The neural-network policy may eventually learn or provide:

* Local navigation decisions
* Adaptive movement
* Speed selection
* Local trajectory selection
* Dynamic obstacle response
* Robot interaction behavior
* Congestion-aware movement
* Learned local decision-making

The exact neural-network architecture and training methodology are implementation/research components and should remain replaceable.

AI inference should preferably occur locally on the robot's edge computer so that robot decision-making does not require continuous dependence on a central server.

---

# 13. Mode 3 — AI Global Planning

The third mode uses AI primarily for **global planning and route correction**, while retaining deterministic local control and safety.

The conceptual pipeline is:

```text
             Warehouse + Fleet State
                       │
                       ▼
              AI Global Planner
                       │
                Proposed Route
                       │
                       ▼
                Route Validation
                       │
              ┌────────┴────────┐
              │                 │
            Valid            Invalid
              │                 │
              ▼                 ▼
         Local Planner       Rerouting
              │                 │
              └────────┬────────┘
                       ▼
              Local Collision Avoidance
                       │
                       ▼
                  Safety Guard
                       │
                       ▼
                   Controller
                       │
                       ▼
                     Robot
```

The AI global planner may assist with:

* Route selection
* Global path correction
* Rerouting
* Congestion prediction
* Congestion-aware route selection
* Identifying inefficient paths
* Adapting routes to changing warehouse conditions

The global AI does not bypass local collision avoidance.

---

# 14. Common Safety Architecture

Safety is common to all three modes.

```text
             Decision / Planning
                      │
                      ▼
            ┌──────────────────┐
            │ Deterministic    │
            │ Safety Layer      │
            │                  │
            │ • Collision      │
            │ • Constraints    │
            │ • Safe distance  │
            │ • Emergency stop │
            │ • Override       │
            └────────┬─────────┘
                     │
                     ▼
                 Controller
```

The safety system must be capable of overriding:

* Algorithmic planners
* AI robot policies
* AI global planners
* Normal trajectory following

Safety should be treated as a **higher-priority execution constraint**, not as another optional planning strategy.

---

# 15. Imminent Collision Avoidance

The most immediate safety layer is responsible for reacting to imminent collisions.

It must be able to:

* Detect imminent collision
* Predict near-future collision
* Reduce speed
* Stop the robot
* Modify trajectory
* Avoid another robot
* Avoid dynamic obstacles
* Enforce minimum separation
* Override AI decisions

Conceptually:

```text
AI / Planner
     │
     ▼
Proposed action
     │
     ▼
Collision prediction
     │
 ┌───┴───────────────┐
 │                   │
Safe              Imminent
 │                   │
 ▼                   ▼
Execute          Override
                     │
             Stop / slow / avoid
```

The system must never assume that a globally valid path is locally safe at every instant.

---

# 16. Global Path Planning vs Local Collision Avoidance

These remain separate subsystems.

## Global Path Planning

Answers:

> "How should I get from A to B?"

It considers:

* Warehouse layout
* Static obstacles
* Destination
* Known route costs
* Global fleet conditions

A* provides the initial deterministic baseline:

```text
Start → → → ↓ ↓ → → → Goal
```

AI Global Planning Mode may later replace or augment this process.

## Local Collision Avoidance

Answers:

> "Given what is happening right now, should I continue following this path?"

For example:

```text
            AMR1
              ↓
              ↓
              X
              ↑
              ↑
            AMR2
```

Both robots can have valid global paths while their paths conflict locally.

The local system may therefore decide:

```text
AMR1 → slow down
AMR2 → continue
```

or:

```text
AMR1 → continue
AMR2 → wait
```

or:

```text
AMR1 → modify trajectory
```

This process occurs continuously during execution.

---

# 17. Robot Internal Architecture

Each AMR should contain:

```text
                         AMR NODE
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
 Communication         Fleet Agent          Telemetry
        │                   │
        │                   ├── Auction
        │                   ├── Task state
        │                   ├── Priority
        │                   └── P2P coordination
        │
        └────────────────────────┐
                                 ▼
                         Task Executor
                                 │
                                 ▼
                    Decision / Planning Layer
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
                    ▼            ▼            ▼
                Algorithm      Robot AI    Global AI
                  Mode           Mode       Planning
                    │            │            │
                    └────────────┼────────────┘
                                 ▼
                       Local Collision Avoidance
                                 │
                                 ▼
                         Deterministic Safety
                                 │
                                 ▼
                            Controller
                                 │
                                 ▼
                               ROBOT
```

The exact implementation may combine some of these modules, but the responsibilities should remain logically separated.

---

# 18. Robot State and Telemetry

Every AMR continuously publishes information such as:

```text
AMR 2

Position:       (12.4, 8.7)
Velocity:       (0.8, 0.0)
Heading:        90°
Status:         MOVING
Task:           42
Battery:        78%
Current path:   [...]
Next waypoint:  (13,9)
Health:         OK
Timestamp:      ...
```

This information is useful to:

* The fleet server
* The dashboard
* Other robots
* Collision-avoidance systems
* Global planners
* AI models

Importantly:

> **Other robots must receive relevant state information directly through the distributed communication system.**

The server should not be required to relay every robot observation before another robot can react.

---

# 19. Robot Intent

Telemetry answers:

> "Where is the other robot?"

Intent answers:

> "What does the other robot intend to do?"

For example:

```text
AMR1:

Current:
(10,10)

Planned:
(10,10)
   ↓
(10,15)
   ↓
(20,15)
```

AMR2 can then determine that AMR1 intends to pass through a region that AMR2 is approaching.

Intent information can therefore improve:

* Conflict prediction
* Collision avoidance
* Priority negotiation
* Reservation
* Deadlock detection
* AI observations

---

# 20. Distributed Collision Coordination

Consider two robots approaching the same intersection:

```text
                 AMR1
                   ↓
                   ↓
              ─────X─────
                   ↑
                   ↑
                 AMR2
```

Robots exchange information such as:

```text
position
velocity
planned path
ETA
task priority
intent
reservation
```

A coordination mechanism determines an appropriate action:

```text
AMR1 → GO
AMR2 → WAIT
```

The priority mechanism should eventually consider meaningful factors rather than relying only on robot ID.

Possible factors include:

```text
task priority
waiting time
urgency
ETA
current progress
other fleet constraints
```

The exact priority function can evolve independently.

---

# 21. Reservations

For constrained warehouse regions such as:

* Narrow aisles
* Intersections
* Choke points
* Single-lane passages

robots may exchange or maintain reservations representing intended occupancy.

A reservation can conceptually contain:

```text
Robot
Region
Entry time
Exit time
Priority
Task
```

Reservations should assist planning and coordination but must not replace the imminent-collision safety layer.

A robot must still react to unexpected conditions.

---

# 22. Deadlock Handling

Collision avoidance alone is insufficient.

Robots can enter situations where every robot waits for another robot.

Example:

```text
             AMR1
               ↓
               │
         ──────┼──────
               │
               ↑
             AMR2
```

The system therefore requires explicit deadlock handling.

Conceptual process:

```text
Conflict
   ↓
Waiting
   ↓
Waiting exceeds threshold
   ↓
Deadlock suspected
   ↓
Distributed resolution
   ↓
One robot receives priority
   ↓
Other robot waits/reroutes
```

Deadlock handling should be a dedicated subsystem rather than being hidden entirely inside the path planner.

AI may eventually assist with deadlock prediction or resolution, but deterministic recovery must remain available.

---

# 23. Dynamic Environment Handling

The warehouse is not necessarily static.

The dashboard should be able to:

* Add obstacles
* Remove obstacles
* Move obstacles
* Change warehouse dimensions
* Add pickup locations
* Add drop-off locations
* Create tasks

A change should propagate through the system:

```text
Environment change
       ↓
State update
       ↓
Path validation
       ↓
Conflict detection
       ↓
Replanning / rerouting
       ↓
Local execution
       ↓
Safety validation
```

AI Global Planning Mode can use these changes to generate new routes, while Robot AI Mode can incorporate the changed environment into its local observations.

---

# 24. Robot Failure Detection and Recovery

Robots periodically publish heartbeat/status information.

Example:

```text
AMR3

heartbeat
heartbeat
heartbeat
heartbeat
   X
```

A timeout can produce:

```text
No heartbeat
     ↓
Timeout
     ↓
AMR3 suspected failed
```

The fleet must then:

1. Mark the robot unavailable.
2. Identify its active task.
3. Determine whether the task was completed.
4. Recover/requeue the task if necessary.
5. Make the task available for another auction.
6. Replan affected paths.
7. Update the dashboard.

For example:

```text
Task 27
   ↓
AMR3 FAILED
   ↓
Task becomes AVAILABLE
   ↓
New auction
   ↓
AMR1 / AMR2 bid
   ↓
New winner
   ↓
Task continues
```

This makes the fleet fault tolerant.

---

# 25. Complete Task Lifecycle

A task should follow a well-defined state machine:

```text
CREATED
   │
   ▼
ANNOUNCED
   │
   ▼
BIDDING
   │
   ▼
ASSIGNED
   │
   ▼
PLANNING
   │
   ▼
EXECUTING
   │
   ├──────────────► BLOCKED
   │                  │
   │                  ▼
   │               REROUTING
   │                  │
   │                  └──────► EXECUTING
   │
   ▼
COMPLETED
```

Failures must also be explicit:

```text
EXECUTING
    │
    ▼
  FAILURE
    │
    ▼
TASK RECOVERY
    │
    ├──► Retry
    ├──► Reassign
    └──► Cancel
```

**Implementation status.** The concrete `TaskStatus` machine in `task_manager.py` is:

```text
PENDING ── announce ──► AUCTIONING ── claim/commit ──► ASSIGNED ──► PICKING_UP ──► DELIVERING ──► COMPLETED
   ▲                        │
   └── requeue (timeout /   └──► retries exhausted ──► requeue at queue tail
        no eligible robot /      (or cancel / fail via control topics)
        no commit / watchdog)
```

* `AUCTIONING` is set the moment a task is announced; it is never permanent. On timeout, no-commit, no-winner or a failed winner the task is reset to `PENDING` and re-auctioned (fresh `auctionId` `T:A<N>`, max retries).
* **One active assignment per robot.** TaskManager keeps the authoritative ledger; `is_robot_available` returns false while the robot has an `ASSIGNED`/`PICKING_UP`/`DELIVERING` task or reports telemetry busy (online, battery above critical, no live `currentTaskId`, or a `COMPLETED` hold). Availability gates announcement (`_any_eligible_robot`), bid filtering (the coordinator-agent's `eligible_filter` in `server_node.py` for SERVER mode), and every `assign_task`/`apply_auction_commit` call. Stale "IDLE-looking" telemetry is overridden by the ledger, so a busy robot never accumulates a second task.
* **Assignment watchdog.** `sync_tasks` runs continuously; if a robot was assigned a task but neither picks it up nor fails within `ASSIGN_WATCHDOG_S = 8 s`, the assignment is recovered: auction-sourced tasks are re-auctioned, manually assigned tasks are marked `FAILED` (never silently re-auctioned).
* **P2P self-block.** A robot that already won a concurrent round (its `self_won` flag is still pending) aborts a second simultaneous self-win at its deadline and never accepts the double assignment; the server's timeout then requeues the aborted round.
* **Just-completed robots are reusable.** The robot controller keeps `currentTaskId` set through the `COMPLETED` hold (so the server's `sync_tasks` observes the completion), then transitions to `IDLE`. After that the robot is eligible for the next auction.
* **Authoritative ledger → dashboard.** `world/state` now carries `tasks` (the full ledger), `taskStats`, `robotStats` and `metrics`. The dashboard mirrors these verbatim, so task statuses can never go stale; the telemetry-derived fallback cannot regress a terminal state.

---

# 26. Dashboard Architecture

The dashboard is the primary human interface.

```text
                  REACT DASHBOARD
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      Warehouse         Fleet          Tasks
        Editor          Monitor        Panel
          │              │              │
          └──────────────┼──────────────┘
                         │
                         ▼
                   API / Zenoh
                         │
                         ▼
                       SERVER
```

The dashboard should provide:

### Warehouse

```text
Width: 30m
Height: 20m

[Add obstacle]
[Remove obstacle]
```

### Task Controls

```text
Task Generation

○ Manual
○ Same dropoff
○ Random pickup/dropoff

Number of tasks: 20

[Generate]
```

### Fleet Monitor

```text
AMR1   ONLINE    Task 12    82%
AMR2   ONLINE    Task 15    64%
AMR3   OFFLINE   ---        71%
```

### Operating Mode

```text
Operating Mode

○ Algorithm Only
○ Robot AI
○ AI Global Planning
```

The selected mode should be visible during execution.

---

# 27. Dashboard Visualization

The Canvas-based visualization should display:

* Warehouse boundaries
* Obstacles
* Robots
* Robot direction
* Robot paths
* Planned trajectories
* Pickup points
* Drop-off points
* Task assignments
* Collision warnings
* Reservations
* Communication state
* Deadlock state
* Robot failures
* Rerouting
* AI decisions where useful

For AI modes, additional useful information includes:

```text
AI action
AI route proposal
AI inference state
Safety override
Collision-avoidance intervention
Rerouting event
```

The dashboard is primarily an **observation and configuration system**, not the component responsible for real-time robot control.

---

# 28. Mode Switching

The three modes should use the same fleet infrastructure.

Switching modes changes the decision/planning implementation:

```text
                    Fleet State
                        │
                        ▼
                  Mode Selector
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
      Algorithm       Robot AI     Global AI
      Planner          Policy       Planner
          │             │             │
          └─────────────┼─────────────┘
                        ▼
                Common Safety Layer
                        │
                        ▼
                   Controller
```

The system should avoid duplicating:

* Communication
* Task management
* Fleet coordination
* Telemetry
* Safety
* Robot interfaces

Only the appropriate decision/planning module should change.

---

# 29. AI and Safety Authority

The final authority hierarchy is:

```text
                 EMERGENCY SAFETY
                        ▲
                        │
                 highest authority
                        │
              IMMINENT COLLISION
                        │
                        ▼
              LOCAL COLLISION
                 AVOIDANCE
                        │
                        ▼
              AI / LOCAL DECISION
                        │
                        ▼
                PATH PLANNING
                        │
                        ▼
               TASK ALLOCATION
                        │
                        ▼
             DASHBOARD CONFIGURATION
```

This hierarchy is conceptual rather than a requirement that every component communicate synchronously.

The critical rule is:

> **No AI component may bypass the deterministic safety constraints governing physical movement.**

---

# 30. Simulation and Physical Deployment

Robot logic should be separated from the physical robot interface.

```text
                 ROBOT AGENT
                      │
               ┌──────┴──────┐
               │             │
          Simulation       Hardware
               │             │
        2D / simulator   Sensors / motors
```

The same high-level robot architecture should therefore be usable with:

* A software simulation
* Raspberry Pi
* Jetson-class edge hardware
* Future physical AMRs

AI inference should preferably run locally on the robot's edge computer.

This maintains the distributed nature of the system and avoids making continuous cloud/server inference a requirement.

---

# 31. Communication Model

The distributed communication layer should expose an abstraction rather than tying the robot logic directly to a particular transport implementation.

The current distributed implementation uses **Zenoh**.

Conceptually:

```text
Robot 1 ───────────┐
                   │
Robot 2 ───────────┼── Zenoh Network
                   │
Robot 3 ───────────┤
                   │
Fleet Server ──────┘
```

Communication should support messages for:

```text
robot/
    state
    telemetry
    intent
    status
    heartbeat

task/
    announce
    bid
    assignment
    progress
    completion
    failure

coordination/
    reservation
    conflict
    negotiation
    priority

system/
    mode
    configuration
    emergency
```

The exact topic structure may evolve, but communication responsibilities should remain clearly separated.

### 31.1 Auction modes

**Server-Auction** (default, `AuctionMode.SERVER_AUCTION`): robots bid on `auction/bids`; the coordinator's coordinator-agent aggregates bids, picks the winner with the same deterministic `select_winner` used everywhere, and publishes `tasks/assigned` itself. This is the legacy flow and remains the default so existing deployments are unaffected.

**P2P-Auction** (`AuctionMode.P2P_AUCTION`): the coordinator is a *passive ledger*, not a decision-maker.

```text
Server                                     robots
  │                                           │
  ├── tasks/new (+ auctionId, scheme T:A<N>) ─┤ every robot
  │                                           ▼
  │        (each robot stores its own bid locally)
  │           bid broadcast: auction/bids (+ auctionId)
  │                ▲         ▼
  │                └─ peer ⇄ peer ─┘        (no server role)
  │                                           │
  │                     at deadline each robot runs select_winner
  │                                           │
  │ ◄────────────────── auction/commit ─────── winner self-commits
  │   (apply_auction_commit: idempotent ledger, no assignment here)
  │                                           │
  │                    winner waits COMMIT_WINDOW_S (0.6 s)
  │                                           │
  │ ◄────────────────── tasks/assigned ─────── winner self-assigns
  │                     (server only records)
```

* Announce carries a unique `auctionId` (`f"{task_id}:A{attempt}"`); every round, bid, commit and result carries it, so re-auctions of the same task (e.g. `T4:A1`, `T4:A2`) are unambiguous.
* Each robot publishes its own bid and *also* keeps it locally, so a robot can always compute the winner even while peers' messages are in flight.
* Deadlines: `DEADLINE_S = 1.0`; the server independently re-auctions a non-committing task after `DEADLINE + P2P_COMMIT_GRACE_S = 2.4 s` (`sync_auction_timeout`).
* Conflict safety: if a peer's `auction/commit` names a different winner than the robot's own `select_winner` result, the robot aborts the round (`B_CONFLICT`) and never executes — determinism plus an "at-most-one-committer" abort; the server ledger is single-assignment as an independent backstop.
* A failed auction-sourced winner is re-auctioned automatically (fresh `auctionId`); manually assigned tasks are never re-auctioned.

Select `AuctionMode` per process via `--mode server_auction|p2p_auction` (`server.server_node`, `robot/robot_node.py`, fleet-manager `POST /api/coordinator/configure`), and it is broadcast in `world/state` as `auctionMode`.

---

# 32. Complete End-to-End Data Flow

A complete task execution looks like:

### Step 1 — Warehouse configuration

```text
Dashboard
    ↓
Server
    ↓
Warehouse state
```

### Step 2 — Task generation

```text
Task Generator
    ↓
Task 42
    ↓
Zenoh
```

### Step 3 — Task announcement

```text
               Task 42
                  │
          ┌───────┼───────┐
          ▼       ▼       ▼
        AMR1    AMR2    AMR3
```

### Step 4 — Distributed bidding

```text
AMR1 → bid
AMR2 → bid
AMR3 → bid
```

### Step 5 — Winner determination

```text
Distributed coordination
          ↓
      AMR2 wins
```

### Step 6 — Decision/planning

The selected operating mode determines what happens next.

```text
Algorithm Only
      ↓
     A*

Robot AI
      ↓
Neural Network

AI Global Planning
      ↓
AI Global Planner
```

### Step 7 — Local execution

```text
Planning
   ↓
Local execution
   ↓
Collision avoidance
   ↓
Safety guard
   ↓
Controller
   ↓
Robot
```

### Step 8 — Continuous observation

```text
AMR1 telemetry ──┐
AMR3 telemetry ──┼──→ AMR2
AMR intentions ──┘
```

### Step 9 — Conflict

```text
Potential conflict
       ↓
Prediction
       ↓
Coordination
       ↓
Wait / slow / modify trajectory / reroute
```

### Step 10 — Safety intervention if required

```text
Imminent collision
       ↓
Safety override
       ↓
Stop / slow / emergency avoidance
```

### Step 11 — Continue execution

```text
Safe
  ↓
Continue
```

### Step 12 — Task completion

```text
MOVING_TO_PICKUP
        ↓
      PICKING
        ↓
MOVING_TO_DROPOFF
        ↓
     DROPPING
        ↓
    COMPLETED
```

### Step 13 — Next task

The robot becomes available and can participate in another distributed auction.

---

# 33. Server vs Robot Responsibilities

This distinction should remain explicit.

| Function                  |  Server  |            Robot           |
| ------------------------- | :------: | :------------------------: |
| Warehouse configuration   |     ✓    |            Read            |
| Dashboard                 |     ✓    |              —             |
| Task generation           |     ✓    |              —             |
| Task announcement         |     ✓    |           Receive          |
| Task bidding              |     —    |            **✓**           |
| Distributed task decision |     —    |            **✓**           |
| Global path planning      | Optional |            **✓**           |
| AI global planning        | Optional |   Depends on architecture  |
| Robot AI policy           |     —    |            **✓**           |
| Local trajectory planning |     —    |            **✓**           |
| Collision avoidance       |     —    |            **✓**           |
| Safety guard              |     —    |            **✓**           |
| Deadlock handling         |  Monitor |            **✓**           |
| Robot telemetry           |  Collect |          **Send**          |
| Robot health monitoring   |     ✓    |            **✓**           |
| Failure detection         |     ✓    |            **✓**           |
| Task reassignment         | Announce | **Participate in auction** |
| Visualization             |     ✓    |              —             |
| Mode configuration        |     ✓    |        Receive/apply       |

The core division remains:

> **Server = observe + organize**

> **Robots = decide + execute + coordinate**

---

# 34. Final Software Architecture

A logical project structure can be organized approximately as:

```text
amr-fleet/
│
├── server/
│   ├── warehouse/
│   │   ├── warehouse.py
│   │   └── obstacles.py
│   │
│   ├── tasks/
│   │   ├── task.py
│   │   └── task_generator.py
│   │
│   ├── telemetry/
│   │   └── telemetry_manager.py
│   │
│   ├── fleet/
│   │   ├── fleet_manager.py
│   │   └── health_monitor.py
│   │
│   └── server.py
│
├── robot/
│   ├── communication/
│   │   ├── zenoh_node.py
│   │   └── messages.py
│   │
│   ├── fleet/
│   │   ├── auction.py
│   │   ├── priority.py
│   │   └── task_manager.py
│   │
│   ├── planning/
│   │   ├── astar.py
│   │   ├── global_planner.py
│   │   └── local_planner.py
│   │
│   ├── ai/
│   │   ├── robot_policy.py
│   │   ├── global_planner.py
│   │   └── inference.py
│   │
│   ├── avoidance/
│   │   ├── collision.py
│   │   ├── deadlock.py
│   │   └── reservation.py
│   │
│   ├── safety/
│   │   ├── guard.py
│   │   ├── emergency.py
│   │   └── constraints.py
│   │
│   ├── control/
│   │   └── controller.py
│   │
│   ├── telemetry/
│   │   └── telemetry.py
│   │
│   └── robot_node.py
│
├── simulation/
│   ├── world.py
│   ├── robot.py
│   └── simulator.py
│
├── dashboard/
│   ├── src/
│   │   ├── components/
│   │   ├── canvas/
│   │   └── api/
│   └── ...
│
└── tests/
    ├── planning/
    ├── auction/
    ├── collision/
    ├── safety/
    ├── ai/
    ├── communication/
    └── integration/
```

The exact language and directory structure may evolve. The important requirement is separation of responsibilities.

---

# 35. Development Strategy

Development should proceed incrementally while preserving the working deterministic baseline.

```text
Stage 1
Deterministic multi-robot simulation
        ↓
Stage 2
Distributed task allocation
        ↓
Stage 3
Real distributed communication
        ↓
Stage 4
Global + local path planning
        ↓
Stage 5
Distributed collision avoidance
        ↓
Stage 6
Deadlock + failure recovery
        ↓
Stage 7
Dashboard observability/control
        ↓
Stage 8
Algorithm-Only baseline stabilization
        ↓
Stage 9
Neural-network robot policy
        ↓
Stage 10
Deterministic AI safety guard rails
        ↓
Stage 11
Robot AI Mode
        ↓
Stage 12
AI global planning / rerouting
        ↓
Stage 13
Three-mode evaluation
        ↓
Stage 14
Simulation → edge hardware
```

The exact order may change during implementation, but the **Algorithm-Only mode should remain functional throughout development**.

This provides a deterministic reference implementation against which AI behavior can be evaluated.

---

# 36. Evaluation

The final system should be evaluated across the three operating modes.

Relevant measurements include:

### Fleet performance

* Task completion rate
* Average task completion time
* Total travel distance
* Robot utilization
* Idle time
* Task allocation efficiency

### Navigation

* Path length
* Replanning frequency
* Rerouting frequency
* Congestion
* Navigation failures

### Coordination

* Number of conflicts
* Waiting time
* Deadlock frequency
* Deadlock recovery time
* Communication latency
* Coordination overhead

### Safety

* Collision count
* Near-collision events
* Emergency stops
* Safety overrides
* Minimum separation distance

### AI

* Inference latency
* AI decision frequency
* AI intervention/override rate
* Route quality
* Performance under unseen layouts
* Performance under dynamic obstacles

The purpose is not merely to demonstrate that AI can move a robot, but to determine how AI changes the behavior of a **distributed multi-robot fleet while deterministic safety remains enforced**.

---

# 37. Core Concept

The entire system can be represented by:

```text
                         ┌───────────────┐
                         │   DASHBOARD   │
                         └───────┬───────┘
                                 │
                                 ▼
                      ┌────────────────────┐
                      │    FLEET SERVER    │
                      │                    │
                      │ Warehouse          │
                      │ Task Generation    │
                      │ Telemetry          │
                      │ Monitoring         │
                      │ Mode Configuration │
                      └──────────┬─────────┘
                                 │
                           Task / State
                                 │
                          ═══ ZENOH ═══
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
              ▼                  ▼                  ▼
          ┌────────┐         ┌────────┐         ┌────────┐
          │ AMR 1  │◄───────►│ AMR 2  │◄───────►│ AMR 3  │
          └───┬────┘         └───┬────┘         └───┬────┘
              │                  │                  │
              └──────── P2P FLEET COORDINATION ────┘
                                 │
                                 ▼
                       Winning Robot / Task
                                 │
                                 ▼
                     ┌─────────────────────┐
                     │ DECISION / PLANNING │
                     │                     │
                     │ Algorithmic         │
                     │ Robot AI            │
                     │ AI Global Planning  │
                     └──────────┬──────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │ COLLISION AVOIDANCE │
                     └──────────┬──────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │  DETERMINISTIC      │
                     │  SAFETY GUARD       │
                     │                     │
                     │ Emergency override  │
                     │ Safe constraints    │
                     └──────────┬──────────┘
                                │
                                ▼
                           CONTROLLER
                                │
                                ▼
                              🤖 AMR
                                │
                                │ telemetry
                                │ intent
                                ▼
                           Other AMRs
```

## Fundamental Principle

**Centralized:**

> The server knows the warehouse, manages system state, and announces available tasks.

**Distributed:**

> The robots communicate and collectively determine task ownership and coordinate their operation.

**Local:**

> Each robot decides how to execute its assigned task in the changing environment.

**AI-enabled:**

> Neural networks may provide robot-level decisions or global planning/rerouting depending on the selected operating mode.

**Safety-critical:**

> Deterministic collision avoidance and emergency safety constraints have higher authority than AI or normal planning.

The resulting system is therefore a **distributed, multi-agent AMR fleet with interchangeable algorithmic and AI decision layers, peer-to-peer coordination, and deterministic safety guard rails**.

---

# 38. Robot Charge Points & Dynamic Fleet Roster

### 38.1 Charge Spot Allocation & Persistence
Each AMR configured in the fleet has an assigned charge spot persisted in `backend/state/amrs.json` as `homeBay`:
* **Primary Charging Pads (`kind: "pad"`):** Robots up to the layout's pad count are designated pad owners. Each owner is assigned a specific charging bay (`padId`) and rests at the pad centroid (`spawnPoint`).
* **Standby Overflow Slots (`kind: "standby"`):** Robots exceeding pad capacity are assigned deterministic standby slots located along the charging bay buffer lane.

When new AMRs are created, charge spots are automatically assigned to the lowest-index free pad (or lowest-index free standby slot if all pads are owned) without requiring manual coordinate inputs. When an AMR is removed, its spot is immediately released back to the allocation pool.

### 38.2 Centroid Navigation & Effective Charging
Charging pads define a bounding box `[x, y, width, height]` and a centroid `spawnPoint`.
* Motion planning navigates robots to the pad centroid `(spawnPoint.x, spawnPoint.y)` rather than bounding-box corners.
* Charging is effective only within `PAD_OCCUPANCY = 0.6 m` of the pad centroid (`CHARGE_RATE_PER_SEC = 8.0 %/s`).

### 38.3 Smart Turnover & Yielding Rules
* **Pad Owner Trickle-Parking:** Pad owners rest on their assigned pad when idle, maintaining 100% battery.
* **Owner Yielding to Needy Robots:** A pad owner vacates its pad and retreats to its standby slot whenever any off-pad robot drops below the warning threshold (`battery < BATTERY_WARN_THRESHOLD = 25%`).
* **Standby Robot Seeking:** Standby robots wait at their standby parking slot when all pads are occupied. As soon as a pad becomes free, they seek the lowest-index free pad to charge.
* **Immediate Vacation at 100%:** An overflow robot charging on another robot's pad vacates immediately upon reaching 100% battery, freeing the pad for other AMRs.

### 38.4 Dynamic Roster Updates (Zero-Restart Fleet Modification)
Fleet modification occurs dynamically without restarting running processes:
* **Zenoh Topic:** `topics.CONTROL_ROSTER_UPDATE = "control/roster/update"`
* **8-Field Roster Token:** `id:x:y:kind:slot:padId:standbyX:standbyY` (with legacy 3-field backwards-compatibility).
* **Coordinator Integration:** `ServerNode` subscribes to roster updates, rebuilds `_pad_owners` and `_standby_spots`, and broadcasts updated `world/state` with `assignedRobotId` per pad and active `standbySpots`.
* **Safe Removal:** AMRs must be stopped before removal (`DELETE /api/amrs/{id}` returns 400 if still running), preventing orphan processes or phantom states.

