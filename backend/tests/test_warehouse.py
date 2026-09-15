"""
test_warehouse.py — Unit tests for warehouse layout generation.
Mirrors test logic from warehouse-builder.test.mjs.
"""

import pytest
from server.warehouse import build_warehouse, build_from_preset, WAREHOUSE_PRESETS, Rect


class TestPresets:
    def test_all_presets_available(self):
        for key in ["ECOMMERCE", "DISTRIBUTION", "MICRO_FULFILLMENT"]:
            assert key in WAREHOUSE_PRESETS

    def test_ecommerce_preset(self):
        layout = build_from_preset("ECOMMERCE")
        assert layout.width == 30
        assert layout.height == 20
        assert len(layout.robots) == 3
        assert len(layout.delivery_zone.stations) == 3

    def test_distribution_preset(self):
        layout = build_from_preset("DISTRIBUTION")
        assert layout.width == 40
        assert layout.height == 25
        assert len(layout.robots) == 5

    def test_micro_fulfillment_preset(self):
        layout = build_from_preset("MICRO_FULFILLMENT")
        assert layout.width == 20
        assert layout.height == 14
        assert len(layout.robots) == 2

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError):
            build_from_preset("UNKNOWN")


class TestDeliveryZone:
    def test_delivery_zone_on_left(self):
        layout = build_warehouse()
        dz = layout.delivery_zone
        assert dz.rect.x == 0
        assert dz.rect.width > 0
        assert dz.rect.height == layout.height

    def test_delivery_stations_count(self):
        layout = build_warehouse({"delivery_docks": 3})
        assert len(layout.delivery_zone.stations) == 3

    def test_delivery_stations_have_dropoff_points(self):
        layout = build_warehouse()
        for st in layout.delivery_zone.stations:
            assert st.dropoff_point.x > 0
            assert 0 < st.dropoff_point.y < layout.height

    def test_dropoff_point_within_delivery_zone(self):
        layout = build_warehouse()
        dz = layout.delivery_zone
        for st in layout.delivery_zone.stations:
            assert dz.rect.x <= st.dropoff_point.x <= dz.rect.x2
            assert dz.rect.y <= st.dropoff_point.y <= dz.rect.y2


class TestChargingZone:
    def test_charging_zone_on_right(self):
        layout = build_warehouse()
        cz = layout.charging_zone
        assert cz.rect.x + cz.rect.width == pytest.approx(layout.width, abs=0.01)

    def test_charging_pads_count(self):
        layout = build_warehouse({"charging_pads": 3})
        assert len(layout.charging_zone.pads) == 3

    def test_charging_pads_have_spawn_points(self):
        layout = build_warehouse()
        for pad in layout.charging_zone.pads:
            assert 0 < pad.spawn_point.x < layout.width
            assert 0 < pad.spawn_point.y < layout.height

    def test_assigned_robot_ids(self):
        layout = build_warehouse({"robot_count": 2, "charging_pads": 3})
        assigned = [p.assigned_robot_id for p in layout.charging_zone.pads if p.assigned_robot_id]
        assert len(assigned) == 2
        assert "AMR1" in assigned
        assert "AMR2" in assigned


class TestShelves:
    def test_shelves_in_center(self):
        layout = build_warehouse()
        dz_right = layout.delivery_zone.rect.x2
        cz_left = layout.charging_zone.rect.x
        for shelf in layout.shelves:
            assert shelf.rect.x >= dz_right - 0.1, f"Shelf {shelf.id} overlaps delivery zone"
            assert shelf.rect.x2 <= cz_left + 0.1, f"Shelf {shelf.id} overlaps charging zone"

    def test_shelves_have_pick_points(self):
        layout = build_warehouse()
        for shelf in layout.shelves:
            assert len(shelf.pick_points) == 4

    def test_shelves_not_empty(self):
        layout = build_warehouse()
        assert len(layout.shelves) > 0

    def test_custom_shelf_count(self):
        layout = build_warehouse({"shelf_rows": 2, "shelf_cols": 3})
        # may be <= 6 due to boundary clipping
        assert len(layout.shelves) <= 6
        assert len(layout.shelves) >= 1

    def test_obstacles_match_shelves(self):
        layout = build_warehouse()
        obs = layout.obstacles()
        assert len(obs) == len(layout.shelves)
        for o in obs:
            assert "id" in o and "x" in o and "y" in o


class TestRobotSpawns:
    def test_robots_spawned_in_charging_zone(self):
        layout = build_warehouse()
        cz = layout.charging_zone.rect
        for robot in layout.robots:
            assert cz.x <= robot.x <= cz.x2 + 0.1, f"{robot.id} not in charging zone x"
            assert cz.y <= robot.y <= cz.y2 + 0.1, f"{robot.id} not in charging zone y"

    def test_robot_ids(self):
        layout = build_warehouse({"robot_count": 3})
        ids = {r.id for r in layout.robots}
        assert ids == {"AMR1", "AMR2", "AMR3"}

    def test_roster_dict(self):
        layout = build_warehouse()
        roster = layout.roster()
        assert all("id" in r and "x" in r and "y" in r for r in roster)


class TestTaskGeneration:
    def test_random_pick_point_within_bounds(self):
        layout = build_warehouse()
        for _ in range(20):
            pt = layout.random_pick_point()
            assert 0 <= pt.x <= layout.width
            assert 0 <= pt.y <= layout.height

    def test_random_delivery_point_in_delivery_zone(self):
        layout = build_warehouse()
        dz = layout.delivery_zone.rect
        for _ in range(20):
            pt = layout.random_delivery_point()
            assert dz.x <= pt.x <= dz.x2 + 0.1
            assert dz.y <= pt.y <= dz.y2 + 0.1

    def test_free_point_not_inside_obstacle(self):
        layout = build_warehouse()
        for _ in range(10):
            pt = layout.free_point()
            for shelf in layout.shelves:
                assert not shelf.rect.inflated_contains(pt.x, pt.y, 0.1), \
                    f"free_point ({pt.x:.2f},{pt.y:.2f}) landed inside shelf {shelf.id}"


class TestDimensionClamping:
    def test_width_minimum(self):
        layout = build_warehouse({"width": 1})
        assert layout.width >= 12

    def test_width_maximum(self):
        layout = build_warehouse({"width": 999})
        assert layout.width <= 100

    def test_height_minimum(self):
        layout = build_warehouse({"height": 1})
        assert layout.height >= 10

    def test_robot_count_capped_by_charging_pads(self):
        layout = build_warehouse({"robot_count": 10, "charging_pads": 3})
        assert len(layout.robots) <= 3
