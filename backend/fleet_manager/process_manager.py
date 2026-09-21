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
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from common import topics
from . import config
from .charge_allocation import allocate_charge_spots, assign_charge_spot, roster_token

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
    """Resolve binary location cross-platform (Windows .exe, Linux/macOS, TOOLS_DIR, PATH)."""
    env_key = {"zenohd": "ZENOH_ZENOHD", "zenoh-bridge-remote-api": "ZENOH_BRIDGE"}.get(name)
    names_to_check = [name]
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        names_to_check = [f"{name}.exe", name]

    if env_key:
        cand = os.environ.get(env_key)
        if cand:
            p = Path(cand)
            if p.is_file() and (os.access(cand, os.X_OK) or sys.platform == "win32"):
                return str(p.resolve())

    for n in names_to_check:
        local = config.TOOLS_DIR / n
        if local.is_file() and (os.access(local, os.X_OK) or sys.platform == "win32"):
            return str(local.resolve())

    for n in names_to_check:
        found = shutil.which(n)
        if found:
            return found

    return None


def resolve_zenoh_bins() -> dict:
    router = resolve_bin("zenohd")
    bridge = resolve_bin("zenoh-bridge-remote-api")
    if bridge is None and router is not None:
        # Check if remote_api plugin is present alongside zenohd (Windows plugin mode)
        plugin_dll = config.TOOLS_DIR / "zenoh_plugin_remote_api.dll"
        plugin_so = config.TOOLS_DIR / "libzenoh_plugin_remote_api.so"
        if plugin_dll.is_file() or plugin_so.is_file():
            bridge = router
    return {
        "zenohd": router,
        "bridge": bridge,
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
    """True when something is listening on 127.0.0.1:port or localhost:port."""
    for host in ("127.0.0.1", "localhost"):
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            continue
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
        publish_cb: Optional[Callable] = None,
    ):
        self.spawn = spawn or default_spawn
        self.zenoh_available = zenoh_available or default_zenoh_available
        self.log = log or _log
        self.monitor_interval = monitor_interval
        self.bootstrap_s = bootstrap_s
        self.check_timeout_s = check_timeout_s
        self.graceful_stop_s = graceful_stop_s
        self.publish_cb = publish_cb
        self.charge_spots: dict[str, dict] = {}
        self._preset_layout_cache: dict[str, object] = {}

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

    def _preset_layout(self, preset: Optional[str] = None):
        p = preset or self.settings.preset
        if p not in self._preset_layout_cache:
            from server.warehouse import build_from_preset
            self._preset_layout_cache[p] = build_from_preset(p)
        return self._preset_layout_cache[p]

    def _reconcile_charge_spots(self) -> None:
        layout = self._preset_layout()
        self.charge_spots.clear()
        for rid, entry in self.amrs.items():
            hb = entry.get("homeBay")
            if hb and isinstance(hb, dict) and hb.get("kind"):
                self.charge_spots[rid] = hb
        self._promote_standby_to_pads(layout)
        for rid, entry in self.amrs.items():
            if rid not in self.charge_spots:
                spot = assign_charge_spot(layout, self.charge_spots, rid)
                entry["homeBay"] = spot["homeBay"]
                entry["x"] = spot["x"]
                entry["y"] = spot["y"]
                self.charge_spots[rid] = spot["homeBay"]
                try:
                    self.amr_store.update(rid, entry)
                except Exception as exc:
                    self.log(f"Failed to update AMR store for {rid}: {exc}")

    def _promote_standby_to_pads(self, layout) -> bool:
        """Upgrade standby robots to unowned charging pads and persist the change.

        Returns True when at least one standby robot was promoted. Existing
        homeBay entries are refreshed so pad-free fleets always occupy their own
        station once capacity allows (e.g. after a preset switch or a pad owner
        removal).
        """
        changed = False
        from .charge_allocation import promote_standby_to_pads
        for rid, spot in promote_standby_to_pads(layout, self.charge_spots).items():
            entry = self.amrs[rid]
            entry["homeBay"] = spot
            entry["x"] = float(spot["x"])
            entry["y"] = float(spot["y"])
            self.charge_spots[rid] = spot
            changed = True
            try:
                self.amr_store.update(rid, entry)
                self.log(f"{rid} promoted to charging pad {spot['padId']}")
            except Exception as exc:
                self.log(f"Failed to persist promotion for {rid}: {exc}")
        if changed:
            self.update_coordinator_cmd()
        return changed

    def charge_roster(self) -> list[dict]:
        roster = []
        for rid, entry in self.amrs.items():
            hb = self.charge_spots.get(rid, entry.get("homeBay"))
            item = dict(entry)
            item["homeBay"] = hb
            roster.append(item)
        return roster

    def _roster_token(self, entry: dict) -> str:
        return roster_token(entry)

    def publish_roster_update(self) -> None:
        payload = {"roster": self.charge_roster()}
        if getattr(self, "publish_cb", None):
            try:
                self.publish_cb(topics.CONTROL_ROSTER_UPDATE, payload)
            except Exception as exc:
                self.log(f"publish_cb error: {exc}")
        try:
            from robot.communication import open_session, ZenohBus
            session = open_session(f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}")
            bus = ZenohBus(session, "fleet-manager", self.log)
            bus.publish(topics.CONTROL_ROSTER_UPDATE, payload)
            session.close()
            self.log(f"Roster update published to coordinator ({len(self.charge_roster())} robots)")
        except Exception as exc:
            self.log(f"Direct Zenoh roster publish failed: {exc}")

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
            roster = self.charge_roster()
        mode = auction_mode or self.settings.auction_mode
        cmd = self._coordinator_cmd(preset, tasks, roster, mode)
        return self.add_process(
            "coordinator",
            cmd,
            cwd=str(config.BACKEND_DIR),
            detail=self._coordinator_detail(preset, tasks, roster, mode),
            env=backend_env(),
        )

    @classmethod
    def _coordinator_cmd(cls, preset: str, tasks: int, roster: list, auction_mode: str) -> list:
        roster_arg = ",".join(roster_token(r) for r in roster)
        return [
            config.PYTHON_BIN,
            "-m", "server.server_node",
            "--preset", preset,
            "--tasks", str(tasks),
            "--url", config.COORDINATOR_URL,
            "--roster", roster_arg,
            "--mode", auction_mode,
        ]

    @classmethod
    def _coordinator_detail(cls, preset: str, tasks: int, roster: list, auction_mode: str) -> dict:
        roster_arg = ",".join(roster_token(r) for r in roster)
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
        roster = self.charge_roster()
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
        bridge_bin = resolve_bin("zenoh-bridge-remote-api")
        if bridge_bin is not None:
            cmd = [
                bridge_bin,
                "--no-multicast-scouting",
                "--connect", f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}",
                "--ws-port", str(config.ZENOH_WS_PORT),
            ]
        elif resolve_bin("zenohd") and (config.TOOLS_DIR / "zenoh_plugin_remote_api.dll").is_file():
            # Windows plugin mode: zenohd running remote_api plugin with WebSocket listener
            cmd = [
                resolve_bin("zenohd"),
                "--no-multicast-scouting",
                "--listen", f"ws/127.0.0.1:{config.ZENOH_WS_PORT}",
                "--connect", f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}",
                "--plugin-search-dir", str(config.TOOLS_DIR),
                "-P", "remote_api",
            ]
        else:
            cmd = [
                "zenoh-bridge-remote-api",
                "--no-multicast-scouting",
                "--connect", f"tcp/127.0.0.1:{config.ZENOH_TCP_PORT}",
                "--ws-port", str(config.ZENOH_WS_PORT),
            ]
        return self.add_process(
            "bridge",
            cmd,
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
        self.amrs = {e["id"]: dict(e) for e in entries}
        self._reconcile_charge_spots()
        self.coordinator_process(self.settings.preset, self.settings.tasks, self.charge_roster())
        for entry in self.amrs.values():
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
        proc.pid = getattr(child, "pid", None) if child is not None else None
        proc.state = "STARTING"
        proc.started_at = time.time()
        proc.last_error = None
        proc.log_lines.clear()
        proc.readers = [] if child is None else [
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

    async def create_amr(self, amr_id: str, x: Optional[float] = None, y: Optional[float] = None) -> dict:
        if amr_id in ("zenohd", "bridge", "coordinator", "server"):
            raise ProcessError(f"'{amr_id}' is a reserved component name")
        if amr_id in self.amrs:
            raise ProcessError(f"AMR '{amr_id}' already exists")
        layout = self._preset_layout()
        self._promote_standby_to_pads(layout)
        spot = assign_charge_spot(layout, self.charge_spots, amr_id)
        pos_x = spot["x"] if x is None else round(float(x), 2)
        pos_y = spot["y"] if y is None else round(float(y), 2)
        entry = {
            "id": amr_id,
            "x": pos_x,
            "y": pos_y,
            "homeBay": spot["homeBay"],
        }
        self.amrs[amr_id] = entry
        self.charge_spots[amr_id] = spot["homeBay"]
        self.register_amr(entry)
        try:
            self.amr_store.add(entry)
        except Exception:
            self.amrs.pop(amr_id, None)
            self.charge_spots.pop(amr_id, None)
            self.processes.pop(self.amr_process_name(amr_id), None)
            raise
        self.update_coordinator_cmd()
        self.publish_roster_update()
        try:
            await self.start(self.amr_process_name(amr_id), wait=False)
            self.log(f"{amr_id} added and started (dynamic roster join)")
        except (DependencyError, ProcessError) as exc:
            self.log(f"WARN: {amr_id} added but auto-start deferred: {exc}")
        return entry

    async def remove_amr(self, amr_id: str) -> dict:
        proc = self.processes.get(self.amr_process_name(amr_id))
        if not proc:
            raise ProcessError(f"AMR '{amr_id}' not configured")
        if proc.state in ("STARTING", "RUNNING", "STOPPING"):
            self.log(f"{amr_id} is {proc.state} — stopping before removal")
            await self.stop(self.amr_process_name(amr_id))
        self.amrs.pop(amr_id, None)
        self.charge_spots.pop(amr_id, None)
        self.processes.pop(self.amr_process_name(amr_id), None)
        if not self.amr_store.remove(amr_id):
            raise ProcessError(f"AMR '{amr_id}' not configured")
        # Free pad from the removed owner: promote a standby robot into it
        self._promote_standby_to_pads(self._preset_layout())
        self.update_coordinator_cmd()
        self.publish_roster_update()
        return {"ok": True, "id": amr_id}

    def set_configuration(self, preset: Optional[str] = None, tasks: Optional[int] = None,
                          auction_mode: Optional[str] = None) -> dict:
        coord = self.processes.get("coordinator")
        if coord and coord.state in ("STARTING", "RUNNING", "STOPPING"):
            raise ProcessError("stop the coordinator before changing its configuration")
        self.settings.set(preset, tasks, auction_mode)
        self._reconcile_charge_spots()
        self.coordinator_process(self.settings.preset, self.settings.tasks, self.charge_roster(),
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
        """Readiness reflects the coordination stack, not AMR lifecycle state.

        AMRs are individually manageable (start/stop/remove live, without a
        fleet restart), so a STOPPED or STARTING AMR must not flag the whole
        fleet as unready — otherwise the dashboard unmounts to the startup
        screen every time an AMR is added/stopped/removed.
        """
        infra = (self.processes.get("zenohd") or ManagedProcess("zenohd", [], "")).info()
        bridge = (self.processes.get("bridge") or ManagedProcess("bridge", [], "")).info()
        coord = (self.processes.get("coordinator") or ManagedProcess("coordinator", [], "")).info()
        return (
            infra["state"] == "RUNNING"
            and bridge["state"] == "RUNNING"
            and coord["state"] == "RUNNING"
        )

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
        from .charge_allocation import allocate_charge_spots
        layout = build_from_preset(preset)
        robot_ids = [r.id for r in layout.robots]
        return allocate_charge_spots(layout, robot_ids)
    except Exception:
        return []