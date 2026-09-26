"""
test_server.py — HTTP/REST + WebSocket contract of the RL training server.

Uses aiohttp's in-process TestClient against a real ``build_app`` instance so
the routes, CORS headers, command dispatch and the WS event stream are all
exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import pytest_asyncio
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from rl.scenarios import scenario_config
from rl.server import ALLOWED_COMMANDS, SPEC_INFO, build_app
from rl.trainer import RLTrainer


@pytest_asyncio.fixture(scope="function")
async def client():
    trainer = RLTrainer(small_cfg(), n_envs=2, rollout_steps=32, minibatch=8,
                        update_epochs=2, seed=1, speed=1.0,
                        checkpoint_dir="/tmp/rl-server-test-checkpoints")
    app = build_app(trainer)
    tc = TestClient(TestServer(app))
    await tc.start_server()
    yield tc, trainer
    await tc.close()
    trainer.shutdown()


def small_cfg():
    cfg = scenario_config("simple")
    cfg.update({"max_steps": 120})
    return cfg


@pytest.mark.asyncio
async def test_status_endpoint(client) -> None:
    tc, trainer = client
    resp = await tc.get("/rl/status")
    assert resp.status == 200
    body = await resp.json()
    assert body["type"] == "status"
    assert body["state"] == "IDLE"
    assert body["total_steps"] == trainer.total_steps
    assert body["agent"]["device"] in ("cpu", "cuda")


@pytest.mark.asyncio
async def test_scenarios_endpoint(client) -> None:
    tc, _ = client
    resp = await tc.get("/rl/scenarios")
    body = await resp.json()
    assert len(body["presets"]) >= 8
    assert len(body["curriculum"]) == 8
    assert body["spec"] == SPEC_INFO


@pytest.mark.asyncio
async def test_checkpoints_endpoint(client) -> None:
    tc, trainer = client
    trainer.save_checkpoint("srv_test")
    resp = await tc.get("/rl/checkpoints")
    body = await resp.json()
    assert isinstance(body["checkpoints"], list)
    assert any(c["name"] == "srv_test.pt" for c in body["checkpoints"])
    trainer.delete_checkpoint("srv_test.pt")


@pytest.mark.asyncio
async def test_snapshot_endpoint(client) -> None:
    tc, _ = client
    resp = await tc.get("/rl/snapshot")
    assert resp.status == 200
    body = await resp.json()
    assert "robots" in body
    assert "obstacles" in body


@pytest.mark.asyncio
async def test_command_start_and_status_change(client) -> None:
    tc, trainer = client
    resp = await tc.post("/rl/command", json={"command": "start"})
    body = await resp.json()
    assert body["ok"] is True
    report = report_until(lambda: trainer.total_steps > 0)
    assert report
    trainer.stop()


@pytest.mark.asyncio
async def test_unknown_command_rejected(client) -> None:
    tc, _ = client
    resp = await tc.post("/rl/command", json={"command": "frobnicate"})
    assert resp.status == 400
    body = await resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_malformed_json_rejected(client) -> None:
    tc, _ = client
    resp = await tc.post("/rl/command", data=b"{not json}{")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_command_error_reported(client) -> None:
    tc, _ = client
    resp = await tc.post("/rl/command", json={"command": "load_checkpoint",
                                              "args": {"name": "missing.pt"}})
    body = await resp.json()
    assert body["ok"] is False
    assert "error" in body


@pytest.mark.asyncio
async def test_cors_headers_present(client) -> None:
    tc, _ = client
    resp = await tc.get("/rl/status")
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    resp = await tc.options("/rl/status")
    assert resp.status == 204


@pytest.mark.asyncio
async def test_ws_burst_and_command_ack(client) -> None:
    tc, trainer = client
    ws = await tc.ws_connect("/rl/ws")
    kinds: set[str] = set()
    for _ in range(20):
        if {"status", "metrics", "snapshot"} <= kinds:
            break
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        kinds.add(msg.get("type"))
    assert {"status", "metrics", "snapshot"} <= kinds, \
        "initial burst must include status, metrics and snapshot"

    await ws.send_json({"type": "cmd", "command": "pause"})
    ack = None
    for _ in range(20):
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if msg.get("type") == "ack":
            ack = msg
            break
    assert ack is not None
    assert ack["command"] == "pause"
    assert ack["ok"] is True

    await ws.send_json({"type": "cmd", "command": "bogus"})
    bad = None
    for _ in range(20):
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if msg.get("type") == "ack":
            bad = msg
            break
    assert bad is not None and bad["ok"] is False

    await ws.close()
    trainer.stop()


@pytest.mark.asyncio
async def test_ws_streams_status_events_during_training(client) -> None:
    tc, trainer = client
    ws = await tc.ws_connect("/rl/ws")
    for _ in range(3):
        await asyncio.wait_for(ws.receive_json(), timeout=5)
    await ws.send_json({"type": "cmd", "command": "start"})
    statuses = 0
    deadline = time.time() + 6
    while time.time() < deadline and statuses == 0:
        try:
            msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        if msg.get("type") == "status" and msg.get("state") == "TRAINING":
            statuses += 1
    assert statuses > 0
    trainer.stop()
    await ws.close()


def report_until(predicate, timeout=6.0, interval=0.03) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()