"""charge_allocation.py — Charge spot allocation and roster encoding for AMRs.

Manages assignment of warehouse charging pads (primary bays) and standby slots
(overflow waiting areas), with persistence in amrs.json (homeBay) and 8-field
roster token encoding across Fleet Manager, Coordinator and AMRs.
"""

from __future__ import annotations
from typing import Optional, Sequence


def compute_standby_slots(layout, count: int = 20) -> list[dict]:
    """Compute deterministic coordinates for standby (overflow) parking slots."""
    pads = layout.charging_pads() if hasattr(layout, "charging_pads") else []
    if pads:
        min_pad_x = min(p["x"] for p in pads)
        sx = max(1.0, round(min_pad_x - 1.5, 2))
    else:
        sx = max(1.0, round(getattr(layout, "width", 30.0) - 4.0, 2))

    max_y = getattr(layout, "height", 20.0) - 2.0
    slots = []
    for i in range(max(count, 1)):
        sy = round(2.0 + (i % 8) * 2.0, 2)
        x_offset = (i // 8) * 1.5
        slots.append({
            "slot": i,
            "x": round(max(1.0, sx - x_offset), 2),
            "y": round(min(max_y, sy), 2),
        })
    return slots


def allocate_charge_spots(layout, robot_ids: Sequence[str]) -> list[dict]:
    """Allocate charge spots (pads or standby) for a list of robot IDs in a layout.

    Robots up to the pad count are assigned charging pads as pad owners (spawning
    at the pad centre). Robots beyond the pad count are allocated standby slots.
    """
    pads = layout.charging_pads() if hasattr(layout, "charging_pads") else []
    num_pads = len(pads)
    standby_slots = compute_standby_slots(layout, count=max(len(robot_ids) - num_pads, 10))

    roster = []
    for idx, rid in enumerate(robot_ids):
        if idx < num_pads:
            pad = pads[idx]
            spawn = pad["spawnPoint"]
            entry = {
                "id": rid,
                "x": float(spawn["x"]),
                "y": float(spawn["y"]),
                "homeBay": {
                    "kind": "pad",
                    "slot": idx,
                    "padId": pad["id"],
                    "x": float(spawn["x"]),
                    "y": float(spawn["y"]),
                },
            }
        else:
            standby_idx = idx - num_pads
            slot_pos = standby_slots[standby_idx] if standby_idx < len(standby_slots) else {
                "slot": standby_idx, "x": 2.0, "y": 2.0
            }
            entry = {
                "id": rid,
                "x": float(slot_pos["x"]),
                "y": float(slot_pos["y"]),
                "homeBay": {
                    "kind": "standby",
                    "slot": standby_idx,
                    "padId": None,
                    "x": float(slot_pos["x"]),
                    "y": float(slot_pos["y"]),
                },
            }
        roster.append(entry)
    return roster


def assign_charge_spot(layout, existing_spots: dict[str, dict], amr_id: str) -> dict:
    """Auto-assign a charge spot (pad or standby) for a newly added AMR.

    Picks the lowest-index unoccupied charging pad. If all pads are owned,
    assigns the lowest-index unoccupied standby slot.
    """
    pads = layout.charging_pads() if hasattr(layout, "charging_pads") else []
    assigned_pad_ids = {
        spot["padId"] for spot in existing_spots.values()
        if spot and spot.get("kind") == "pad" and spot.get("padId")
    }

    free_pad_tuple = None
    for idx, pad in enumerate(pads):
        if pad["id"] not in assigned_pad_ids:
            free_pad_tuple = (idx, pad)
            break

    if free_pad_tuple is not None:
        idx, pad = free_pad_tuple
        spawn = pad["spawnPoint"]
        return {
            "id": amr_id,
            "x": float(spawn["x"]),
            "y": float(spawn["y"]),
            "homeBay": {
                "kind": "pad",
                "slot": idx,
                "padId": pad["id"],
                "x": float(spawn["x"]),
                "y": float(spawn["y"]),
            },
        }

    # All pads are occupied: allocate lowest-index free standby slot
    used_standby_slots = {
        spot["slot"] for spot in existing_spots.values()
        if spot and spot.get("kind") == "standby" and spot.get("slot") is not None
    }
    slot_idx = 0
    while slot_idx in used_standby_slots:
        slot_idx += 1

    standby_slots = compute_standby_slots(layout, count=slot_idx + 1)
    slot_pos = standby_slots[slot_idx]
    return {
        "id": amr_id,
        "x": float(slot_pos["x"]),
        "y": float(slot_pos["y"]),
        "homeBay": {
            "kind": "standby",
            "slot": slot_idx,
            "padId": None,
            "x": float(slot_pos["x"]),
            "y": float(slot_pos["y"]),
        },
    }


def roster_token(entry: dict) -> str:
    """Format an entry into an 8-field roster token string.

    Format: id:x:y:kind:slot:padId:standbyX:standbyY
    """
    rid = str(entry["id"])
    x = f"{float(entry.get('x', 0.0)):.2f}".rstrip("0").rstrip(".")
    y = f"{float(entry.get('y', 0.0)):.2f}".rstrip("0").rstrip(".")
    hb = entry.get("homeBay") or {}
    kind = hb.get("kind", "pad")
    slot = str(hb.get("slot", 0))
    pad_id = hb.get("padId") or ""
    if kind == "standby":
        sb_x = f"{float(hb.get('x', entry.get('x', 0.0))):.2f}".rstrip("0").rstrip(".")
        sb_y = f"{float(hb.get('y', entry.get('y', 0.0))):.2f}".rstrip("0").rstrip(".")
    else:
        sb_x = ""
        sb_y = ""
    return f"{rid}:{x}:{y}:{kind}:{slot}:{pad_id}:{sb_x}:{sb_y}"


def parse_roster_token(token: str) -> dict:
    """Parse an 8-field or legacy 3-field roster token string into a dict."""
    parts = token.strip().split(":")
    if len(parts) >= 8:
        rid, x, y, kind, slot, pad_id, sb_x, sb_y = parts[:8]
        is_pad = (kind == "pad")
        return {
            "id": rid,
            "x": float(x),
            "y": float(y),
            "homeBay": {
                "kind": kind,
                "slot": int(slot) if slot else 0,
                "padId": pad_id if pad_id else None,
                "x": float(x) if is_pad else (float(sb_x) if sb_x else float(x)),
                "y": float(y) if is_pad else (float(sb_y) if sb_y else float(y)),
            },
        }
    if len(parts) >= 3:
        return {
            "id": parts[0],
            "x": float(parts[1]),
            "y": float(parts[2]),
            "homeBay": None,
        }
    return {"id": parts[0], "x": 0.0, "y": 0.0, "homeBay": None}
