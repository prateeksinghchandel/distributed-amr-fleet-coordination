"""Unit tests for charge spot allocation, standby slots, and roster encoding."""

from __future__ import annotations

import pytest
from server.warehouse import build_from_preset
from fleet_manager.charge_allocation import (
    compute_standby_slots,
    allocate_charge_spots,
    assign_charge_spot,
    promote_standby_to_pads,
    roster_token,
    parse_roster_token,
)
from fleet_manager.amr_store import AmrStore, SettingsStore
from fleet_manager.process_manager import ProcessManager, _preset_roster


async def _zenoh_up(_port: int) -> bool:
    return True


def test_allocate_under_capacity():
    """When robot count <= charging pads, all robots get pad assignments at centroid."""
    layout = build_from_preset("ECOMMERCE")  # 3 pads
    pads = layout.charging_pads()
    assert len(pads) == 3

    robot_ids = ["AMR1", "AMR2", "AMR3"]
    roster = allocate_charge_spots(layout, robot_ids)

    assert len(roster) == 3
    for idx, entry in enumerate(roster):
        hb = entry["homeBay"]
        assert hb is not None
        assert hb["kind"] == "pad"
        assert hb["slot"] == idx
        assert hb["padId"] == pads[idx]["id"]
        assert entry["x"] == pytest.approx(pads[idx]["spawnPoint"]["x"])
        assert entry["y"] == pytest.approx(pads[idx]["spawnPoint"]["y"])
        assert hb["x"] == pytest.approx(pads[idx]["spawnPoint"]["x"])
        assert hb["y"] == pytest.approx(pads[idx]["spawnPoint"]["y"])


def test_allocate_overflow_standby():
    """Robots exceeding pad capacity receive deterministic standby slots."""
    layout = build_from_preset("ECOMMERCE")  # 3 pads
    pads = layout.charging_pads()

    robot_ids = ["AMR1", "AMR2", "AMR3", "AMR4", "AMR5"]
    roster = allocate_charge_spots(layout, robot_ids)

    assert len(roster) == 5
    # First 3 are pads
    for i in range(3):
        assert roster[i]["homeBay"]["kind"] == "pad"
        assert roster[i]["homeBay"]["padId"] == pads[i]["id"]

    # Next 2 are standby
    standby_slots = compute_standby_slots(layout, count=2)
    for i, s_idx in enumerate([3, 4]):
        hb = roster[s_idx]["homeBay"]
        assert hb["kind"] == "standby"
        assert hb["slot"] == i
        assert hb["padId"] is None
        assert roster[s_idx]["x"] == pytest.approx(standby_slots[i]["x"])
        assert roster[s_idx]["y"] == pytest.approx(standby_slots[i]["y"])


def test_assign_charge_spot_next():
    """assign_charge_spot fills lowest-index available pad, then lowest standby slot."""
    layout = build_from_preset("ECOMMERCE")  # 3 pads: BAY-1, BAY-2, BAY-3
    pads = layout.charging_pads()

    # Simulate existing spots where BAY-1 and BAY-3 are assigned, BAY-2 is free
    existing_spots = {
        "AMR1": {"kind": "pad", "slot": 0, "padId": pads[0]["id"]},
        "AMR3": {"kind": "pad", "slot": 2, "padId": pads[2]["id"]},
    }

    assigned = assign_charge_spot(layout, existing_spots, "AMR2")
    assert assigned["homeBay"]["kind"] == "pad"
    assert assigned["homeBay"]["padId"] == pads[1]["id"]
    assert assigned["homeBay"]["slot"] == 1
    assert assigned["x"] == pytest.approx(pads[1]["spawnPoint"]["x"])

    # Now assign when all pads are occupied -> lowest standby slot (0)
    existing_spots["AMR2"] = assigned["homeBay"]
    standby_amr = assign_charge_spot(layout, existing_spots, "AMR4")
    assert standby_amr["homeBay"]["kind"] == "standby"
    assert standby_amr["homeBay"]["slot"] == 0
    assert standby_amr["homeBay"]["padId"] is None

    # When slot 0 is taken, assign slot 1
    existing_spots["AMR4"] = standby_amr["homeBay"]
    standby_amr5 = assign_charge_spot(layout, existing_spots, "AMR5")
    assert standby_amr5["homeBay"]["kind"] == "standby"
    assert standby_amr5["homeBay"]["slot"] == 1


@pytest.mark.asyncio
async def test_release_charge_spot(tmp_path):
    """Removing an AMR releases its charge spot so a newly created AMR reclaims it."""
    amrs_file = tmp_path / "amrs.json"
    settings_file = tmp_path / "settings.json"
    amr_store = AmrStore(amrs_file, preset="ECOMMERCE", layout_roster=_preset_roster("ECOMMERCE"))
    settings = SettingsStore(settings_file, default_preset="ECOMMERCE")

    pm = ProcessManager(
        spawn=lambda *args, **kwargs: None,
        zenoh_available=_zenoh_up,
        amr_store=amr_store,
        settings=settings,
    )

    # Initial roster has 3 robots on 3 pads
    assert len(pm.amrs) == 3
    pad_id_amr2 = pm.amrs["AMR2"]["homeBay"]["padId"]
    assert pad_id_amr2 is not None

    # Remove AMR2
    await pm.remove_amr("AMR2")
    assert "AMR2" not in pm.amrs
    assert "AMR2" not in pm.charge_spots

    # Create AMR4 - it should claim the vacated pad (AMR2's old pad)
    new_entry = await pm.create_amr("AMR4")
    assert new_entry["homeBay"]["kind"] == "pad"
    assert new_entry["homeBay"]["padId"] == pad_id_amr2
    assert "AMR4" in pm.charge_spots


def test_reconcile_legacy_amrs(tmp_path):
    """Legacy state without homeBay entries gets automatically reconciled on load."""
    amrs_file = tmp_path / "amrs.json"
    settings_file = tmp_path / "settings.json"
    # Seed legacy amrs without homeBay
    import json
    legacy_data = {
        "amrs": [
            {"id": "AMR1", "x": 1.0, "y": 2.0},
            {"id": "AMR2", "x": 3.0, "y": 4.0},
        ]
    }
    amrs_file.write_text(json.dumps(legacy_data))

    amr_store = AmrStore(amrs_file, preset="ECOMMERCE", layout_roster=_preset_roster("ECOMMERCE"))
    settings = SettingsStore(settings_file, default_preset="ECOMMERCE")

    pm = ProcessManager(
        spawn=lambda *args, **kwargs: None,
        zenoh_available=_zenoh_up,
        amr_store=amr_store,
        settings=settings,
    )

    # Both AMRs should now have homeBay assigned
    assert "AMR1" in pm.charge_spots
    assert "AMR2" in pm.charge_spots
    assert pm.amrs["AMR1"]["homeBay"]["kind"] == "pad"
    assert pm.amrs["AMR2"]["homeBay"]["kind"] == "pad"
    # Unique pads
    assert pm.amrs["AMR1"]["homeBay"]["padId"] != pm.amrs["AMR2"]["homeBay"]["padId"]


def test_promote_standby_to_free_pads():
    """promote_standby_to_pads upgrades standby robots to unowned pads, lowest first."""
    layout = build_from_preset("DISTRIBUTION")  # 5 pads
    pads = layout.charging_pads()
    spots = {
        "AMR1": {"kind": "pad", "slot": 0, "padId": pads[0]["id"]},
        "AMR2": {"kind": "standby", "slot": 0, "padId": None},
        "AMR3": {"kind": "pad", "slot": 2, "padId": pads[2]["id"]},
        "AMR4": {"kind": "standby", "slot": 1, "padId": None},
    }
    promoted = promote_standby_to_pads(layout, spots)
    # AMR2 (lowest id) gets the lowest unowned pad BAY-2, AMR4 gets BAY-4
    assert promoted["AMR2"]["kind"] == "pad"
    assert promoted["AMR2"]["padId"] == pads[1]["id"]
    assert promoted["AMR4"]["kind"] == "pad"
    assert promoted["AMR4"]["padId"] == pads[3]["id"]
    assert "AMR1" not in promoted
    assert "AMR3" not in promoted
    # Each promoted robot spawns at the pad centre
    assert promoted["AMR2"]["x"] == pytest.approx(pads[1]["spawnPoint"]["x"])
    assert promoted["AMR2"]["y"] == pytest.approx(pads[1]["spawnPoint"]["y"])


def test_reconcile_promotes_standby_robot_to_free_pad(tmp_path):
    """ProcessManager reconcile promotes a persisted standby robot into a free pad."""
    import json
    amrs_file = tmp_path / "amrs.json"
    settings_file = tmp_path / "settings.json"
    # DISTRIBUTION has 5 pads; seed a stale ECOMMERCE-era roster where AMR3 is standby
    layout = build_from_preset("DISTRIBUTION")
    pads = layout.charging_pads()
    stale = {
        "amrs": [
            {"id": "AMR1", "x": 1.0, "y": 1.0, "homeBay": {"kind": "pad", "slot": 0, "padId": pads[0]["id"]}},
            {"id": "AMR2", "x": 2.0, "y": 2.0, "homeBay": {"kind": "pad", "slot": 1, "padId": pads[1]["id"]}},
            {"id": "AMR3", "x": 3.0, "y": 3.0, "homeBay": {"kind": "standby", "slot": 0, "padId": None}},
            {"id": "AMR4", "x": 4.0, "y": 4.0, "homeBay": {"kind": "pad", "slot": 3, "padId": pads[3]["id"]}},
        ]
    }
    amrs_file.write_text(json.dumps(stale))
    amr_store = AmrStore(amrs_file, preset="DISTRIBUTION", layout_roster=_preset_roster("DISTRIBUTION"))
    settings = SettingsStore(settings_file, default_preset="DISTRIBUTION")
    pm = ProcessManager(
        spawn=lambda *args, **kwargs: None,
        zenoh_available=_zenoh_up,
        amr_store=amr_store,
        settings=settings,
    )
    # AMR3 (stale standby) is promoted to the first unowned pad
    assert pm.amrs["AMR3"]["homeBay"]["kind"] == "pad"
    assert pm.amrs["AMR3"]["homeBay"]["padId"] == pads[2]["id"]
    assert pm.amrs["AMR3"]["homeBay"]["slot"] == 2


@pytest.mark.asyncio
async def test_remove_owner_promotes_standby(tmp_path):
    """Removing a pad owner promotes an existing standby robot into the freed pad."""
    import json
    amrs_file = tmp_path / "amrs.json"
    settings_file = tmp_path / "settings.json"
    # ECOMMERCE has 3 pads; 4 AMRs -> AMR4 on standby
    layout = build_from_preset("ECOMMERCE")
    pads = layout.charging_pads()
    data = {
        "amrs": [
            {"id": "AMR1", "x": 1.0, "y": 1.0, "homeBay": {"kind": "pad", "slot": 0, "padId": pads[0]["id"]}},
            {"id": "AMR2", "x": 2.0, "y": 2.0, "homeBay": {"kind": "pad", "slot": 1, "padId": pads[1]["id"]}},
            {"id": "AMR3", "x": 3.0, "y": 3.0, "homeBay": {"kind": "pad", "slot": 2, "padId": pads[2]["id"]}},
            {"id": "AMR4", "x": 4.0, "y": 4.0, "homeBay": {"kind": "standby", "slot": 0, "padId": None}},
        ]
    }
    amrs_file.write_text(json.dumps(data))
    amr_store = AmrStore(amrs_file, preset="ECOMMERCE", layout_roster=_preset_roster("ECOMMERCE"))
    settings = SettingsStore(settings_file, default_preset="ECOMMERCE")
    pm = ProcessManager(
        spawn=lambda *args, **kwargs: None,
        zenoh_available=_zenoh_up,
        amr_store=amr_store,
        settings=settings,
    )
    assert pm.amrs["AMR4"]["homeBay"]["kind"] == "standby"

    # Remove AMR2 -> AMR4 is promoted into AMR2's freed pad
    await pm.remove_amr("AMR2")
    assert pm.amrs["AMR4"]["homeBay"]["kind"] == "pad"
    assert pm.amrs["AMR4"]["homeBay"]["padId"] == pads[1]["id"]


def test_roster_token_roundtrip():
    """Test 8-field roster token serialization/deserialization and 3-field backwards compatibility."""
    # Pad owner token
    entry_pad = {
        "id": "AMR1",
        "x": 2.5,
        "y": 4.0,
        "homeBay": {
            "kind": "pad",
            "slot": 0,
            "padId": "BAY-1",
            "x": 2.5,
            "y": 4.0,
        },
    }
    token_pad = roster_token(entry_pad)
    assert token_pad == "AMR1:2.5:4:pad:0:BAY-1::"
    parsed_pad = parse_roster_token(token_pad)
    assert parsed_pad["id"] == "AMR1"
    assert parsed_pad["x"] == 2.5
    assert parsed_pad["y"] == 4.0
    assert parsed_pad["homeBay"]["kind"] == "pad"
    assert parsed_pad["homeBay"]["padId"] == "BAY-1"
    assert parsed_pad["homeBay"]["slot"] == 0

    # Standby robot token
    entry_sb = {
        "id": "AMR5",
        "x": 1.0,
        "y": 8.0,
        "homeBay": {
            "kind": "standby",
            "slot": 2,
            "padId": None,
            "x": 1.0,
            "y": 8.0,
        },
    }
    token_sb = roster_token(entry_sb)
    assert token_sb == "AMR5:1:8:standby:2::1:8"
    parsed_sb = parse_roster_token(token_sb)
    assert parsed_sb["id"] == "AMR5"
    assert parsed_sb["x"] == 1.0
    assert parsed_sb["y"] == 8.0
    assert parsed_sb["homeBay"]["kind"] == "standby"
    assert parsed_sb["homeBay"]["padId"] is None
    assert parsed_sb["homeBay"]["slot"] == 2

    # Legacy 3-field token backwards-compatibility
    token_legacy = "AMR9:12.3:14.5"
    parsed_legacy = parse_roster_token(token_legacy)
    assert parsed_legacy["id"] == "AMR9"
    assert parsed_legacy["x"] == pytest.approx(12.3)
    assert parsed_legacy["y"] == pytest.approx(14.5)
    assert parsed_legacy["homeBay"] is None
