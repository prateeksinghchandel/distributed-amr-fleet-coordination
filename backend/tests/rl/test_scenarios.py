"""test_scenarios.py — scenario presets, curriculum and baselines."""

from __future__ import annotations

import pytest

from rl.scenarios import (CURRICULUM, SCENARIO_PRESETS, _clamp_scenario,
                          build_scene, evaluate_algorithmic, resolve_scenario,
                          scenario_config, curriculum_config, SceneSeedGen)
from rl.env import DT


def test_presets_are_valid() -> None:
    assert len(SCENARIO_PRESETS) >= 8
    for name in ("simple", "obstacle_avoidance", "two_robot", "intersection",
                 "narrow_corridor", "chokepoint", "dense_traffic", "random"):
        cfg = scenario_config(name)
        assert cfg["name"] == name
        assert 0 < cfg["n_robots"] <= 8
        assert cfg["n_rl"] >= 1
        assert cfg["max_steps"] >= 25
        assert cfg["width"] > cfg["height"] > 0
        assert cfg["difficulty"] >= 1


def test_no_curriculum_out_of_bounds() -> None:
    # levels are clamped, never crash
    assert curriculum_config(9)["difficulty"] == 8
    assert curriculum_config(0)["difficulty"] == 1


def test_curriculum_is_monotonic() -> None:
    difficulties = [CURRICULUM[lv]["difficulty"] for lv in sorted(CURRICULUM)]
    assert difficulties == sorted(difficulties)
    assert len(CURRICULUM) == 8
    assert CURRICULUM[8]["n_rl"] == 1


def test_build_scene_is_deterministic() -> None:
    s1 = build_scene(scenario_config("obstacle_avoidance"), seed=17)
    s2 = build_scene(scenario_config("obstacle_avoidance"), seed=17)
    assert [r.x for r in s1.robots] == [r.x for r in s2.robots]
    assert [r.y for r in s1.robots] == [r.y for r in s2.robots]
    assert [tuple(t) for t in s1.robots[0].path] == \
           [tuple(t) for t in s2.robots[0].path]


def test_evaluate_algorithmic_baseline_runs() -> None:
    cfg = {**scenario_config("simple"), "max_steps": 150}
    res = evaluate_algorithmic(cfg, seed=3, max_steps=150)
    assert isinstance(res, dict)
    assert "success" in res
    assert res["time"] > 0
    assert res["steps"] <= 150
    assert isinstance(res["distance"], float)


def test_evaluate_algorithmic_multi_seed() -> None:
    cfg = {**scenario_config("simple"), "max_steps": 200}
    out = [evaluate_algorithmic(cfg, seed=s) for s in (1, 2, 3)]
    successes = sum(1 for r in out if r["success"])
    assert 0 <= successes <= 3


def test_clamp_scenario_defaults() -> None:
    cfg = _clamp_scenario({"name": "x", "max_steps": 500})
    assert cfg["n_robots"] >= 1
    assert cfg["n_rl"] >= 1
    assert cfg["obstacle_mode"] in ("none", "random", "quadrant", "corridor")
    assert cfg["max_steps"] == 500


def test_resolve_scenario_level_wins_over_named_preset() -> None:
    cfg, note = resolve_scenario("simple", 4, None)
    assert cfg["name"] == CURRICULUM[4]["name"]
    assert cfg["difficulty"] == 4
    assert "level wins" in note


def test_resolve_scenario_scenario_only() -> None:
    cfg, note = resolve_scenario("dense_traffic", None, None)
    assert cfg["name"] == "dense_traffic"
    assert "level wins" not in note


def test_resolve_scenario_default_when_both_missing() -> None:
    cfg, note = resolve_scenario(None, None, None)
    assert cfg["name"] == "obstacle_avoidance"


def test_resolve_scenario_overrides_applied() -> None:
    cfg, _note = resolve_scenario("simple", None, {"max_steps": 77})
    assert cfg["max_steps"] == 77


def test_scene_seed_gen_campaign_is_monotonic() -> None:
    gen = SceneSeedGen(42)
    a = [gen.next() for _ in range(50)]
    b = [gen.next() for _ in range(50)]
    assert len(set(a + b)) == 100          # no repeated draws in a campaign
    assert gen.draws == 100


def test_scene_seed_gen_is_reproducible() -> None:
    assert SceneSeedGen(7).take(10) == SceneSeedGen(7).take(10)
    assert SceneSeedGen(7).take(10) != SceneSeedGen(8).take(10)