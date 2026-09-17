Starting the system (2 terminals)
cd dashboard && npm run fleet-manager     # terminal 1
cd dashboard && npm run dev               # terminal 2 → http://localhost:5173
Use the Fleet Control strip: START ALL brings up Zenoh→bridge→coordinator→AMRs; create AMRs with id+x+y (persisted); per-component START/STOP/RESTART/REMOVE with confirm; log viewer for every process. Or run tests manually:
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/ -q
One behavioral note: AMRs created while the coordinator is running are added to its roster on the coordinator's next restart (a running coordinator keeps its launched argv).