1. **Central Server → knows the warehouse and announces tasks**
2. **Fleet/P2P Layer → robots collectively decide who does each task**
3. **Individual AMR → decides how to physically execute its task safely**

The central server should **not** continuously control the robots. That is what makes the system genuinely distributed.

# 1. Overall Architecture

```text
                         ┌─────────────────────────┐
                         │      WEB DASHBOARD       │
                         │                         │
                         │ • Warehouse editor      │
                         │ • Robot visualization   │
                         │ • Task generation       │
                         │ • Fleet status          │
                         │ • Experiment controls   │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │      FLEET SERVER        │
                         │                         │
                         │ • Warehouse state       │
                         │ • Task manager          │
                         │ • Task generator        │
                         │ • Telemetry collector   │
                         │ • Robot health monitor  │
                         │ • Logging               │
                         └────────────┬────────────┘
                                      │
                                ZENOH NETWORK
                                      │
             ┌────────────────────────┼────────────────────────┐
             │                        │                        │
             ▼                        ▼                        ▼
      ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
      │     AMR 1    │◄──────►│     AMR 2    │◄──────►│     AMR 3    │
      │              │        │              │        │              │
      │ P2P Agent    │◄──────►│ P2P Agent    │◄──────►│ P2P Agent    │
      │ Auction      │        │ Auction      │        │ Auction      │
      │ Planner      │        │ Planner      │        │ Planner      │
      │ Controller   │        │ Controller   │        │ Controller   │
      │ Safety       │        │ Safety       │        │ Safety       │
      └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
             │                       │                       │
             ▼                       ▼                       ▼
          Robot 1                 Robot 2                 Robot 3
```

There are therefore **two different communication directions**:

### Server ↔ Robots

Used mainly for:

* task announcements
* telemetry
* warehouse/environment information
* configuration
* monitoring

### Robot ↔ Robot

Used for:

* auctions
* task allocation
* position/state sharing
* trajectory/intent sharing
* priority negotiation
* collision coordination
* failure information

This second communication path is the important part of your **peer-to-peer fleet coordination**.

---

# 2. The Fleet Server

The server is **not the fleet's decision-making brain**.

Think of it as the **warehouse supervisor/observer**.

Its responsibilities are:

```text
                    SERVER
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Warehouse        Tasks          Telemetry
    Manager         Manager         Manager
        │              │              │
        ▼              ▼              ▼
   Environment      Task Queue     Robot States
```

## 2.1 Warehouse Manager

Maintains:

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
    [5,5] → [8,10]
    [15,3] → [18,12]
```

The dashboard can modify these while the simulation is running.

---

# 3. Task Manager

The server creates and announces tasks.

A task looks roughly like:

```text
Task 42

Pickup:  (4.5, 7.2)
Dropoff: (24.2, 15.8)

Priority: NORMAL
Status: UNASSIGNED
```

The server publishes:

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

**The server does not choose the robot.**

This is where your distributed auction begins.

---

# 4. P2P Fleet Coordination

Every robot runs a **fleet agent**.

Suppose Task 42 is announced.

```text
                 TASK 42
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
        AMR1      AMR2      AMR3
          │         │         │
        calculate own bid
          │         │         │
          ▼         ▼         ▼
         32        18        25
          │         │         │
          └─────────┼─────────┘
                    ▼
                 AMR2 wins
```

The important thing is:

**AMR2 calculates its own bid.**

It might consider:

```text
distance to pickup
distance to dropoff
current workload
battery
estimated travel time
current congestion
task priority
```

For example:

```text
bid =
    travel_cost
  + congestion_cost
  + battery_cost
  + workload_cost
```

The exact formula can evolve later.

---

# 5. Why This Is P2P

The server says:

> "There is a new task."

It does **not** say:

> "AMR2, you do it."

Instead:

```text
Server
   │
   │ NEW TASK
   ▼
All robots
   │
   ├── AMR1 calculates bid
   ├── AMR2 calculates bid
   └── AMR3 calculates bid
            │
            ▼
       Robots communicate
            │
            ▼
       Winner determined
```

The robots collectively make the assignment decision.

That is one of the core contributions of your system.

---

# 6. What Happens After Winning?

Suppose AMR2 wins.

```text
Task
 ↓
AMR2
 ↓
Local planner
 ↓
Path
 ↓
Trajectory
 ↓
Local controller
 ↓
Robot
```

AMR2 calculates its own route.

For example:

```text
Start
  │
  ├──→→→
  │     ↓
  │     ↓
  │     └────→→→
  │             ↓
  └──────────── Goal
```

Initially:

**A*** should be your baseline path planner.

Later you can compare it with other approaches.

---

# 7. Path Planning vs Collision Avoidance

These should be treated as **different systems**.

This distinction is extremely important.

## Global path planning

Answers:

> "How should I get from A to B?"

Example:

```text
A*:

Start → → → ↓ ↓ → → → Goal
```

It considers:

* warehouse
* static obstacles
* destination

---

## Local collision avoidance

Answers:

> "Given what is happening RIGHT NOW, should I continue following that path?"

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

Both have valid paths.

But those paths conflict.

The local systems may decide:

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
AMR1 → change trajectory
```

This happens continuously while the robot is moving.

---

# 8. Robot's Internal Architecture

Each AMR should therefore contain several modules.

```text
                    AMR NODE
                       │
       ┌───────────────┼────────────────┐
       │               │                │
       ▼               ▼                ▼
 Communication     Fleet Agent      Telemetry
       │               │
       │               ├── Auction
       │               ├── Task state
       │               ├── Priority
       │               └── P2P coordination
       │
       └─────────────────────┐
                             ▼
                       Task Executor
                             │
                             ▼
                      Global Planner
                             │
                             ▼
                     Local Planner
                             │
                             ▼
                  Collision Avoidance
                             │
                             ▼
                       Controller
                             │
                             ▼
                          ROBOT
```

---

# 9. Telemetry

Every AMR continuously broadcasts information.

Something like:

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

The server receives this for the dashboard.

But importantly:

**other robots also receive relevant state information.**

For example:

```text
AMR1
  │
  ├── position
  ├── velocity
  ├── trajectory
  └── intention
          │
          ▼
       AMR2
```

This allows AMR2 to reason about AMR1.

---

# 10. Robot Intentions

Telemetry tells robots:

> "Where is the other robot?"

Intent information tells them:

> "What is the other robot planning to do?"

For example:

```text
AMR1:

Current:
(10,10)

Planning:
(10,10)
    ↓
(10,15)
    ↓
(20,15)
```

AMR2 can detect:

> "AMR1 intends to pass through the intersection I am approaching."

That is much more useful than just knowing AMR1's current position.

---

# 11. Distributed Collision Resolution

Imagine:

```text
                 AMR1
                   ↓
                   ↓
             ──────X──────
                   ↑
                   ↑
                 AMR2
```

Each robot detects a potential conflict.

They exchange:

```text
position
velocity
planned path
ETA
task priority
intent
```

Then a coordination mechanism determines:

```text
AMR1 → GO
AMR2 → WAIT
```

The decision should not simply be:

> "Robot with smaller ID wins."

You can eventually use a priority function such as:

```text
priority =
    task_priority
  + waiting_time
  + urgency
  + other factors
```

---

# 12. Deadlock Handling

Collision avoidance isn't enough.

Consider:

```text
             AMR1
               ↓
               │
        ───────┼───────
               │
               ↑
             AMR2
```

Or more complex warehouse choke points:

```text
      A →
          ┌─────┐
          │     │
          │     │
          └─────┘
                ← B
```

Robots can enter situations where everyone waits.

Your system needs:

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
One robot gets priority
   ↓
Other robot waits/reroutes
```

This should be a dedicated subsystem rather than something buried inside the path planner.

---

# 13. Failure Detection

Telemetry also gives you robot health monitoring.

```text
AMR3

heartbeat
heartbeat
heartbeat
heartbeat
   X
```

Other nodes notice:

```text
No heartbeat
      ↓
Timeout
      ↓
AMR3 suspected failed
```

Then the fleet can react.

If AMR3 owned Task 27:

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

This is an important part of making the system **fault tolerant**.

---

# 14. AI Layer

I would make AI an **optional optimization layer**, not the fundamental safety layer.

Your architecture could eventually look like:

```text
             LOCAL ROBOT
                  │
        ┌─────────▼─────────┐
        │ Deterministic     │
        │ Safety Layer      │
        │                   │
        │ • collision       │
        │ • emergency stop  │
        │ • constraints     │
        │ • deadlock rules  │
        └─────────┬─────────┘
                  │
        ┌─────────▼─────────┐
        │ Planning /        │
        │ Optimization      │
        │                   │
        │ A*                │
        │ ORCA              │
        │ AI model          │
        │ learned policy    │
        └───────────────────┘
```

AI could later help with:

* congestion prediction
* task bidding
* route selection
* deadlock prediction
* adaptive speed
* parameter selection

But the robot should still have deterministic safety constraints.

---

# 15. Dashboard Architecture

Your React + Canvas dashboard can be structured as:

```text
                 REACT DASHBOARD
                       │
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
     Warehouse       Fleet         Tasks
       Editor        Monitor        Panel
          │            │             │
          └────────────┼─────────────┘
                       │
                    API / Zenoh
                       │
                       ▼
                    SERVER
```

### Warehouse editor

```text
Width: 30m
Height: 20m

[Add obstacle]
[Remove obstacle]
```

### Task controls

```text
Task Generation

○ Manual
○ Same dropoff
○ Random pickup/dropoff

Number of tasks: 20

[Generate]
```

### Fleet monitor

```text
AMR1   ONLINE    Task 12    82%
AMR2   ONLINE    Task 15    64%
AMR3   OFFLINE   ---        71%
```

### Visualization

Canvas displays:

```text
┌──────────────────────────────────────┐
│                                      │
│   ████                               │
│   ████       🤖1 ──────→             │
│                                      │
│                🤖2                   │
│                       █████           │
│                                      │
│                    🤖3 ───→          │
│                                      │
└──────────────────────────────────────┘
```

You can additionally display:

* paths
* planned trajectories
* robot direction
* task pickup/dropoff
* collision warnings
* reservations
* communication state
* deadlock state

---

# 16. Complete Data Flow

Now let's follow one task through the **entire system**.

### Step 1 — User creates warehouse

```text
Dashboard
    ↓
Server
    ↓
Warehouse state
```

---

### Step 2 — Server generates task

```text
Task Generator
      ↓
Task 42
      ↓
Zenoh
```

---

### Step 3 — All robots receive it

```text
              Task 42
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
     AMR1      AMR2      AMR3
```

---

### Step 4 — Robots calculate bids

```text
AMR1 → 31
AMR2 → 17
AMR3 → 26
```

---

### Step 5 — Distributed winner selection

```text
AMR2 wins
```

---

### Step 6 — AMR2 plans its route

```text
Task
 ↓
A*
 ↓
Path
```

---

### Step 7 — AMR2 starts moving

```text
Path
 ↓
Local trajectory
 ↓
Controller
 ↓
Robot
```

---

### Step 8 — AMR2 continuously observes other robots

```text
AMR1 telemetry ──┐
AMR3 telemetry ──┼──→ AMR2
                 │
AMR intentions ──┘
```

---

### Step 9 — Conflict occurs

```text
AMR2 detects AMR1
will occupy same
intersection
```

Local coordination occurs.

```text
AMR2 → WAIT
AMR1 → GO
```

---

### Step 10 — AMR2 continues

Once the intersection is clear:

```text
WAIT
 ↓
SAFE
 ↓
CONTINUE
```

---

### Step 11 — Robot reaches pickup

```text
MOVING_TO_PICKUP
        ↓
     PICKING
```

---

### Step 12 — Robot goes to dropoff

```text
PICKING
   ↓
MOVING_TO_DROPOFF
   ↓
DROPPING
   ↓
COMPLETED
```

---

### Step 13 — Another task appears

AMR2 can participate in another auction.

---

# 17. What the Server Does vs What Robots Do

This distinction should remain extremely clear in your implementation.

| Function                  |  Server  |    Robot    |
| ------------------------- | :------: | :---------: |
| Warehouse configuration   |     ✓    |     Read    |
| Dashboard                 |     ✓    |      —      |
| Task generation           |     ✓    |      —      |
| Task announcement         |     ✓    |   Receive   |
| Task auction              |     —    |    **✓**    |
| Task winner decision      |     —    |    **✓**    |
| Local path planning       | Optional |    **✓**    |
| Local trajectory planning |     —    |    **✓**    |
| Collision avoidance       |     —    |    **✓**    |
| Deadlock handling         |  Monitor |    **✓**    |
| Robot telemetry           |  Collect |   **Send**  |
| Robot health monitoring   |     ✓    |   **P2P**   |
| Failure detection         |     ✓    |    **✓**    |
| Task reassignment         | Announce | **Auction** |
| Visualization             |     ✓    |      —      |

The most important columns are:

> **Server = observe + organize**

> **Robots = decide + execute + coordinate**

---

# 18. Final Software Architecture

I'd organize the actual project roughly like this:

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
│   ├── avoidance/
│   │   ├── collision.py
│   │   ├── deadlock.py
│   │   └── reservation.py
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
    ├── communication/
    └── integration/
```

This structure also supports your incremental development strategy because each major research component has a relatively isolated location.

---

# 19. The Core Concept

If you have to explain the entire project in one diagram, I'd use this:

```text
                         ┌───────────────┐
                         │   DASHBOARD   │
                         └───────┬───────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │      FLEET SERVER      │
                    │                        │
                    │ Warehouse              │
                    │ Task Generation        │
                    │ Telemetry              │
                    │ Monitoring              │
                    └───────────┬────────────┘
                                │
                         Task announcement
                                │
                         ═══ ZENOH ═══
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
              ▼                 ▼                 ▼
          ┌───────┐         ┌───────┐         ┌───────┐
          │ AMR 1 │◄───────►│ AMR 2 │◄───────►│ AMR 3 │
          └───┬───┘         └───┬───┘         └───┬───┘
              │                 │                 │
              │       P2P AUCTION /             │
              │       COORDINATION              │
              │                 │                 │
              └─────────────────┼─────────────────┘
                                │
                         Winning robot
                                │
                                ▼
                       ┌────────────────┐
                       │ GLOBAL PLANNER │
                       │      A*        │
                       └───────┬────────┘
                               │
                               ▼
                       ┌────────────────┐
                       │ LOCAL PLANNER  │
                       └───────┬────────┘
                               │
                 ┌─────────────▼─────────────┐
                 │   LOCAL SAFETY / CONTROL  │
                 │                           │
                 │ Collision avoidance       │
                 │ Speed adjustment          │
                 │ Trajectory adjustment     │
                 │ Deadlock handling         │
                 │ Emergency response        │
                 └─────────────┬─────────────┘
                               │
                               ▼
                            🤖 AMR
                               │
                               │
                         telemetry /
                         intentions
                               │
                               ▼
                         Other AMRs
```

### The fundamental principle

**Centralized:**

> The server knows the warehouse and tells robots what tasks exist.

**Distributed:**

> The robots decide among themselves who performs those tasks and coordinate with each other.

**Local:**

> Each robot decides how to safely execute its assigned task in the constantly changing environment.

That gives you a coherent **Edge-AI / distributed fleet coordination architecture** rather than just a multi-robot simulator with a server attached.
