## Live stack, 4 steps (all parts I built/verified this session):
### 1. Start the infra (router + WS bridge):
```
cd dashboard && bash scripts/zenohd.sh
```
Starts zenohd on tcp/127.0.0.1:7447 and the WebSocket bridge on ws/127.0.0.1:10000. Binaries are already in dashboard/tools/zenoh/.
### 2. Start the Python coordinator (from backend/):
```
cd backend
```
```
PYTHONPATH=. ../.venv/bin/python -m server.server_node --preset MICRO_FULFILLMENT --tasks 1 --url tcp/127.0.0.1:7447
```
MICRO_FULFILLMENT has 2 robots (AMR1, AMR2). Use --preset ECOMMERCE for 3 and spawn AMR3 below.
### 3. Start one robot per roster entry *(SEPARATE TERMINALS)*:
```
PYTHONPATH=. ../.venv/bin/python robot/robot_node.py --id AMR1 --url tcp/127.0.0.1:7447
```
```
PYTHONPATH=. ../.venv/bin/python robot/robot_node.py --id AMR2 --url tcp/127.0.0.1:7447
```
### 4. Run the dashboard:
```
cd dashboard && npm run dev
```
Open http://localhost:5173. Once both robots report in, the coordinator auto-announces 1 task (auction → assign → completion); use the Tasks panel's Manual/Random/Same Dropoff buttons to create more live tasks, and Assign/Cancel to command the fleet.