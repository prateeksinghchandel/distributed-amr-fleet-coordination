"""Configuration constants for the Fleet Manager."""

from __future__ import annotations
from pathlib import Path

# Project root (backend/ parent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
SCRIPTS_DIR = DASHBOARD_DIR / "scripts"
TOOLS_DIR = DASHBOARD_DIR / "tools" / "zenoh"
LOG_DIR = DASHBOARD_DIR / "logs"
FM_LOG_DIR = PROJECT_ROOT / "backend" / "logs"
FM_STATE_DIR = PROJECT_ROOT / "backend" / "state"
AMR_STATE_FILE = FM_STATE_DIR / "amrs.json"
SETTINGS_STATE_FILE = FM_STATE_DIR / "settings.json"

# Ports
ZENOH_TCP_PORT = 7447
ZENOH_WS_PORT = 10000

# Fleet Manager HTTP server
FM_HOST = "127.0.0.1"
FM_PORT = 8270

# Python interpreter (resolved cross-platform for Windows, Linux, and custom envs)
def _resolve_python_bin() -> str:
    import os
    import shutil
    import sys
    env_bin = os.environ.get("PYTHON_BIN")
    if env_bin and Path(env_bin).is_file():
        return str(Path(env_bin).resolve())

    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",   # Windows venv
        PROJECT_ROOT / ".venv" / "bin" / "python",           # Linux/macOS venv
        PROJECT_ROOT / "venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / "venv" / "bin" / "python",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())

    if sys.executable and Path(sys.executable).is_file():
        return str(Path(sys.executable).resolve())

    return shutil.which("python") or shutil.which("python3") or "python"


PYTHON_BIN = _resolve_python_bin()

# Process start/stop timeouts
STARTUP_TIMEOUT_S = 10.0
GRACEFUL_STOP_TIMEOUT_S = 5.0

# Zenoh availability check
ZENOH_CHECK_TIMEOUT_S = 5.0
ZENOH_CHECK_INTERVAL_S = 0.2

# Coordinator defaults
COORDINATOR_PRESET = "MICRO_FULFILLMENT"
COORDINATOR_TASKS = 1
COORDINATOR_URL = f"tcp/127.0.0.1:{ZENOH_TCP_PORT}"

# AMR defaults
DEFAULT_AMR_URL = f"tcp/127.0.0.1:{ZENOH_TCP_PORT}"
