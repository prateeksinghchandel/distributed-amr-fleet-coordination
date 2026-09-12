#!/usr/bin/env bash
# Starts the Zenoh remote-api WebSocket bridge (router) the distributed fleet connects to.
# Binary resolution: $ZENOH_BRIDGE  >  dashboard/tools/zenoh/  >  PATH.
set -u
cd "$(dirname "$0")/.."
DASH_DIR="$(pwd)"
LOG_DIR="$DASH_DIR/logs"
mkdir -p "$LOG_DIR"
PID_FILE="$LOG_DIR/zenohd.pid"
WS_PORT="${ZENOH_WS_PORT:-10000}"

resolve_bridge() {
    if [ -n "${ZENOH_BRIDGE:-}" ] && [ -x "$ZENOH_BRIDGE" ]; then
        echo "$ZENOH_BRIDGE"; return 0
    fi
    if [ -x "$DASH_DIR/tools/zenoh/zenoh-bridge-remote-api" ]; then
        echo "$DASH_DIR/tools/zenoh/zenoh-bridge-remote-api"; return 0
    fi
    if command -v zenoh-bridge-remote-api >/dev/null 2>&1; then
        command -v zenoh-bridge-remote-api; return 0
    fi
    return 1
}

BRIDGE="$(resolve_bridge)" || {
    echo "zenoh-bridge-remote-api not found."
    echo "Set ZENOH_BRIDGE, or place it at dashboard/tools/zenoh/, or install it on PATH."
    echo "Download: https://github.com/eclipse-zenoh/zenoh-ts/releases (zenoh-ts <ver> standalone zip -> zenoh-bridge-remote-api)"
    exit 1
}

if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "zenoh bridge already running (pid $OLD_PID) on ws port $WS_PORT"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

setsid "$BRIDGE" --no-multicast-scouting --ws-port "$WS_PORT" > "$LOG_DIR/zenohd.log" 2>&1 < /dev/null &
BRIDGE_PID=$!
echo "$BRIDGE_PID" > "$PID_FILE"
sleep 2
if kill -0 "$BRIDGE_PID" 2>/dev/null; then
    echo "zenoh bridge started (pid $BRIDGE_PID) on ws port $WS_PORT"
else
    echo "zenoh bridge failed to start; check logs/zenohd.log"
    exit 1
fi