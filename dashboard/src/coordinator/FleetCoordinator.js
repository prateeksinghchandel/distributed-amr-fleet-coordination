import { Warehouse } from '../simulation/Warehouse.js';
import { ROBOT_STATUS } from '../simulation/Robot.js';
import { Task, TASK_STATUS } from '../simulation/Task.js';
import { validateTask } from '../simulation/TaskGenerator.js';
import { TOPICS } from '../simulation/messages/topics.js';
import { FleetAgent } from '../simulation/fleet/FleetAgent.js';

export const MAX_AUCTION_RETRIES = 3;
export const MAX_AUCTION_HISTORY = 20;
export const MAX_RANDOM_TASKS = 100;
export const WORLD_HEARTBEAT_SECONDS = 1.0;
export const DEFAULT_ROSTER = [
    { id: 'AMR1', x: 5, y: 5 },
    { id: 'AMR2', x: 14, y: 7 },
    { id: 'AMR3', x: 24, y: 6 },
];

function telemetrySnapshot(id) {
    return {
        robotId: id,
        x: null,
        y: null,
        heading: 0,
        status: ROBOT_STATUS.IDLE,
        battery: 100,
        currentTaskId: null,
        blocked: false,
        online: true,
    };
}

export class FleetCoordinator {
    constructor(bus, options) {
        this.bus = bus;
        this.width = options.width;
        this.height = options.height;
        this.roster = options.roster || DEFAULT_ROSTER;
        this.log = typeof options.onEvent === 'function' ? options.onEvent : (msg) => console.log(`[coordinator] ${msg}`);
        this.warehouse = new Warehouse(this.width, this.height);
        if (options.obstacles) {
            for (const o of options.obstacles) {
                this.warehouse.addObstacle(o.x, o.y, o.width, o.height);
            }
        }

        this.fleet = new Map();
        this.tasks = [];
        this.auctionEnabled = true;
        this.auctionQueue = [];
        this.auctionInFlightId = null;
        this.auctionRetries = new Map();
        this.waitingTasks = new Set();
        this.resultSeen = new Set();
        this.auctions = [];

        this.started = false;
        this.startOptions = options.start || {};
        this.lastWorldPublish = -Infinity;

        this.serverRobot = {
            id: 'server',
            x: 0,
            y: 0,
            heading: 0,
            radius: 0.4,
            maxSpeed: 0,
            speed: 0,
            velocity: { x: 0, y: 0 },
            targetX: null,
            targetY: null,
            status: ROBOT_STATUS.IDLE,
            blocked: false,
            online: false,
            battery: 100,
            color: '#888888',
            currentTaskId: null,
        };

        this.agent = new FleetAgent(this.serverRobot, this.bus, {
            onEvent: (msg) => this.log(msg),
            onAssign: () => false,
            coordinatorCommit: (taskId, winner) => this.assignTask(taskId, winner, { source: 'auction' }),
            getFleetSnapshot: () => this.roster.map((r) => telemetrySnapshot(r.id)),
            getObstacles: () => this.warehouse.obstacles,
            suppressTelemetry: true,
        });

        this.unsubs = [
            this.bus.subscribe(TOPICS.AUCTION_RESULT, ({ payload }) => this.handleAuctionResult(payload)),
            this.bus.subscribe(TOPICS.ROBOT_TELEMETRY, ({ payload }) => this.onRobotTelemetry(payload)),
        ];
        this.publishWorld();
    }

    get bounds() {
        return { width: this.width, height: this.height };
    }

    get obstacles() {
        return this.warehouse.obstacles;
    }

    publishWorld() {
        this.bus.publish(TOPICS.WORLD_STATE, {
            width: this.width,
            height: this.height,
            obstacles: this.obstacles.map((o) => ({ id: o.id, x: o.x, y: o.y, width: o.width, height: o.height })),
            roster: this.roster.map((r) => ({ id: r.id, x: r.x, y: r.y })),
        }, { sender: 'server' });
        this.lastWorldPublish = Date.now() / 1000;
        this.log(`World state published (${this.width}×${this.height}, ${this.roster.length} robot(s) in roster)`);
    }

    onRobotTelemetry(payload) {
        this.fleet.set(payload.robotId, payload);
    }

    anyEligibleRobot() {
        for (const r of this.fleet.values()) {
            if (r.online && (r.currentTaskId === null || r.status === ROBOT_STATUS.COMPLETED)) return true;
        }
        return false;
    }

    readyToStart() {
        return this.roster.every((r) => this.fleet.has(r.id));
    }

    step(dt, now) {
        const wallNow = Date.now() / 1000;
        this.bus.deliverDue(now);
        this.agent.tick(dt, now);
        this.syncTasks();
        this.reconsiderWaitingTasks();
        if (!this.started) {
            if (this.readyToStart()) {
                this.started = true;
                this.log(`All ${this.roster.length} robot(s) reported in; starting task dispatch`);
                if (this.startOptions.taskSpecs) {
                    for (const spec of this.startOptions.taskSpecs) this.createTask(spec.pickup, spec.dropoff, true);
                }
                if (this.startOptions.count) this.generateRandomTasks(this.startOptions.count);
            }
        }
        this.advanceAuction();
        if (wallNow - this.lastWorldPublish >= WORLD_HEARTBEAT_SECONDS) this.publishWorld();
    }

    announceTask(task) {
        this.bus.publish(TOPICS.TASK_NEW, {
            taskId: task.id,
            pickup: { x: task.pickup.x, y: task.pickup.y },
            dropoff: { x: task.dropoff.x, y: task.dropoff.y },
            priority: task.priority,
        }, { sender: 'server' });
    }

    advanceAuction() {
        if (this.auctionInFlightId) return;
        if (!this.auctionEnabled) return;
        while (this.auctionQueue.length > 0) {
            const taskId = this.auctionQueue.shift();
            const task = this.tasks.find((t) => t.id === taskId);
            if (!task || task.status !== TASK_STATUS.PENDING) continue;
            if (!this.anyEligibleRobot()) {
                this.waitingTasks.add(taskId);
                this.log(`[AUCTION] Task ${taskId} deferred (no eligible robot)`);
                continue;
            }
            this.auctionInFlightId = taskId;
            this.log(`[AUCTION] Task ${taskId} announced to ${this.roster.length} robot(s)`);
            this.announceTask(task);
            return;
        }
    }

    reconsiderWaitingTasks() {
        if (this.waitingTasks.size === 0 || !this.anyEligibleRobot()) return;
        for (const taskId of [...this.waitingTasks]) {
            const task = this.tasks.find((t) => t.id === taskId);
            if (task && task.status === TASK_STATUS.PENDING) this.auctionQueue.push(taskId);
        }
        this.waitingTasks.clear();
    }

    handleAuctionResult({ taskId, winner, bids }) {
        const task = this.tasks.find((t) => t.id === taskId);
        if (!task || task.status === TASK_STATUS.CANCELLED) return;
        if (this.resultSeen.has(taskId)) return;
        this.resultSeen.add(taskId);
        if (this.auctionInFlightId === taskId) this.auctionInFlightId = null;
        const committed = Boolean(task.status !== TASK_STATUS.PENDING);
        this.recordAuction({ taskId, winner, bids, committed });
        if (!committed && task && task.status !== TASK_STATUS.CANCELLED) {
            if (winner === null) {
                this.waitingTasks.add(taskId);
                this.log(`[AUCTION] Task ${taskId} deferred (no eligible robot)`);
            } else {
                const retries = (this.auctionRetries.get(taskId) || 0) + 1;
                if (retries <= MAX_AUCTION_RETRIES) {
                    this.auctionRetries.set(taskId, retries);
                    this.auctionQueue.unshift(taskId);
                    this.log(`[AUCTION] Task ${taskId} re-queued for auction (attempt ${retries})`);
                } else {
                    this.auctionRetries.delete(taskId);
                    this.waitingTasks.add(taskId);
                    this.log(`[AUCTION] Task ${taskId} deferred (commit failed ${retries}×)`);
                }
            }
        }
        this.advanceAuction();
    }

    recordAuction({ taskId, winner, bids, committed }) {
        this.auctions.push({
            taskId,
            winner,
            bids: (bids || []).map((b) => ({
                robotId: b.robotId,
                bid: b.bid,
                costs: b.costs,
                reason: b.reason || null,
            })),
            reason: winner ? (committed ? null : 'commit failed') : 'no eligible robot',
            time: new Date().toLocaleTimeString(),
        });
        if (this.auctions.length > MAX_AUCTION_HISTORY) this.auctions.shift();
    }

    createTask(pickup, dropoff, announce = false) {
        const error = validateTask(this.warehouse, pickup, dropoff);
        if (error) {
            this.log(error);
            return null;
        }
        const task = new Task(pickup, dropoff);
        this.tasks.push(task);
        this.log(`Task ${task.id} created (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
        if (announce) {
            this.auctionQueue.push(task.id);
            this.advanceAuction();
        }
        return task;
    }

    generateRandomTasks(count) {
        const target = Math.max(0, Math.min(count, MAX_RANDOM_TASKS));
        let created = 0;
        let attempts = 0;
        while (created < target && attempts < 2000) {
            attempts += 1;
            const pickup = this.warehouse.randomFreePoint();
            const dropoff = this.warehouse.randomFreePoint();
            if (pickup && dropoff && !validateTask(this.warehouse, pickup, dropoff)) {
                const task = new Task(pickup, dropoff);
                this.tasks.push(task);
                this.log(`Task ${task.id} created (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
                this.auctionQueue.push(task.id);
                this.advanceAuction();
                created += 1;
            }
        }
        this.log(`Random generation: ${created} task(s) created`);
        return created;
    }

    assignTask(taskId, robotId, options = {}) {
        const task = this.tasks.find((t) => t.id === taskId);
        const robot = this.fleet.get(robotId);
        if (!task) {
            this.log(`Cannot assign: task ${taskId} not found`);
            return false;
        }
        if (!robot) {
            this.log(`Cannot assign ${task.id}: robot ${robotId} not in fleet view`);
            return false;
        }
        if (!robot.online) {
            this.log(`Cannot assign ${task.id}: ${robotId} is offline`);
            return false;
        }
        if (robot.currentTaskId && robot.status !== ROBOT_STATUS.COMPLETED) {
            this.log(`Cannot assign ${task.id}: ${robotId} is busy with ${robot.currentTaskId}`);
            return false;
        }
        if (task.status !== TASK_STATUS.PENDING) {
            this.log(`Cannot assign ${task.id}: task is ${task.status}`);
            return false;
        }
        if (options.source === 'auction' && !this.auctionEnabled) {
            this.log(`Cannot assign ${task.id}: auction disabled`);
            return false;
        }
        const resolvedRobotId = robot.robotId || robot.id;
        task.assignedRobotId = resolvedRobotId;
        task.status = TASK_STATUS.ASSIGNED;
        this.auctionQueue = this.auctionQueue.filter((id) => id !== task.id);
        this.auctionRetries.delete(task.id);
        this.waitingTasks.delete(task.id);
        if (this.auctionInFlightId === task.id) {
            this.auctionInFlightId = null;
            this.advanceAuction();
        }
        this.bus.publish(TOPICS.TASK_ASSIGNED, {
            taskId: task.id,
            robotId: resolvedRobotId,
            source: options.source || 'manual',
        }, { sender: options.source === 'auction' ? resolvedRobotId : 'server' });
        const via = options.source === 'auction' ? ' (via auction)' : '';
        this.log(`${robotId} started task ${task.id}${via}`);
        return true;
    }

    cancelTask(taskId) {
        const task = this.tasks.find((t) => t.id === taskId);
        if (!task) return;
        if (task.status === TASK_STATUS.ASSIGNED ||
            task.status === TASK_STATUS.PICKING_UP ||
            task.status === TASK_STATUS.DELIVERING) {
            task.assignedRobotId = null;
        }
        task.status = TASK_STATUS.CANCELLED;
        this.auctionQueue = this.auctionQueue.filter((id) => id !== task.id);
        this.auctionRetries.delete(task.id);
        this.waitingTasks.delete(task.id);
        if (this.auctionInFlightId === task.id) {
            this.auctionInFlightId = null;
            this.advanceAuction();
        }
        this.bus.publish(TOPICS.TASK_CANCELLED, { taskId: task.id }, { sender: 'server' });
        this.log(`Task ${task.id} cancelled`);
    }

    syncTasks() {
        for (const task of this.tasks) {
            if (!task.assignedRobotId) continue;
            const robot = this.fleet.get(task.assignedRobotId);
            if (!robot || robot.currentTaskId !== task.id) continue;
            if (robot.status === ROBOT_STATUS.COMPLETED) {
                if (task.status !== TASK_STATUS.COMPLETED) {
                    task.status = TASK_STATUS.COMPLETED;
                    task.completedAt = Date.now();
                    this.log(`Task ${task.id} completed by ${robot.robotId}`);
                }
            } else if (robot.status === ROBOT_STATUS.FAILED || !robot.online) {
                if (task.status !== TASK_STATUS.FAILED) {
                    task.status = TASK_STATUS.FAILED;
                    this.log(`Task ${task.id} failed (${robot.robotId})`);
                }
            } else if (robot.status === ROBOT_STATUS.PICKING) {
                if (task.status !== TASK_STATUS.PICKING_UP) task.status = TASK_STATUS.PICKING_UP;
            } else if (robot.status === ROBOT_STATUS.MOVING_TO_DROPOFF || robot.status === ROBOT_STATUS.DROPPING) {
                if (task.status !== TASK_STATUS.DELIVERING) task.status = TASK_STATUS.DELIVERING;
            }
        }
    }

    setAuctionEnabled(enabled) {
        this.auctionEnabled = Boolean(enabled);
        if (!this.auctionEnabled) {
            this.auctionInFlightId = null;
            this.auctionQueue = [];
            this.auctionRetries.clear();
            this.waitingTasks.clear();
        }
        this.log(`[AUCTION] auto-assign via auction ${this.auctionEnabled ? 'enabled' : 'disabled'}`);
    }

    dispose() {
        for (const unsub of this.unsubs) unsub();
        this.unsubs = [];
        if (this.agent) this.agent.destroy();
    }
}

export function createFleetCoordinator(bus, options) {
    return new FleetCoordinator(bus, options);
}