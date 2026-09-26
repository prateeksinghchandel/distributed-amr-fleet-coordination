"""
server.py — aiohttp WebSocket/REST server for the RL training application.

Endpoints (all under ``/rl``)::

    GET  /rl/status        trainer state + metrics (poll-friendly)
    GET  /rl/scenarios     scenario presets + curriculum + obs/action dims
    GET  /rl/checkpoints   checkpoint listing
    GET  /rl/snapshot      most recent environment snapshot
    POST /rl/command       {command, args}  ->  control commands
    GET  /rl/ws            bidirectional WebSocket:
                             client -> {type:"cmd", command, args}
                             server  -> event stream (snapshot/status/metrics/
                                        episode/update/evaluation/step/log/error)

The trainer runs in its own thread. Events are pushed into each WebSocket's
asyncio queue from the trainer thread via ``call_soon_threadsafe``, so heavy
training never blocks the event loop and the UI never misses a snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Callable, Optional

from aiohttp import web, WSMsgType

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


@web.middleware
async def _cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_CORS_HEADERS)
    resp = await handler(request)
    for k, v in _CORS_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp

from rl.trainer import RLTrainer
from rl.env import LIDAR_RAYS, LIDAR_RANGE, MAX_PEERS, OBS_DIM, ACTION_DIM
from rl.scenarios import CURRICULUM, SCENARIO_PRESETS

ALLOWED_COMMANDS = {
    "start", "pause", "resume", "step", "step_episode",
    "reset_episode", "reset_training", "save_checkpoint", "load_checkpoint",
    "delete_checkpoint", "set_speed", "change_scenario", "evaluate", "stop",
    # self-play league
    "league_start", "league_stop", "league_promote", "league_vs_pool",
}

LEAGUE_COMMANDS = {
    "league_start": "start",
    "league_stop": "stop",
    "league_promote": "promote",
    "league_vs_pool": "champion_vs_pool",
}

SPEC_INFO = {
    "obs_dim": OBS_DIM,
    "action_dim": ACTION_DIM,
    "lidar_rays": LIDAR_RAYS,
    "lidar_range": LIDAR_RANGE,
    "max_peers": MAX_PEERS,
}


def _e(event) -> str:  # normalize exceptions into a str
    return getattr(event, "message", None) or str(event)


class TrainingApp:
    """Cors-enabled aiohttp app holding one RLTrainer + websocket clients."""

    def __init__(self, trainer: RLTrainer, league: Optional["LeagueTrainer"] = None):
        self.trainer = trainer
        self.league = league
        self._ws_bridges: dict[web.WebSocketResponse, Callable] = {}

    # ---------------------------------------------------------------- REST

    async def get_status(self, request: web.Request) -> web.Response:
        status = self.trainer.status()
        metrics = self.trainer.metrics_brief()
        for k, v in metrics.items():
            if k != "type":
                status[k] = v
        if self.trainer.last_evaluation:
            status["evaluation"] = self.trainer.last_evaluation
        if self.league is not None:
            status["league"] = self.league.status()
        return web.json_response(status)

    async def get_scenarios(self, request: web.Request) -> web.Response:
        return web.json_response({
            "presets": SCENARIO_PRESETS,
            "curriculum": {str(k): v for k, v in CURRICULUM.items()},
            "spec": SPEC_INFO,
        })

    async def get_checkpoints(self, request: web.Request) -> web.Response:
        return web.json_response({"checkpoints": self.trainer.list_checkpoints(),
                                  "dir": str(self.trainer.checkpoint_dir)})

    async def get_snapshot(self, request: web.Request) -> web.Response:
        snap = self.trainer.last_snapshot
        if snap is None:
            return web.json_response({"error": "no snapshot yet"}, status=503)
        return web.json_response(snap)

    async def post_command(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception as exc:
            return web.json_response({"ok": False, "error": _e(exc)}, status=400)
        return await self._dispatch(body)

    async def _dispatch(self, body: dict) -> web.Response:
        command = body.get("command")
        args = body.get("args") or {}
        base = {"type": "ack", "command": command}
        if not isinstance(command, str) or command not in ALLOWED_COMMANDS:
            return web.json_response({**base, "ok": False,
                                      "error": f"unknown command '{command}'"},
                                     status=400)
        if command in LEAGUE_COMMANDS:
            if self.league is None:
                return web.json_response({**base, "ok": False,
                                          "error": "league not enabled"},
                                         status=400)
            handler = getattr(self.league, LEAGUE_COMMANDS[command], None)
        else:
            handler = getattr(self.trainer, command, None)
        if handler is None:
            return web.json_response({**base, "ok": False,
                                      "error": "no handler"},
                                     status=400)
        try:
            result = handler(**args)
            ok = result.get("ok", True) if isinstance(result, dict) else True
            resp = {**base, "ok": bool(ok)}
            if isinstance(result, dict):
                resp["result"] = result
                if result.get("error"):
                    resp["error"] = result["error"]
            return web.json_response(resp)
        except Exception as exc:
            return web.json_response({**base, "ok": False,
                                      "error": _e(exc)}, status=400)

    # ------------------------------------------------------------- Websocket

    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        loop = asyncio.get_running_loop()

        def bridge(kind: str, payload: dict) -> None:
            loop.call_soon_threadsafe(_put_safely, queue, (kind, payload))

        self._ws_bridges[ws] = bridge
        self.trainer.attach_listener(bridge)

        try:
            # Initial burst: current status + metrics + snapshot + league.
            for item in (self.trainer.status(), self.trainer.metrics_brief(),
                         self.trainer.last_snapshot):
                if item is not None:
                    await ws.send_str(json.dumps(item, default=str))
            if self.league is not None:
                await ws.send_str(json.dumps(
                    self.league.status() | {"type": "league"}, default=str))
        except Exception:
            pass

        async def sender() -> None:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if ws.closed:
                        return
                    continue
                if ws.closed:
                    return
                try:
                    await ws.send_str(json.dumps(payload, default=str))
                except Exception:
                    return

        async def receiver() -> None:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        body = json.loads(msg.data)
                    except Exception:
                        await _try_send(ws, {"type": "error",
                                             "message": "invalid JSON"})
                        continue
                    if body.get("type") == "cmd":
                        resp = await self._dispatch(body)
                        await _try_send(ws, json.loads(resp.body.decode()))
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    return

        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())
        try:
            await asyncio.gather(sender_task, receiver_task)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            sender_task.cancel()
            receiver_task.cancel()
            self.trainer.detach_listener(self._ws_bridges.pop(ws, None))
        return ws


def _put_safely(queue: asyncio.Queue, item) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        # Drop the oldest event to keep the UI stream live under load.
        try:
            queue.get_nowait()
            queue.put_nowait(item)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass


async def _try_send(ws: web.WebSocketResponse, payload: dict) -> None:
    if ws.closed:
        return
    try:
        await ws.send_str(json.dumps(payload))
    except Exception:
        pass


# --------------------------------------------------------------------- app


def build_app(trainer: Optional[RLTrainer] = None, league: Optional["LeagueTrainer"] = None,
              **trainer_kwargs) -> web.Application:
    app = TrainingApp(trainer or RLTrainer(**trainer_kwargs), league=league)
    application = web.Application(middlewares=[_cors_middleware])
    application.router.add_get("/rl/status", app.get_status)
    application.router.add_get("/rl/scenarios", app.get_scenarios)
    application.router.add_get("/rl/checkpoints", app.get_checkpoints)
    application.router.add_get("/rl/snapshot", app.get_snapshot)
    application.router.add_post("/rl/command", app.post_command)
    application.router.add_get("/rl/ws", app.ws_handler)
    application.router.add_route("*", "/rl", lambda r: web.json_response(
        {"service": "rl-training",
         "hint": "GET /rl/status, /rl/scenarios, /rl/checkpoints, /rl/ws"}))
    application["trainer_app"] = app
    return application


RL_HOST = "127.0.0.1"
RL_PORT = 8370


def main() -> None:
    parser = argparse.ArgumentParser(description="RL training server")
    parser.add_argument("--host", default=RL_HOST)
    parser.add_argument("--port", type=int, default=RL_PORT)
    parser.add_argument("--scenario", default="obstacle_avoidance")
    parser.add_argument("--level", type=int, default=None)
    parser.add_argument("--n-envs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--safety", default="guard",
                        choices=("off", "guard", "strict"))
    parser.add_argument("--device", default=None)
    parser.add_argument("--rollout-steps", type=int, default=512)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--save-every", type=int, default=5000,
                        help="autosave the policy to autosave.pt every N sim "
                             "steps while training (0 disables)")
    parser.add_argument("--league", action="store_true",
                        help="enable self-play: champion vs a pool of frozen "
                             "former champions")
    parser.add_argument("--pool-size", type=int, default=4)
    parser.add_argument("--pool-every", type=int, default=3000,
                        help="promote the champion into the pool every N sim steps")
    parser.add_argument("--vs-pool-episodes", type=int, default=3)
    args = parser.parse_args()

    from rl.scenarios import scenario_config, curriculum_config
    cfg = curriculum_config(args.level) if args.level else \
        scenario_config(args.scenario)

    kwargs = dict(n_envs=args.n_envs, seed=args.seed, safety=args.safety,
                  rollout_steps=args.rollout_steps, device=args.device,
                  autosave_every=args.save_every)
    if args.checkpoint_dir:
        kwargs["checkpoint_dir"] = args.checkpoint_dir

    trainer = RLTrainer(cfg, **kwargs)
    app = build_app(trainer)

    if args.league:
        from rl.league import LeagueTrainer
        league = LeagueTrainer(trainer, pool_size=args.pool_size,
                               checkpoint_dir=trainer.checkpoint_dir)
        app = build_app(trainer, league=league)

    async def _serve():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
        trainer._log(f"RL training server on http://{args.host}:{args.port}"
                     f" (scenario={cfg['name']})")
        await asyncio.Event().wait()

    try:
        asyncio.run(_serve())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        trainer.shutdown()


if __name__ == "__main__":
    main()