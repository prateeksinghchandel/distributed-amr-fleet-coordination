"""Unit tests for the Fleet Manager process-management layer.

Process spawning and Zenoh availability are faked (no real OS processes or
Zenoh routers required).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import pytest

from fleet_manager.amr_store import AmrStore, SettingsStore
from fleet_manager.process_manager import (
    DependencyError,
    ProcessError,
    ProcessManager,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class ScriptedStream:
    """Async byte stream used in place of a subprocess pipe."""

    def __init__(self):
        self._lines = deque()
        self._closed = False
        self._event = asyncio.Event()

    def emit(self, line: bytes) -> None:
        self._lines.append(line)
        self._event.set()

    def close(self) -> None:
        self._closed = True
        self._event.set()

    async def readline(self) -> bytes:
        while True:
            if self._lines:
                return self._lines.popleft()
            if self._closed:
                return b""
            await self._event.wait()
            self._event.clear()


class FakeProcess:
    _pid_counter = 5000

    def __init__(self, name: str, terminate_order: list | None = None):
        self.pid = FakeProcess._pid_counter
        FakeProcess._pid_counter += 1
        self.name = name
        self.stdout = ScriptedStream()
        self.stderr = ScriptedStream()
        self.returncode = None
        self._exited = asyncio.Event()
        self.terminated = False
        self.killed = False
        self.ignore_terminate = False
        self._terminate_order = terminate_order

    async def wait(self):
        await self._exited.wait()
        return self.returncode

    def terminate(self):
        if self._terminate_order is not None:
            self._terminate_order.append(self.name)
        self.terminated = True
        if not self.ignore_terminate:
            self._exit(-15)

    def kill(self):
        if self._terminate_order is not None:
            self._terminate_order.append(f"{self.name}!KILL")
        self.killed = True
        self._exit(-9)

    def crash(self, rc: int = 1) -> None:
        self._exit(rc)

    def _exit(self, rc: int) -> None:
        self.returncode = rc
        self._exited.set()


class FakeSpawn:
    def __init__(self):
        self.calls = []          # [{name, args, cwd, process}]
        self.by_name = {}
        self.terminate_order: list = []

    async def __call__(self, args, *, cwd, env=None, log=None):
        name = self._name(args)
        proc = FakeProcess(name, terminate_order=self.terminate_order)
        self.calls.append({"name": name, "args": list(args), "cwd": cwd, "process": proc})
        self.by_name[name] = proc
        return proc

    @staticmethod
    def _name(args) -> str:
        joined = " ".join(str(a) for a in args)
        if "server_node" in joined:
            return "coordinator"
        if "robot_node" in joined:
            i = args.index("--id")
            return f"amr:{args[i + 1]}"
        if "zenohd" in joined and "bridge" not in joined:
            return "zenohd"
        if "bridge-remote-api" in joined:
            return "bridge"
        return "unknown"


async def _always_up(_port: int) -> bool:
    return True


async def _always_down(_port: int) -> bool:
    return False


async def wait_for(predicate, timeout: float = 3.0, msg: str = "condition never became true"):
    deadline = time.time() + timeout
    while not predicate():
        if time.time() > deadline:
            raise AssertionError(msg)
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_pm(tmp_path, spawn=None, zenoh=_always_up, preset="MICRO_FULFILLMENT", tasks=1):
    from server.warehouse import build_from_preset
    roster = build_from_preset(preset).roster()
    store = AmrStore(tmp_path / "amrs.json", preset, roster, log=lambda m: None)
    settings = SettingsStore(tmp_path / "settings.json", default_preset=preset, default_tasks=tasks)
    spawn = spawn or FakeSpawn()
    pm = ProcessManager(
        spawn=spawn,
        zenoh_available=zenoh,
        log=lambda m: None,
        monitor_interval=0.01,
        bootstrap_s=0.05,
        check_timeout_s=0.5,
        graceful_stop_s=0.05,
        amr_store=store,
        settings=settings,
    )
    return pm, spawn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_zenoh(tmp_path):
    pm, spawn = make_pm(tmp_path)
    assert pm.processes["zenohd"].state == "STOPPED"

    await pm.start("zenohd")
    await wait_for(lambda: pm.processes["zenohd"].state == "RUNNING",
                   msg="zenohd never became RUNNING")

    assert pm.processes["zenohd"].pid == spawn.by_name["zenohd"].pid
    args = spawn.calls[0]["args"]
    assert args[0].endswith("zenohd")
    assert "--listen" in args
    assert "tcp/127.0.0.1:7447" in args
    assert pm.status()["ready"] is False   # rest of the stack not started


@pytest.mark.asyncio
async def test_stop_zenoh(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("zenohd")
    await wait_for(lambda: pm.processes["zenohd"].state == "RUNNING")

    result = await pm.stop("zenohd")
    assert result["state"] == "STOPPED"
    assert pm.processes["zenohd"].state == "STOPPED"
    assert pm.processes["zenohd"].pid is None
    assert spawn.by_name["zenohd"].terminated is True


@pytest.mark.asyncio
async def test_start_coordinator(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("coordinator")
    await wait_for(lambda: pm.processes["coordinator"].state == "RUNNING",
                   msg="coordinator never became RUNNING")

    args = spawn.calls[0]["args"]
    joined = " ".join(args)
    assert "server.server_node" in joined
    assert "--preset MICRO_FULFILLMENT" in joined
    assert "--tasks 1" in joined
    assert "--url tcp/127.0.0.1:7447" in joined
    assert "AMR1:" in joined and "AMR2:" in joined


@pytest.mark.asyncio
async def test_start_amr(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR1")
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "RUNNING",
                   msg="AMR1 never became RUNNING")

    args = [c["args"] for c in spawn.calls if c["name"] == "amr:AMR1"][0]
    joined = " ".join(args)
    assert args[0].endswith("python")
    assert "robot/robot_node.py" in joined
    assert "--id AMR1" in joined
    assert "--url tcp/127.0.0.1:7447" in joined


@pytest.mark.asyncio
async def test_stop_amr(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR2")
    await wait_for(lambda: pm.processes["amr:AMR2"].state == "RUNNING")

    result = await pm.stop("amr:AMR2")
    assert result["state"] == "STOPPED"
    assert pm.processes["amr:AMR2"].state == "STOPPED"
    assert spawn.by_name["amr:AMR2"].terminated is True


@pytest.mark.asyncio
async def test_crashed_process_detection(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR1")
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "RUNNING")

    spawn.by_name["amr:AMR1"].crash(rc=13)
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "CRASHED",
                   msg="AMR1 was not marked CRASHED after unexpected exit")

    proc = pm.processes["amr:AMR1"]
    assert proc.pid is None
    assert "13" in (proc.last_error or "")


@pytest.mark.asyncio
async def test_crashed_during_startup_detection(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR2")
    # dies before the bootstrap window elapses
    spawn.by_name["amr:AMR2"].crash(rc=1)
    await wait_for(lambda: pm.processes["amr:AMR2"].state == "CRASHED",
                   msg="AMR2 was not marked CRASHED during startup")


@pytest.mark.asyncio
async def test_dependency_handling_refuses_without_zenoh(tmp_path):
    pm, spawn = make_pm(tmp_path, zenoh=_always_down)

    with pytest.raises(DependencyError):
        await pm.start("bridge")
    with pytest.raises(DependencyError):
        await pm.start("coordinator")
    with pytest.raises(DependencyError):
        await pm.start("amr:AMR1")

    # nothing was spawned
    assert spawn.calls == []
    assert pm.processes["coordinator"].state == "STOPPED"
    assert pm.processes["amr:AMR1"].state == "STOPPED"


@pytest.mark.asyncio
async def test_zenoh_can_start_independently_of_dependencies(tmp_path):
    pm, spawn = make_pm(tmp_path, zenoh=_always_up)
    # zenohd itself must start even when everything else is down
    await pm.start("zenohd")
    await wait_for(lambda: pm.processes["zenohd"].state == "RUNNING")
    assert spawn.calls[0]["name"] == "zenohd"


@pytest.mark.asyncio
async def test_create_amr(tmp_path):
    pm, spawn = make_pm(tmp_path)

    entry = await pm.create_amr("AMR7", 3.5, 4.5)
    assert entry == {"id": "AMR7", "x": 3.5, "y": 4.5}
    assert "AMR7" in pm.amrs
    assert "amr:AMR7" in pm.processes
    assert pm.amr_store.get("AMR7") == entry

    with pytest.raises(ProcessError):
        await pm.create_amr("server", 0, 0)      # reserved name
    with pytest.raises(ProcessError):
        await pm.create_amr("AMR1", 0, 0)        # duplicate

    # persisted on disk — a fresh store sees it
    roster = pm.amr_store.list()
    store2 = AmrStore(tmp_path / "amrs.json", "MICRO_FULFILLMENT", roster, log=lambda m: None)
    assert store2.get("AMR7") == entry


@pytest.mark.asyncio
async def test_remove_amr(tmp_path):
    pm, spawn = make_pm(tmp_path)

    await pm.create_amr("AMR7", 1, 2)
    result = await pm.remove_amr("AMR7")
    assert result["ok"] is True
    assert "AMR7" not in pm.amrs
    assert "amr:AMR7" not in pm.processes
    assert pm.amr_store.get("AMR7") is None

    with pytest.raises(ProcessError):
        await pm.remove_amr("AMR7")              # already gone


@pytest.mark.asyncio
async def test_remove_running_amr_refused(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR1")
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "RUNNING")

    with pytest.raises(ProcessError):
        await pm.remove_amr("AMR1")
    assert "AMR1" in pm.amrs


@pytest.mark.asyncio
async def test_start_all_ordering(tmp_path):
    pm, spawn = make_pm(tmp_path)   # preseeded AMR1, AMR2
    results = await pm.start_all()

    names = [c["name"] for c in spawn.calls]
    assert names == ["zenohd", "bridge", "coordinator", "amr:AMR1", "amr:AMR2"]

    for name in names:
        assert results[name]["ok"] is True, f"{name} failed: {results[name]}"
    for name in names:
        assert pm.processes[name].state == "RUNNING"
    assert pm.status()["ready"] is True


@pytest.mark.asyncio
async def test_stop_all_ordering(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start_all()
    await wait_for(lambda: pm.status()["ready"] is True)

    results = await pm.stop_all()
    assert spawn.terminate_order == [
        "amr:AMR2", "amr:AMR1", "coordinator", "bridge", "zenohd",
    ]
    for name in spawn.by_name:
        assert results[name]["ok"] is True
    for name in pm.processes:
        assert pm.processes[name].state == "STOPPED"
    assert pm.status()["ready"] is False


@pytest.mark.asyncio
async def test_stop_all_force_kills_uncooperative_process(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR1")
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "RUNNING")

    spawn.by_name["amr:AMR1"].ignore_terminate = True   # ignores SIGTERM
    result = await pm.stop("amr:AMR1")
    assert result["state"] == "STOPPED"
    assert spawn.by_name["amr:AMR1"].terminated is True
    assert spawn.by_name["amr:AMR1"].killed is True


@pytest.mark.asyncio
async def test_restart_amr(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR2")
    await wait_for(lambda: pm.processes["amr:AMR2"].state == "RUNNING")
    old_pid = pm.processes["amr:AMR2"].pid

    await pm.restart("amr:AMR2")
    await wait_for(lambda: pm.processes["amr:AMR2"].state == "RUNNING")
    assert spawn.by_name["amr:AMR2"].killed is False   # graceful path
    assert pm.processes["amr:AMR2"].pid != old_pid


@pytest.mark.asyncio
async def test_create_amr_while_coordinator_running_keeps_process_managed(tmp_path):
    """Adding an AMR must refresh the coordinator's roster args for the next
    restart WITHOUT orphaning the currently-running coordinator process."""
    pm, spawn = make_pm(tmp_path)
    await pm.start_all()
    await wait_for(lambda: pm.status()["ready"] is True)

    running_coord = pm.processes["coordinator"]
    assert running_coord.proc is not None

    await pm.create_amr("AMR7", 3.0, 4.0)
    assert pm.processes["coordinator"] is running_coord          # object not replaced
    assert "AMR7:" in " ".join(pm.processes["coordinator"].cmd)  # roster refreshed
    assert running_coord.state == "RUNNING"

    await pm.restart("coordinator")
    await wait_for(lambda: pm.processes["coordinator"].state == "RUNNING")
    # the old coordinator process was gracefully stopped, not left orphaned
    assert "AMR7:" in " ".join(pm.processes["coordinator"].cmd)
    assert pm.processes["coordinator"].proc is not None
    coord_spawns = [c["process"] for c in spawn.calls if c["name"] == "coordinator"]
    assert coord_spawns[0].terminated is True


@pytest.mark.asyncio
async def test_log_capture(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("amr:AMR1")
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "RUNNING")

    proc = spawn.by_name["amr:AMR1"]
    proc.stdout.emit(b"[boot] zenoh session opened\n")
    proc.stdout.emit(b"[bid] AMR1 bid 12.5\n")
    proc.stderr.emit(b"[warn] low battery\n")
    proc.stdout.close()
    proc.stderr.close()

    await wait_for(lambda: len(pm.logs("amr:AMR1")) >= 3,
                   msg="log lines were not captured")
    lines = pm.logs("amr:AMR1")
    assert "[boot] zenoh session opened" in lines
    assert "[bid] AMR1 bid 12.5" in lines
    assert "[warn] low battery" in lines


@pytest.mark.asyncio
async def test_set_configuration(tmp_path):
    pm, spawn = make_pm(tmp_path)
    pm.set_configuration(preset="ECOMMERCE", tasks=3)

    joined = " ".join(pm.processes["coordinator"].cmd)
    assert "--preset ECOMMERCE" in joined
    assert "--tasks 3" in joined
    assert pm.settings.to_dict() == {"preset": "ECOMMERCE", "tasks": 3}


@pytest.mark.asyncio
async def test_set_configuration_refused_while_running(tmp_path):
    pm, spawn = make_pm(tmp_path)
    await pm.start("coordinator")
    await wait_for(lambda: pm.processes["coordinator"].state == "RUNNING")

    with pytest.raises(ProcessError):
        pm.set_configuration(preset="ECOMMERCE")


@pytest.mark.asyncio
async def test_readiness_reflects_stack(tmp_path):
    pm, spawn = make_pm(tmp_path)
    assert pm.status()["ready"] is False

    await pm.start("zenohd")
    await wait_for(lambda: pm.processes["zenohd"].state == "RUNNING")
    assert pm.status()["ready"] is False

    await pm.start("amr:AMR1")
    await wait_for(lambda: pm.processes["amr:AMR1"].state == "RUNNING")
    assert pm.status()["ready"] is False           # coordinator/bridge still down

    await pm.start_all()
    await wait_for(lambda: pm.status()["ready"] is True)