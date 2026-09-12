import { ROBOT_STATUS } from '../Robot.js';
import { TOPICS } from '../messages/topics.js';

export const AUCTION_CONSTANTS = {
    ANNOUNCE_DELAY: 0.1,
    BID_TRANSMIT_DELAY: 0.3,
    DEADLINE: 1.0,
    TELEMETRY_PERIOD: 0.5,
    TELEMETRY_LAG: 0.05,
    BLOCKED_PENALTY: 2.5,
    CONGESTION_RADIUS: 4,
    CONGESTION_COST: 3,
    BATTERY_WEIGHT: 0.05,
    BATTERY_WARN: 20,
    BATTERY_LOW_PENALTY: 30,
};

const dist = (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1);

export function robotEligibility(robot) {
    if (!robot.online) return { eligible: false, reason: 'offline' };
    if (robot.currentTaskId && robot.status !== ROBOT_STATUS.COMPLETED) return { eligible: false, reason: 'busy' };
    return { eligible: true, reason: null };
}

export function selectWinner(bids) {
    let best = null;
    let bestBid = Infinity;
    for (const b of bids) {
        if (typeof b.bid !== 'number' || !Number.isFinite(b.bid)) continue;
        if (b.bid < bestBid || (b.bid === bestBid && (best === null || b.robotId < best))) {
            best = b.robotId;
            bestBid = b.bid;
        }
    }
    return best;
}

export function telemetryOf(robot) {
    return {
        robotId: robot.id,
        x: robot.x,
        y: robot.y,
        heading: robot.heading,
        status: robot.status,
        battery: robot.battery,
        currentTaskId: robot.currentTaskId,
        blocked: robot.blocked,
        online: robot.online,
    };
}

export class FleetAgent {
    constructor(robot, bus, hooks) {
        this.robot = robot;
        this.bus = bus;
        this.hooks = hooks;
        this.fleet = new Map();
        this.rounds = new Map();
        this.telemetryTimer = AUCTION_CONSTANTS.TELEMETRY_PERIOD;
        for (const snapshot of hooks.getFleetSnapshot()) this.fleet.set(snapshot.robotId, snapshot);
        this.unsubs = [
            bus.subscribe(TOPICS.TASK_NEW, (m) => this.onTaskNew(m)),
            bus.subscribe(TOPICS.BID_PLACED, (m) => this.onBidPlaced(m)),
            bus.subscribe(TOPICS.ROBOT_TELEMETRY, (m) => this.onRobotTelemetry(m)),
            bus.subscribe(TOPICS.TASK_ASSIGNED, (m) => this.onTaskResolved(m)),
            bus.subscribe(TOPICS.TASK_CANCELLED, (m) => this.onTaskResolved(m)),
        ];
    }

    destroy() {
        for (const unsub of this.unsubs) unsub();
        this.unsubs = [];
    }

    onTaskNew(msg) {
        const { taskId, pickup, dropoff } = msg.payload;
        if (this.rounds.has(taskId)) return;
        const round = {
            taskId,
            bids: new Map(),
            rosterSize: this.hooks.getFleetSnapshot().length,
            deadline: msg.timestamp + AUCTION_CONSTANTS.DEADLINE,
            done: false,
        };
        this.rounds.set(taskId, round);
        if (!this.robot.online) return;

        const { bid, costs, eligible, reason } = this.computeBid({ pickup, dropoff });
        const entry = eligible
            ? { bid, costs, reason }
            : { bid: null, costs: null, reason };
        this.bus.publish(TOPICS.BID_PLACED, {
            taskId,
            robotId: this.robot.id,
            ...entry,
        }, { sender: this.robot.id, delay: AUCTION_CONSTANTS.BID_TRANSMIT_DELAY });
        this.hooks.onEvent(
            eligible
                ? `[AUCTION] ${taskId} bid from ${this.robot.id}: ${bid}`
                : `[AUCTION] ${taskId} ${this.robot.id} not bidding (${reason || 'ineligible'})`
        );
    }

    computeBid(task) {
        const robot = this.robot;
        const el = robotEligibility(robot);
        if (!el.eligible) return { bid: null, costs: null, eligible: false, reason: el.reason };

        const C = AUCTION_CONSTANTS;
        let travel = dist(robot.x, robot.y, task.pickup.x, task.pickup.y);
        if (this.straightBlocked(robot.x, robot.y, task.pickup.x, task.pickup.y)) travel *= C.BLOCKED_PENALTY;
        travel += dist(task.pickup.x, task.pickup.y, task.dropoff.x, task.dropoff.y);
        travel = Math.round(travel * 100) / 100;

        let congestion = 0;
        for (const peer of this.fleet.values()) {
            if (!peer.online || peer.robotId === robot.id) continue;
            if (dist(peer.x, peer.y, task.pickup.x, task.pickup.y) < C.CONGESTION_RADIUS ||
                dist(peer.x, peer.y, task.dropoff.x, task.dropoff.y) < C.CONGESTION_RADIUS) {
                congestion += C.CONGESTION_COST;
            }
        }

        let battery = (100 - robot.battery) * C.BATTERY_WEIGHT;
        if (robot.battery < C.BATTERY_WARN) battery += C.BATTERY_LOW_PENALTY;
        battery = Math.round(battery * 100) / 100;

        const workload = 0;

        const bid = Math.round((travel + congestion + battery + workload) * 100) / 100;
        return { bid, costs: { travel, congestion, battery, workload }, eligible: true, reason: null };
    }

    onBidPlaced(msg) {
        const round = this.rounds.get(msg.payload.taskId);
        if (!round || round.done) return;
        const { robotId, bid, costs, reason } = msg.payload;
        if (!round.bids.has(robotId)) round.bids.set(robotId, { robotId, bid, costs, reason });
        this.tryFinalize(msg.payload.taskId, msg.timestamp);
    }

    onRobotTelemetry(msg) {
        this.fleet.set(msg.payload.robotId, msg.payload);
    }

    onTaskResolved(msg) {
        this.rounds.delete(msg.payload.taskId);
    }

    tryFinalize(taskId, now) {
        const round = this.rounds.get(taskId);
        if (!round || round.done) return;
        if (this.hooks.disableFinalize) return;
        const allResponded = round.bids.size >= round.rosterSize;
        const deadlinePassed = now >= round.deadline;
        if (allResponded || deadlinePassed) this.finalize(taskId);
    }

    forgetRound(taskId) {
        this.rounds.delete(taskId);
    }

    finalize(taskId) {
        const round = this.rounds.get(taskId);
        if (!round || round.done) return;
        round.done = true;
        this.rounds.delete(taskId);
        const bids = [...round.bids.values()];
        const winner = selectWinner(bids);
        let committed = false;
        if (winner) {
            if (typeof this.hooks.coordinatorCommit === 'function') {
                committed = this.hooks.coordinatorCommit(taskId, winner, bids);
                this.hooks.onEvent(
                    committed
                        ? `[AUCTION] ${taskId} committed for ${winner} by coordinator`
                        : `[AUCTION] ${taskId} commit failed for ${winner}`
                );
            } else if (winner === this.robot.id) {
                committed = this.hooks.onAssign(taskId);
                this.hooks.onEvent(`[AUCTION] ${taskId} ${committed ? 'assigned to' : 'commit failed for'} ${this.robot.id}`);
            }
        }
        this.bus.publish(TOPICS.AUCTION_RESULT, { taskId, winner, bids, committed }, { sender: this.robot.id });
    }

    tick(dt, now) {
        this.telemetryTimer -= dt;
        if (this.telemetryTimer <= 0) {
            this.telemetryTimer += AUCTION_CONSTANTS.TELEMETRY_PERIOD;
            this.publishTelemetry();
        }
        if (this.hooks.disableFinalize) return;
        for (const round of this.rounds.values()) {
            if (!round.done && now >= round.deadline) {
                this.finalize(round.taskId);
            }
        }
    }

    publishTelemetry() {
        if (this.hooks.suppressTelemetry) return;
        const robot = this.robot;
        this.bus.publish(TOPICS.ROBOT_TELEMETRY, {
            robotId: robot.id,
            x: robot.x,
            y: robot.y,
            heading: robot.heading,
            status: robot.status,
            battery: robot.battery,
            currentTaskId: robot.currentTaskId,
            blocked: robot.blocked,
            online: robot.online,
        }, { sender: robot.id, delay: AUCTION_CONSTANTS.TELEMETRY_LAG });
    }

    straightBlocked(x1, y1, x2, y2) {
        const obstacles = this.hooks.getObstacles();
        if (obstacles.length === 0) return false;
        const steps = Math.max(2, Math.ceil(dist(x1, y1, x2, y2) / 0.4));
        for (const obs of obstacles) {
            for (let i = 1; i < steps; i++) {
                const t = i / steps;
                const px = x1 + (x2 - x1) * t;
                const py = y1 + (y2 - y1) * t;
                if (obs.inflatedContains(px, py, 0.35)) return true;
            }
        }
        return false;
    }
}