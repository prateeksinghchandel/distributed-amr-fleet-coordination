Starting the system (2 terminals)
cd dashboard && npm run fleet-manager     # terminal 1
cd dashboard && npm run dev               # terminal 2 → http://localhost:5173
Use the Fleet Control strip: START ALL brings up Zenoh→bridge→coordinator→AMRs; create AMRs with id+x+y (persisted); per-component START/STOP/RESTART/REMOVE with confirm; log viewer for every process. Or run tests manually:
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/ -q
One behavioral note: AMRs created while the coordinator is running are added to its roster on the coordinator's next restart (a running coordinator keeps its launched argv).

Auction mode (server-auction vs p2p-auction)
- Default is server-auction, which is unchanged: the coordinator runs the auction and publishes tasks/assigned.
- For peer-to-peer auctions, set auctionMode to "P2P_AUCTION" on the coordinator before starting it (Fleet Control strip / POST /api/coordinator/configure, field "auctionMode": "P2P_AUCTION"), or pass --mode p2p_auction to server.server_node and robot/robot_node.py. In P2P mode the coordinator is a passive ledger: robots bid among themselves, the winner self-commits on auction/commit and self-publishes tasks/assigned after its commit window; the server never assigns. world/state reports the active "auctionMode".
- Test coverage: backend/tests/test_p2p_auction.py (18 scenario tests in P2P mode) and backend/tests/test_task_manager_lifecycle.py (AUCTIONING lifecycle, one-active-assignment-per-robot, eligible-winner filtering, assignment watchdog, completed-robot reuse, all-busy defer, metrics).