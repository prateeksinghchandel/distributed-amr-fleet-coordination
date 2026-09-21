"""HTTP API routes for the Fleet Manager (aiohttp)."""

from __future__ import annotations

import json

from aiohttp import web

from .process_manager import DependencyError, ProcessError

routes = web.RouteTableDef()


def _err(status: int, message: str, extra: dict | None = None) -> web.Response:
    body = {"ok": False, "error": message}
    if extra:
        body.update(extra)
    return web.json_response(body, status=status)


def _ok(data: dict) -> web.Response:
    data = dict(data)
    data.setdefault("ok", True)
    return web.json_response(data)


@routes.get("/api/info")
async def api_info(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    return _ok(pm.info())


@routes.get("/api/status")
async def api_status(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    return _ok(pm.status())


@routes.get("/api/logs/{name}")
async def api_logs(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    name = request.match_info["name"]
    try:
        limit = int(request.query.get("limit", 300))
    except ValueError:
        limit = 300
    try:
        lines = pm.logs(name, limit=limit)
    except KeyError:
        return _err(404, f"unknown process '{name}'")
    return _ok({"name": name, "lines": lines, "count": len(lines)})


@routes.post("/api/zenoh/start")
async def api_zenoh_start(request: web.Request) -> web.Response:
    return await _run_command(request, "zenohd", "start")


@routes.post("/api/zenoh/stop")
async def api_zenoh_stop(request: web.Request) -> web.Response:
    return await _run_command(request, "zenohd", "stop")


@routes.post("/api/zenoh/restart")
async def api_zenoh_restart(request: web.Request) -> web.Response:
    return await _run_command(request, "zenohd", "restart")


@routes.post("/api/bridge/start")
async def api_bridge_start(request: web.Request) -> web.Response:
    return await _run_command(request, "bridge", "start")


@routes.post("/api/bridge/stop")
async def api_bridge_stop(request: web.Request) -> web.Response:
    return await _run_command(request, "bridge", "stop")


@routes.post("/api/bridge/restart")
async def api_bridge_restart(request: web.Request) -> web.Response:
    return await _run_command(request, "bridge", "restart")


@routes.post("/api/coordinator/start")
async def api_coordinator_start(request: web.Request) -> web.Response:
    return await _run_command(request, "coordinator", "start")


@routes.post("/api/coordinator/stop")
async def api_coordinator_stop(request: web.Request) -> web.Response:
    return await _run_command(request, "coordinator", "stop")


@routes.post("/api/coordinator/restart")
async def api_coordinator_restart(request: web.Request) -> web.Response:
    return await _run_command(request, "coordinator", "restart")


@routes.post("/api/coordinator/configure")
async def api_coordinator_configure(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    body = await request.json()
    preset = (body.get("preset") or "").upper().strip()
    tasks = body.get("tasks")
    auction_mode = (body.get("auctionMode") or "").upper().strip() or None
    if preset and preset not in ("MICRO_FULFILLMENT", "ECOMMERCE", "DISTRIBUTION"):
        return _err(400, f"unsupported preset '{preset}'")
    if tasks is not None:
        try:
            tasks = int(tasks)
        except ValueError:
            return _err(400, "tasks must be an integer")
    if auction_mode and auction_mode not in ("SERVER_AUCTION", "P2P_AUCTION"):
        return _err(400, f"unsupported auction mode '{auction_mode}'")
    pm.set_configuration(preset or None, tasks, auction_mode)
    return _ok(pm.status())


@routes.post("/api/amrs")
async def api_amr_create(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    body = await request.json()
    amr_id = str(body.get("id") or "").strip()
    if not amr_id:
        return _err(400, "AMR id is required")
    x = None
    y = None
    if "x" in body and "y" in body and body["x"] is not None and body["y"] is not None:
        try:
            x = float(body["x"])
            y = float(body["y"])
        except (TypeError, ValueError):
            return _err(400, "x/y must be numbers")
    try:
        entry = await pm.create_amr(amr_id, x, y)
    except ProcessError as exc:
        return _err(409, str(exc))
    return _ok({"entry": entry, "status": pm.status()})


@routes.delete("/api/amrs/{amr_id}")
async def api_amr_remove(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    amr_id = request.match_info["amr_id"]
    try:
        result = await pm.remove_amr(amr_id)
    except ProcessError as exc:
        return _err(400 if "still running" in str(exc) else 409, str(exc))
    return _ok({**result, "status": pm.status()})


@routes.post("/api/amrs/{amr_id}/start")
async def api_amr_start(request: web.Request) -> web.Response:
    return await _run_command(request, f"amr:{request.match_info['amr_id']}", "start")


@routes.post("/api/amrs/{amr_id}/stop")
async def api_amr_stop(request: web.Request) -> web.Response:
    return await _run_command(request, f"amr:{request.match_info['amr_id']}", "stop")


@routes.post("/api/amrs/{amr_id}/restart")
async def api_amr_restart(request: web.Request) -> web.Response:
    return await _run_command(request, f"amr:{request.match_info['amr_id']}", "restart")


@routes.post("/api/start-all")
async def api_start_all(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    return _ok({"results": await pm.start_all(), "status": pm.status()})


@routes.post("/api/stop-all")
async def api_stop_all(request: web.Request) -> web.Response:
    pm = request.app["pm"]
    return _ok({"results": await pm.stop_all(), "status": pm.status()})


async def _run_command(request: web.Request, name: str, action: str) -> web.Response:
    pm = request.app["pm"]
    try:
        if action == "start":
            result = await pm.start(name)
        elif action == "stop":
            result = await pm.stop(name)
        else:
            result = await pm.restart(name)
    except DependencyError as exc:
        return _err(409, str(exc), {"dependency": True})
    except ProcessError as exc:
        return _err(409, str(exc))
    except Exception as exc:
        return _err(500, f"unexpected error: {exc}")
    return _ok({**result, "status": pm.status()})