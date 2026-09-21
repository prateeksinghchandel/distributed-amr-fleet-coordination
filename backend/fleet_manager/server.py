"""aiohttp server wiring for the Fleet Manager."""

from __future__ import annotations

import argparse
import asyncio
import signal

from aiohttp import web

from . import config
from .process_manager import ProcessManager
from .routes import routes

_PROCESS_KEYS = ("zenohd", "bridge", "coordinator")


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(request))
    resp = await handler(request)
    _apply_cors(resp, request)
    return resp


def _cors_headers(request) -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "3600",
    }


def _apply_cors(resp, request) -> None:
    for key, value in _cors_headers(request).items():
        resp.headers[key] = value


def build_app(pm: ProcessManager) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["pm"] = pm
    app.add_routes(routes)
    app.add_routes([
        web.get("/", api_root),
    ])
    return app


async def api_root(request: web.Request) -> web.Response:
    return web.json_response({
        "service": "fleet-manager",
        "hint": "use /api/status, /api/info, /api/logs/{name}, and the control endpoints",
    })


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Local Fleet Manager for the AMR fleet stack")
    p.add_argument("--host", default=config.FM_HOST)
    p.add_argument("--port", type=int, default=config.FM_PORT)
    return p.parse_args()


async def run(pm: ProcessManager, host: str, port: int) -> None:
    app = build_app(pm)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    pm.log(f"Fleet Manager listening on http://{host}:{port}/api/status")

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def _signal_handler(signum, _frame):
        pm.log("Shutdown signal received — stopping managed processes")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler, sig, None)
        except NotImplementedError:
            # Windows does not support asyncio.add_signal_handler()
            signal.signal(sig, _signal_handler)

    try:
        await stop.wait()
    finally:
        await pm.stop_all()
        if pm._monitor_task:
            pm._monitor_task.cancel()
        await runner.cleanup()


def main() -> None:
    args = parse_args()
    pm = ProcessManager()
    try:
        asyncio.run(run(pm, args.host, args.port))
    except KeyboardInterrupt:
        pass