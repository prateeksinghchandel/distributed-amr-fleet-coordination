"""
reservation.py — Distributed region reservations and deterministic arbitration.

Chokepoint coordination is *fully distributed*: there is no central lock server.
Every robot derives the same region geometry from the shared world state (see
:mod:`regionmap`) and runs identical pure functions over the shared fleet
telemetry snapshot. A robot claims a region by advertising a reservation within
its telemetry payload; peers evaluate the same fleet snapshot and apply the same
arbitration rules, so the outcome converges without gossip.

Arbitration order per conflicting region:
  1. robots physically inside the region win (they are already there),
  2. higher task priority,
  3. earlier claim time,
  4. closer to the region entry,
  5. lexicographically smaller robot id.

Reservations automatically expire when the claimer's expected-exit time plus a
grace period has passed, or when the claimer goes offline.
"""

from __future__ import annotations
from typing import Optional

RES_GRACE_S = 1.5          # extra time a claim stays respected after expected exit
RES_LIFETIME_S = 25.0      # hard ceiling on any single claim
RES_LOOKAHEAD_M = 4.0      # how early a robot claims the next region on its path
REGION_OCCUPANCY_PAD_M = 0.05


def _peer_id(peer: dict) -> str:
    return str(peer.get("robotId") or peer.get("id") or "")


def _peer_pos(peer: dict) -> tuple[float, float]:
    return (float(peer.get("x", 0.0)), float(peer.get("y", 0.0)))


def claim_dict(region_id: str, robot_id: str, claimed_at: float, expected_exit: float,
               priority: int, distance_to_entry: float) -> dict:
    return {
        "regionId": region_id,
        "robotId": robot_id,
        "claimedAt": round(claimed_at, 3),
        "expectedExit": round(expected_exit, 3),
        "priority": int(priority),
        "distanceToEntry": round(distance_to_entry, 3),
    }


def active_claim(peer: dict, region_id: str, now: float) -> Optional[dict]:
    """Return the peer's claim for ``region_id`` if it is still active."""
    if not peer.get("online", True):
        return None
    nav = peer.get("nav") or {}
    res = nav.get("reservation")
    if not isinstance(res, dict):
        return None
    if res.get("regionId") != region_id:
        return None
    claimed_at = float(res.get("claimedAt", 0.0))
    expected_exit = float(res.get("expectedExit", claimed_at))
    if now > expected_exit + RES_GRACE_S or now > claimed_at + RES_LIFETIME_S:
        return None
    return res


def peer_inside(region, peer: dict, pad: float = REGION_OCCUPANCY_PAD_M) -> bool:
    x, y = _peer_pos(peer)
    if not region:
        return False
    return region.contains(x, y, pad=pad)


def select_holder(region, peers: list[dict], now: float) -> Optional[str]:
    """Deterministic winner for a region given the current fleet snapshot.

    ``peers`` should contain the candidate robots (claimers and/or robots
    physically inside the region). Returns the winning robot id or ``None``.
    """
    candidates = []
    for peer in peers:
        rid = _peer_id(peer)
        if not rid:
            continue
        if not peer.get("online", True):
            continue
        inside = 0 if peer_inside(region, peer) else 1
        nav = peer.get("nav") or {}
        res = nav.get("reservation") or {}
        priority = int(res.get("priority", 0) or 1)
        claimed_at = float(res.get("claimedAt", float("inf")))
        distance = float(res.get("distanceToEntry", float("inf")))
        rank = (inside, -priority, claimed_at, distance, rid)
        candidates.append((rank, rid))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def select_backup(region, holder_id: str, peers: list[dict], now: float) -> Optional[str]:
    """Pick the robot that must yield/back up when several are inside a region.

    The holder keeps priority; the first non-holder (by the same ranking) is
    asked to give way so the loop is deterministic.
    """
    candidates = [p for p in peers if _peer_id(p) and _peer_id(p) != holder_id
                  and peer_inside(region, p)]
    if not candidates:
        return None
    winner = select_holder(region, candidates, now)
    if winner is None:
        return None
    for peer in candidates:
        if _peer_id(peer) == winner:
            return _peer_id(peer)
    return None