#!/usr/bin/env bash
# Launches the distributed fleet: 1 coordinator + 3 robot processes over Zenoh.
# Usage:
#   ./scripts/start-fleet.sh            start router + coordinator + AMR1..AMR3, then tail logs
#   ./scripts/start-fleet.sh --stop      stop all fleet processes (incl. the zenoh bridge)
#   ./scripts/start-fleet.sh --no-tail   start and print a demonstration check without tailing
set -u
cd "$(dirname "$0")/.."
DASH_DIR="$(pwd)"
LOG_DIR="$DASH_DIR/logs"
mkdir -p "$LOG_DIR"

ROBOTS="${FLEET_ROBOTS:-AMR1 AMR2 AMR3}"
ZENOH_URL="ws/127.0.0.1:${ZENOH_WS_PORT:-10000}"

stop_all() {
    for f in "$LOG_DIR"/coordinator.pid "$LOG_DIR"/AMR*.pid "$LOG_DIR"/zenohd.pid; do
        [ -f "$f" ] || continue
        PID="$(cat "$f")"
        kill "$PID" 2>/dev/null
        rm -f "$f"
    done
    sleep 1
    for f in "$LOG_DIR"/coordinator.pid "$LOG_DIR"/AMR*.pid "$LOG_DIR"/zenohd.pid; do
        [ -f "$f" ] || continue
        PID="$(cat "$f")"
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
        rm -f "$f"
    done
}

if [ "${1:-}" = "--stop" ]; then
    stop_all
    echo "fleet stopped (logs kept in $LOG_DIR)"
    exit 0
fi

TAIL_LOGS=1
if [ "${1:-}" = "--no-tail" ]; then TAIL_LOGS=0; fi

stop_all 2>/dev/null
sleep 1

# 1) Zenoh router
"$DASH_DIR/scripts/zenohd.sh" || exit 1

# 2) coordinator
node "$DASH_DIR/src/coordinator/coordinator_node.js" --url "$ZENOH_URL" --tasks "${FLEET_TASKS:-2}" \
    > "$LOG_DIR/coordinator.log" 2>&1 &
COORD_PID=$!
echo "$COORD_PID" > "$LOG_DIR/coordinator.pid"

# 3) robots
for R in $ROBOTS; do
    node "$DASH_DIR/src/robot/robot_node.js" --id "$R" --url "$ZENOH_URL" \
        > "$LOG_DIR/$R.log" 2>&1 &
    echo $! > "$LOG_DIR/$R.pid"
done

echo "fleet launched: coordinator (pid $COORD_PID) + robots ($ROBOTS)"
echo "logs: $LOG_DIR/{coordinator,AMR1,AMR2,AMR3}.log"

# 4) demonstration check
sleep "${FLEET_CHECK_WAIT:-10}"
echo
echo "--- fleet demonstration check ---"
grep -h "robot(s) in roster\|reported in\|announced to" "$LOG_DIR/coordinator.log" 2>/dev/null | tail -3
echo "-- bids --"
grep -h "bid from" "$LOG_DIR"/AMR*.log 2>/dev/null | tail -3
echo "-- results --"
grep -h "committed for .* by coordinator" "$LOG_DIR/coordinator.log" 2>/dev/null | tail -3
echo "-- assignments --"
grep -h "started task" "$LOG_DIR"/AMR*.log 2>/dev/null | tail -3
echo "-- completion --"
grep -h "completed task\|completed by" "$LOG_DIR"/AMR*.log "$LOG_DIR/coordinator.log" 2>/dev/null | tail -4

if [ "$TAIL_LOGS" = "0" ]; then
    echo
    echo "fleet running. Stop with: ./scripts/start-fleet.sh --stop"
    exit 0
fi

echo
echo "--- live logs (Ctrl-C to stop) ---"
trap 'stop_all; echo "fleet stopped"; exit 0' INT TERM
tail -n 0 -f \
    "$LOG_DIR/coordinator.log" \
    "$LOG_DIR/AMR1.log" \
    "$LOG_DIR/AMR2.log" \
    "$LOG_DIR/AMR3.log"