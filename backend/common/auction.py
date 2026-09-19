"""
auction.py — Shared auction protocol constants and deterministic winner selection.

This module is the single source of truth for how an auction round is keyed
(``auctionId``), how winners are selected, and how the P2P commit protocol is
timed. Both the server and every robot import from here so the logic can never
drift between implementations.

Modes
-----
* ``SERVER_AUCTION`` (default) — the coordinator is the sole auction finalizer.
  It collects bids, runs :func:`select_winner` and publishes the assignment.
  Processes run with the historical semantics: fast-track when every eligible
  robot has reported, otherwise finalize at the deadline.

* ``P2P_AUCTION`` — robots exchange bids with each other and independently run
  :func:`select_winner` at the deadline. The winning robot publishes an
  ``AUCTION_COMMIT`` and self-publishes its task assignment. The server acts as
  a passive task ledger: it applies the first valid commit idempotently, but it
  never selects the winner, never publishes the assignment for an auctioned task
  and never publishes the auction result.
"""

from __future__ import annotations
import math
from typing import Any, Mapping, Optional


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

class AuctionMode:
    SERVER_AUCTION = "SERVER_AUCTION"
    P2P_AUCTION = "P2P_AUCTION"
    VALID = {SERVER_AUCTION, P2P_AUCTION}

    @classmethod
    def normalize(cls, value: Optional[str]) -> str:
        value = (value or cls.SERVER_AUCTION).upper()
        return value if value in cls.VALID else cls.SERVER_AUCTION


# ---------------------------------------------------------------------------
# Timing constants (seconds, synchronised between server and robots)
# ---------------------------------------------------------------------------

DEADLINE_S = 1.0                 # bid collection window after an announce
COMMIT_WINDOW_S = 0.6            # winner waits this long before self-assign
P2P_COMMIT_GRACE_S = 1.4         # server waits announce+DEADLINE+grace for a commit


# ---------------------------------------------------------------------------
# Protocol phase names
# ---------------------------------------------------------------------------

B_ROUND = "ROUND"                 # awaiting bids (past announce)
B_PENDING = "PENDING"             # awaiting bids (pre-announce)
B_WAITING_FINALIZATION = "WAITING_FINALIZATION"
B_COMMITTED = "COMMITTED"
B_SELF_ASSIGNED = "SELF_ASSIGNED"
B_CONFLICT = "CONFLICT"
B_ABORTED = "ABORTED"
B_CANCELLED = "CANCELLED"
B_TIMEOUT = "TIMEOUT"


# ---------------------------------------------------------------------------
# Deterministic winner selection
# ---------------------------------------------------------------------------

def select_winner(bids: Mapping[str, Any]) -> Optional[str]:
    """
    Pick a winner from a round's bids.

    * Only bids with a numeric ``cost`` participate (a ``None`` cost is the
      robot saying "not interested").
    * The lowest numeric cost wins.
    * Ties are broken deterministically by the lexicographically smallest
      robot id, so every participant that sees the same bid set derives the
      same winner.
    """
    eligible = {
        robot_id: bid["cost"]
        for robot_id, bid in bids.items()
        if bid.get("cost") is not None and _is_finite_cost(bid["cost"])
    }
    if not eligible:
        return None
    return min(eligible, key=lambda robot_id: (eligible[robot_id], robot_id))


def _is_finite_cost(cost: Any) -> bool:
    try:
        return math.isfinite(float(cost))
    except (TypeError, ValueError):
        return False