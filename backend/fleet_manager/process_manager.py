"""Process lifecycle management for the distributed AMR fleet stack.

Owns spawning, terminating and monitoring of:

    zenohd (router)          -> zenoh-bridge-remote-api (WebSocket bridge)
    -> server.server_node    -> robot.robot_node instances (one per AMR)

Reuses the same binaries, ports and command-line arguments as the shell-based
runbook (dashboard/scripts/zenohd.sh, Running.md).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from . import config

MAX_LOG_LINES = 2000

# Turn-on of the STARTING -> RUNNING grace window for processes that have no
# service port to wait on (coordinator, AMRs). Long enough to catch the common
# "zenoh unreachable -> sys.exit(1)" failure at boot.
BOOTSTRAP_S = 2.0


def _log(msg: str) -> None:
    print(f"[fleet-manager] {msg}", flush=True)


class DependencyError(RuntimeError):
    """A process refused to start because a prerequisite is not available."""


class ProcessError(RuntimeError):
    """A process failed to start, stop or restart."""


# ---------------------------------------------------------------------------
# Spawn / availability primitives (injectable so tests can fake them)
# ---------------------------------------------------------------------------

def resolve_bin(name: str) -> Optional[str]:
    """Mirror dashboard/scripts/zenohd.sh binary resolution."""
    env_key = {"zenohd": "ZENOH_ZENOHD", "zenoh-bridge-remote-api": "ZENOH_BRIDGE"}.get(name)
    if env_key:
        cand = os.environ.get(env_key)
        if cand and os.access(cand, os.X_OK):
            return cand
    local = config.TOOLS_DIR / name
    if os.access(local, os.X_OK):
        return str(local)
    return shutil.which(name)


def resolve_zenoh_bins() -> dict:
    return {
        "zenohd": resolve_bin("zenohd"),
        "bridge": resolve_bin("zenoh-bridge-remote-api"),
    }


async def default_spawn(args, *, cwd, env=None, log=None):
    """Fork a real OS process with stdout/stderr piped for log capture."""
    if log:
        log(f"spawn: {' '.join(args)} (cwd={cwd})")
    return await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def default_zenoh_available(port: int) -> bool:
    """True when something is listening on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# ManagedProcess
# ---------------------------------------------------------------------------

@dataclass
class ManagedProcess:
    name: str
    cmd: list
    cwd: str
    env: Optional[dict] = None
    detail: dict = field(default_factory=dict)
    proc: Optional[object] = None
    pid: Optional[int] = None
    state: str = "STOPPED"          # STARTING | RUNNING | STOPPING | STOPPED | CRASHED
    started_at: Optional[float] = None
    last_error: Optional[str] = None
    expected_stop: bool = False
    log_lines: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    readers: list = field(default_factory=list)

    def info(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "pid": self.pid,
            "startedAt": self.started_at,
            "running": self.state in ("STARTING", "RUNNING"),
            "detail": dict(self.detail),
            "lastError": self.last_error,
        }


# ---------------------------------------------------------------------------
# ProcessManager
# ---------------------------------------------------------------------------

class ProcessManager:
    def __init__(
        self,
        spawn: Optional[Callable] = None,
        zenoh_available: Optional[Callable] = None,
        log: Optional[Callable] = None,
        monitor_interval: float = 0.2,
        bootstrap_s: float = BOOTSTRAP_S,
        check_timeout_s: float = config.ZENOH_CHECK_TIMEOUT_S,
        graceful_stop_s: float = config.GRACEFUL_STOP_TIMEOUT_S,
        amr_store=None,
        settings=None,
    ):
        self.spawn = spawn or default_spawn
        self.zenoh_available = zenoh_available or default_zenoh_available
        self.log = log or _log
        self.monitor_interval = monitor_interval
        self.bootstrap_s = bootstrap_s
        self.check_timeout_s = check_timeout_s
        self.graceful_stop_s = graceful_stop_s

        if amr_store is None or settings is None:
            from .amr_store import AmrStore, SettingsStore
            self.amr_store = amr_store or AmrStore(
                config.AMR_STATE_FILE,
                preset=config.COORDINATOR_PRESET,
                layout_roster=_preset_roster(config.COORDINATOR_PRESET),
                log=self.log,
            )
            self.settings = settings or SettingsStore(
                config.SETTINGS_STATE_FILE,
                default_preset=config.COORDINATOR_PRESET,
                default_tasks=config.COORDINATOR_TASKS,
            )
        else:
            self.amr_store = amr_store
            self.settings = settings

        self.processes: dict[str, ManagedProcess] = {}
        self.amrs: dict[str, dict] = {}
        self._monitor_task: Optional[asyncio.Task] = None

        self.build_registry()

    # ------------------------------------------------------------------
    # Process registry
    # ------------------------------------------------------------------

    def amr_process_name(self, amr_id: str) -> str:
        return f"amr:{amr_id}"

    def add_process(self, name: str, cmd: list, cwd: str, detail=None, env=None) -> ManagedProcess:
        proc = ManagedProcess(name=name, cmd=list(cmd), cwd=cwd, env=env, detail=detail or {})
        self.processes[name] = proc
        return proc

    def register_amr(self, entry: dict) -> ManagedProcess:
        """Create (or refresh) the managed process entry for an AMR config."""
        amr_id = entry["id"]
        cmd = [
            config.PYTHON_BIN,
            "robot/robot_node.py",
            "--id", amr_id,
            "--url", config.DEFAULT_AMR_URL,
            "--x", f"{entry['x']:.3f}",
            "--y", f"{entry['y']:.3f}",
            "--mode", self.settings.auction_mode,
        ]
        return self.add_process(
            self.amr_process_name(amr_id),
            cmd,
            cwd=str(config.BACKEND_DIR),
            detail={"amrId": amr_id, "type": "amr"},
            env=backend_env(),
        )

    def coordinator_process(self, preset: str, tasks: int, roster: list | None = None,
                            auction_mode: Optional[str] = None) -> ManagedProcess:
        if roster is None:
            roster = list(self.amrs.values())
        mode = auction_mode or self.settings.auction_mode
        cmd = self._coordinator_cmd(preset, tasks, roster, mode)
        return self.add_process(
            "coordinator",
            cmd,
            cwd=str(config.BACKEND_DIR),
            detail=self._coordinator_detail(preset, tasks, roster, mode),
            env=backend_env(),
        )

    @staticmethod
    def _coordinator_cmd(preset: str, tasks: int, roster: list, auction_mode: str) -> list:
        roster_arg = ",".join(f"{r['id']}:{r['x']}:{r['y']}" for r in roster)
        return [
            config.PYTHON_BIN,
            "-m", "server.server_node",
            "--preset", preset,
            "--tasks", str(tasks),
            "--url", config.COORDINATOR_URL,
            "--roster", roster_arg,
            "--mode", auction_mode,
        ]

    @staticmethod
    def _coordinator_detail(preset: str, tasks: int, roster: list, auction_mode: str) -> dict:
        roster_arg = ",".join(f"{r['id']}:{r['x']}:{r['y']}" for r in roster)
        return {"type": "coordinator", "preset": preset, "tasks": tasks,
                "roster": roster_arg, "auctionMode": auction_mode}

    def update_coordinator_cmd(self) -> None:
        """Refresh the coordinator's launch args from the current AMR set.

        Mutates the existing ManagedProcess in place so a running coordinator
        process is never orphaned. Only takes effect on the next (re)start.
        """
        coord = self.processes.get("coordinator")
        if coord is None:
            return
        roster = list(self.amrs.values())
        coord.cmd = self._coordinator_cmd(self.settings.preset, self.settings.tasks, roster,
                                          self.settings.auction_mode)
        coord.detail = self._coordinator_detail(self.settings.preset, self.settings.tasks, roster,
                                                self.settings.auction_mode)

    def zenohd_process(self) -> ManagedProcess:
        return self.add_process(
            "zenohd",
            [
                resolve_bin("zenohd") or "zenohd",
                "--no-multicast-scouting",
                "--listen", f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}",
            ],
            cwd=str(config.DASHBOARD_DIR),
            detail={"type": "infrastructure", "port": config.ZENOH_TCP_PORT,
                    "url": f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}", "readyCheck": "tcp"},
        )

    def bridge_process(self) -> ManagedProcess:
        return self.add_process(
            "bridge",
            [
                resolve_bin("zenoh-bridge-remote-api") or "zenoh-bridge-remote-api",
                "--no-multicast-scouting",
                "--connect", f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}",
                "--ws-port", str(config.ZENOH_WS_PORT),
            ],
            cwd=str(config.DASHBOARD_DIR),
            detail={"type": "infrastructure", "port": config.ZENOH_WS_PORT,
                    "url": f"ws/127.0.0.1:{config.ZENOH_WS_PORT}", "readyCheck": "tcp"},
        )

    def build_registry(self) -> None:
        """(Re)build the managed-process registry from persisted config. Safe to call at startup."""
        self.processes.clear()
        b = resolve_zenoh_bins()
        if not b["zenohd"] or not b["bridge"]:
            self.log("WARNING: zenohd / zenoh-bridge-remote-api binaries not found "
                     "(set ZENOH_ZENOHD / ZENOH_BRIDGE or install dashboard/tools/zenoh/)")
        entries = self.amr_store.list()
        self.zenohd_process()
        self.bridge_process()
        self.coordinator_process(self.settings.preset, self.settings.tasks, entries)
        self.amrs = {e["id"]: dict(e) for e in entries}
        for entry in entries:
            self.register_amr(entry)

    # ------------------------------------------------------------------
    # Monitor loop
    # ------------------------------------------------------------------

    def ensure_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.get_event_loop().create_task(
                self._monitor(), name="fm-monitor"
            )

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self.monitor_interval)
            now = time.time()
            for proc in list(self.processes.values()):
                if proc.proc is None:
                    continue
                rc = getattr(proc.proc, "returncode", None)
                if rc is not None:
                    await self._on_exit(proc, rc)
                    continue
                if proc.state == "STARTING":
                    await self._advance_starting(proc, now)

    async def _advance_starting(self, proc: ManagedProcess, now: float) -> None:
        ready_check = proc.detail.get("readyCheck")
        if ready_check == "tcp":
            port = int(proc.detail.get("readyPort", 0)) or 0
            if proc.name == "zenohd":
                port = config.ZENOH_TCP_PORT
            elif proc.name == "bridge":
                port = config.ZENOH_WS_PORT
            try:
                if await self.zenoh_available(port):
                    proc.state = "RUNNING"
                    self.log(f"{proc.name} RUNNING (tcp/{port} reachable)")
                    return
            except Exception as exc:
                proc.last_error = f"availability check failed: {exc}"
            if proc.started_at and now - proc.started_at > self.check_timeout_s:
                proc.last_error = f"{proc.name} did not become reachable on tcp/127.0.0.1:{port}"
                self.log(f"ERROR: {proc.last_error}")
            return
        if proc.started_at and now - proc.started_at >= self.bootstrap_s:
            proc.state = "RUNNING"
            self.log(f"{proc.name} RUNNING")

    async def _on_exit(self, proc: ManagedProcess, rc: int) -> None:
        proc.pid = None
        for t in proc.readers:
            t.cancel()
        proc.readers.clear()
        proc.proc = None
        if not proc.expected_stop and proc.state in ("STARTING", "RUNNING"):
            proc.state = "CRASHED"
            proc.last_error = f"process exited unexpectedly with returncode {rc}"
            self.log(f"ERROR: {proc.name} CRASHED (returncode {rc})")
        elif proc.state != "STOPPED":
            proc.state = "STOPPED"
            self.log(f"{proc.name} STOPPED")

    # ------------------------------------------------------------------
    # Log capture
    # ------------------------------------------------------------------

    async def _drain(self, name: str, stream, sink) -> None:
        while True:
            try:
                line = await stream.readline()
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                sink(text)

    def logs(self, name: str, limit: Optional[int] = None) -> list:
        proc = self.processes.get(name)
        if proc is None:
            raise KeyError(name)
        lines = list(proc.log_lines)
        if limit:
            lines = lines[-limit:]
        return lines

    # ------------------------------------------------------------------
    # Start / Stop / Restart
    # ------------------------------------------------------------------

    async def _check_dependencies(self, name: str) -> None:
        if name in ("bridge", "coordinator") or name.startswith("amr:"):
            if not await self.zenoh_available(config.ZENOH_TCP_PORT):
                raise DependencyError(
                    f"cannot start {name}: Zenoh router (tcp/127.0.0.1:{config.ZENOH_TCP_PORT}) "
                    "is not reachable — start Zenoh first"
                )

    async def _spawn_process(self, proc: ManagedProcess) -> None:
        proc.expected_stop = False
        try:
            child = await self.spawn(proc.cmd, cwd=proc.cwd, env=proc.env if proc.env is not None else None, log=self.log)
        except Exception as exc:
            if proc.name in ("zenohd", "zenoh-bridge-remote-api") and resolve_bin(proc.name) is None:
                env_key = {"zenohd": "ZENOH_ZENOHD", "zenoh-bridge-remote-api": "ZENOH_BRIDGE"}.get(proc.name)
                raise DependencyError(
                    f"cannot start {proc.name}: binary not found — install it or set {env_key}"
                ) from exc
            proc.last_error = f"spawn failed: {exc}"
            raise ProcessError(f"{proc.name} {proc.last_error}") from exc
        proc.proc = child
        proc.pid = getattr(child, "pid", None)
        proc.state = "STARTING"
        proc.started_at = time.time()
        proc.last_error = None
        proc.log_lines.clear()
        proc.readers = [
            asyncio.get_event_loop().create_task(
                self._drain(proc.name, child.stdout, proc.log_lines.append)
            ),
            asyncio.get_event_loop().create_task(
                self._drain(proc.name, child.stderr, proc.log_lines.append)
            ),
        ]
        self.log(f"{proc.name} started (pid {proc.pid})")

    async def start(self, name: str, wait: bool = True) -> dict:
        proc = self.processes.get(name)
        if proc is None:
            raise ProcessError(f"unknown process '{name}'")
        if proc.state in ("STARTING", "RUNNING"):
            return {"name": name, "ok": True, "state": proc.state, "detail": "already running"}
        await self._check_dependencies(name)
        self.ensure_monitor()
        await self._spawn_process(proc)
        if wait:
            await self._wait_running(proc)
        return {"name": name, "ok": True, "state": proc.state}

    async def _wait_running(self, proc: ManagedProcess) -> None:
        deadline = time.time() + self.check_timeout_s + self.bootstrap_s + 2.0
        while proc.state != "RUNNING":
            if proc.state in ("CRASHED", "STOPPED"):
                raise ProcessError(
                    f"{proc.name} exited during startup ({proc.state}): {proc.last_error or 'no error'}"
                )
            if time.time() > deadline:
                raise ProcessError(
                    f"{proc.name} did not reach RUNNING: {proc.last_error or 'still starting'}"
                )
            await asyncio.sleep(self.monitor_interval)

    async def stop(self, name: str) -> dict:
        proc = self.processes.get(name)
        if proc is None:
            raise ProcessError(f"unknown process '{name}'")
        if proc.state in ("STOPPED", "CRASHED"):
            return {"name": name, "ok": True, "state": proc.state, "detail": "not running"}
        child = proc.proc
        proc.state = "STOPPING"
        proc.expected_stop = True
        try:
            if child is not None:
                child.terminate()
        except Exception:
            pass
        try:
            if child is not None:
                await asyncio.wait_for(child.wait(), self.graceful_stop_s)
        except asyncio.TimeoutError:
            self.log(f"{name} ignoring SIGTERM — force killing")
            try:
                if child is not None:
                    child.kill()
                    await child.wait()
            except Exception:
                pass
        proc.state = "STOPPED"
        proc.pid = None
        self.log(f"{name} stopped")
        return {"name": name, "ok": True, "state": proc.state}

    async def restart(self, name: str, wait: bool = True) -> dict:
        await self.stop(name)
        return await self.start(name, wait=wait)

    # ------------------------------------------------------------------
    # AMR configuration
    # ------------------------------------------------------------------

    def next_amr_id(self) -> str:
        index = 1
        while f"AMR{index}" in self.amrs:
            index += 1
        return f"AMR{index}"

    async def create_amr(self, amr_id: str, x: float, y: float) -> dict:
        if amr_id in ("zenohd", "bridge", "coordinator", "server"):
            raise ProcessError(f"'{amr_id}' is a reserved component name")
        if amr_id in self.amrs:
            raise ProcessError(f"AMR '{amr_id}' already exists")
        entry = {"id": amr_id, "x": round(float(x), 2), "y": round(float(y), 2)}
        self.amrs[amr_id] = entry
        self.register_amr(entry)
        try:
            self.amr_store.add(entry)
        except Exception:
            self.amrs.pop(amr_id, None)
            self.processes.pop(self.amr_process_name(amr_id), None)
            raise
        self.update_coordinator_cmd()
        return entry

    async def remove_amr(self, amr_id: str) -> dict:
        proc = self.processes.get(self.amr_process_name(amr_id))
        if not proc:
            raise ProcessError(f"AMR '{amr_id}' not configured")
        if proc.state in ("STARTING", "RUNNING", "STOPPING"):
            raise ProcessError(f"AMR '{amr_id}' is still running — stop it before removing")
        self.amrs.pop(amr_id, None)
        self.processes.pop(self.amr_process_name(amr_id), None)
        if not self.amr_store.remove(amr_id):
            raise ProcessError(f"AMR '{amr_id}' not configured")
        self.update_coordinator_cmd()
        return {"ok": True, "id": amr_id}

    def set_configuration(self, preset: Optional[str] = None, tasks: Optional[int] = None,
                          auction_mode: Optional[str] = None) -> dict:
        coord = self.processes.get("coordinator")
        if coord and coord.state in ("STARTING", "RUNNING", "STOPPING"):
            raise ProcessError("stop the coordinator before changing its configuration")
        self.settings.set(preset, tasks, auction_mode)
        self.coordinator_process(self.settings.preset, self.settings.tasks, self.amr_store.list(),
                                 self.settings.auction_mode)
        return self.settings.to_dict()

    # ------------------------------------------------------------------
    # Fleet-wide operations
    # ------------------------------------------------------------------

    def startup_order(self) -> list:
        return ["zenohd", "bridge", "coordinator"] + [
            self.amr_process_name(a) for a in list(self.amrs)
        ]

    def shutdown_order(self) -> list:
        return self.startup_order()[::-1]

    async def start_all(self) -> dict:
        results = {}
        for name in self.startup_order():
            try:
                results[name] = await self.start(name)
            except (DependencyError, ProcessError) as exc:
                results[name] = {"name": name, "ok": False, "state": self.processes[name].state, "error": str(exc)}
        return results

    async def stop_all(self) -> dict:
        results = {}
        for name in self.shutdown_order():
            try:
                results[name] = await self.stop(name)
            except ProcessError as exc:
                results[name] = {"name": name, "ok": False, "state": self.processes[name].state, "error": str(exc)}
        return results

    def readiness(self) -> bool:
        infra = (self.processes.get("zenohd") or ManagedProcess("zenohd", [], "")).info()
        bridge = (self.processes.get("bridge") or ManagedProcess("bridge", [], "")).info()
        coord = (self.processes.get("coordinator") or ManagedProcess("coordinator", [], "")).info()
        if not (infra["state"] == "RUNNING" and bridge["state"] == "RUNNING" and coord["state"] == "RUNNING"):
            return False
        if self.amrs:
            return all(
                self.processes[self.amr_process_name(a)].state == "RUNNING"
                for a in self.amrs
            )
        return True

    # ------------------------------------------------------------------
    # Status snapshot for the dashboard
    # ------------------------------------------------------------------

    def info(self) -> dict:
        bins = resolve_zenoh_bins()
        return {
            "name": "fleet-manager",
            "version": 1,
            "ports": {"zenohTcp": config.ZENOH_TCP_PORT, "wsBridge": config.ZENOH_WS_PORT},
            "urls": {
                "zenoh": f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}",
                "wsBridge": f"ws/127.0.0.1:{config.ZENOH_WS_PORT}",
            },
            "binaries": bins,
            "python": config.PYTHON_BIN,
            "configuration": self.settings.to_dict(),
            "presets": ["MICRO_FULFILLMENT", "ECOMMERCE", "DISTRIBUTION"],
        }

    def status(self) -> dict:
        infra = self.processes.get("zenohd")
        bridge = self.processes.get("bridge")
        coord = self.processes.get("coordinator")
        amrs = []
        for entry in self.amrs.values():
            proc = self.processes.get(self.amr_process_name(entry["id"]))
            amrs.append({
                **entry,
                **proc.info(),
            })
        return {
            "manager": {"name": "fleet-manager", "version": 1},
            "ready": self.readiness(),
            "configuration": self.settings.to_dict(),
            "infrastructure": {
                "zenoh": infra.info() if infra else {},
                "bridge": bridge.info() if bridge else {},
            },
            "backend": {
                "coordinator": coord.info() if coord else {},
            },
            "amrs": amrs,
        }


def backend_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(config.BACKEND_DIR)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _preset_roster(preset: str) -> list:
    """Roster entries for a warehouse preset (used to seed a fresh AMR store)."""
    try:
        from server.warehouse import build_from_preset
        return build_from_preset(preset).roster()
    except Exception:
        return []