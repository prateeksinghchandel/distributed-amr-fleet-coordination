#!/usr/bin/env bash
# Starts the distributed-fleet Zenoh infrastructure:
#   1. a core `zenohd` router listening on TCP (Python fleet connects here), and
#   2. the zenoh-bridge-remote-api WebSocket bridge linked to that router
#      (the browser dashboard / zenoh-ts connects here).
# Binary resolution: $ZENOH_ZENOHD > dashboard/tools/zenoh/ > PATH  (router)
#             $ZENOH_BRIDGE      > dashboard/tools/zenoh/ > PATH  (bridge)
set -u
cd "$(dirname "$0")/.."
DASH_DIR="$(pwd)"
LOG_DIR="$DASH_DIR/logs"
mkdir -p "$LOG_DIR"
BRIDGE_PID_FILE="$LOG_DIR/zenoh-bridge.pid"
ROUTER_PID_FILE="$LOG_DIR/zenohd.pid"
WS_PORT="${ZENOH_WS_PORT:-10000}"
TCP_PORT="${ZENOH_TCP_PORT:-7447}"

resolve_bin() {
    local path_var="$1" default="$2"
    if [ -n "${!path_var:-}" ] && [ -x "${!path_var}" ]; then echo "${!path_var}"; return 0; fi
    if [ -x "$DASH_DIR/tools/zenoh/$default" ]; then echo "$DASH_DIR/tools/zenoh/$default"; return 0; fi
    local found
    found="$(command -v "$default" 2>/dev/null)" && { echo "$found"; return 0; }
    return 1
}

BRIDGE="$(resolve_bin ZENOH_BRIDGE zenoh-bridge-remote-api)" || true
ROUTER="$(resolve_bin ZENOH_ZENOHD zenohd)" || true

if [ -z "$BRIDGE" ] || [ -z "$ROUTER" ]; then
    echo "Missing binaries:"
    [ -z "$ROUTER" ] && echo "  zenohd               -> Set ZENOH_ZENOHD, or place dashboard/tools/zenoh/zenohd, or install on PATH."
    [ -z "$BRIDGE" ] && echo "  zenoh-bridge-remote-api -> Set ZENOH_BRIDGE, or place dashboard/tools/zenoh/, or install on PATH."
    echo "Download: https://github.com/eclipse-zenoh/zenoh/releases/tags/1.10.1 (zenoh <ver> standalone zip -> zenohd)"
    echo "          https://github.com/eclipse-zenoh/zenoh-ts/releases (zenoh-ts <ver> standalone zip -> zenoh-bridge-remote-api)"
    exit 1
fi

start_or_detect() {
    local name="$1" pid_file="$2" pid
    if [ -f "$pid_file" ]; then
        pid="$(cat "$pid_file")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "$name already running (pid $pid)"; return 0
        fi
        rm -f "$pid_file"
    fi
    return 1
}

# 1) Core router (TCP)
if ! start_or_detect "zenohd router" "$ROUTER_PID_FILE"; then
    setsid "$ROUTER" --no-multicast-scouting --listen "tcp/127.0.0.1:$TCP_PORT" > "$LOG_DIR/zenohd.log" 2>&1 < /dev/null &
    pid=$!
    echo "$pid" > "$ROUTER_PID_FILE"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "zenohd router started (pid $pid) on tcp port $TCP_PORT"
    else
        echo "zenohd router failed to start; check logs/zenohd.log"
        exit 1
    fi
fi

# 2) WebSocket bridge linked to the router (dashboard / zenoh-ts)
if ! start_or_detect "zenoh bridge" "$BRIDGE_PID_FILE"; then
    setsid "$BRIDGE" --no-multicast-scouting --connect "tcp/127.0.0.1:$TCP_PORT" --ws-port "$WS_PORT" > "$LOG_DIR/zenoh-bridge.log" 2>&1 < /dev/null &
    pid=$!
    echo "$pid" > "$BRIDGE_PID_FILE"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "zenoh bridge started (pid $pid) on ws port $WS_PORT"
    else
        echo "zenoh bridge failed to start; check logs/zenoh-bridge.log"
        exit 1
    fi
fi

echo "Fleet infrastructure ready: Python -> tcp/127.0.0.1:$TCP_PORT, dashboard -> ws/127.0.0.1:$WS_PORT"