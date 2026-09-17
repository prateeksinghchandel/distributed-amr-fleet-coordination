import { Warehouse, clampNum } from './Warehouse.js';
import { Robot, ROBOT_STATUS } from './Robot.js';
import { Task, TASK_STATUS } from './Task.js';
import { validateTask, TASK_POINT_PADDING } from './TaskGenerator.js';
import { createTransport } from './messages/transport.js';
import { TOPICS } from './messages/topics.js';
import { FleetAgent, AUCTION_CONSTANTS, telemetryOf } from './fleet/FleetAgent.js';
import { buildWarehouseConfig } from './WarehouseBuilder.js';
import { ROBOT_COLORS } from './robotColors.js';

export const DEFAULT_WIDTH = 30;
export const DEFAULT_HEIGHT = 20;
export const MIN_DIM = 4;
export const MAX_DIM = 100;
export const MAX_RANDOM_TASKS = 100;
export const MAX_AUCTION_RETRIES = 3;

const MAX_EVENTS = 500;
const MAX_AUCTION_HISTORY = 20;

export class Simulation {
    constructor(width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT, transportOptions) {
        this.warehouse = new Warehouse(width, height);
        this.selectedRobotId = null;
        this.selectedObstacleId = null;
        this.running = true;
        this.speed = 1;
        this.time = 0;
        this.events = [];
        this.onEvent = null;
        this.transportOptions = transportOptions || {};
        this.bus = createTransport(() => this.time, this.transportOptions);
        this.agents = new Map();
        this.auctionEnabled = true;
        this.auctionQueue = [];
        this.auctionInFlightId = null;
        this.auctionRetries = new Map();
        this.waitingTasks = new Set();
        this.resultSeen = new Set();
        this.auctions = [];
        this.layoutConfig = this.transportOptions.layoutConfig || null;
        if (this.transportOptions.layout === 'logistics' || this.layoutConfig) {
            this.applyLogisticsLayout(this.layoutConfig || buildWarehouseConfig({ width, height }));
        } else {
            this.createDefaultFleet();
        }
        this.bindCommunication();
    }

    get width() {
        return this.warehouse.width;
    }

    get height() {
        return this.warehouse.height;
    }

    get obstacles() {
        return this.warehouse.obstacles;
    }

    get robots() {
        return this.warehouse.robots;
    }

    get tasks() {
        return this.warehouse.tasks;
    }

    get bounds() {
        return this.warehouse.bounds;
    }

    get stateLabel() {
        return this.running ? 'RUNNING' : 'PAUSED';
    }

    emit(message) {
        const entry = { time: new Date().toLocaleTimeString(), message };
        this.events.push(entry);
        if (this.events.length > MAX_EVENTS) this.events.shift();
        if (this.onEvent) this.onEvent(entry);
    }

    step(dt) {
        this.time += dt;
        this.bus.deliverDue(this.time);
        for (const robot of this.robots) {
            robot.update(dt, this.obstacles, this.bounds, (msg) => this.emit(msg));
        }
        for (const agent of this.agents.values()) {
            agent.tick(dt, this.time);
        }
        this.syncTasks();
        this.reconsiderWaitingTasks();
        this.advanceAuction();
    }

    bindCommunication() {
        this.bus.subscribe(TOPICS.AUCTION_RESULT, ({ payload }) => this.handleAuctionResult(payload));
    }

    spawnAgent(robot) {
        if (this.agents.has(robot.id)) this.agents.get(robot.id).destroy();
        const agent = new FleetAgent(robot, this.bus, {
            onEvent: (msg) => this.emit(msg),
            onAssign: (taskId) => this.assignTask(taskId, robot.id, { source: 'auction' }),
            getFleetSnapshot: () => this.robots.map((r) => telemetryOf(r)),
            getObstacles: () => this.obstacles,
        });
        this.agents.set(robot.id, agent);
        return agent;
    }

    announceTask(task) {
        this.bus.publish(TOPICS.TASK_NEW, {
            taskId: task.id,
            pickup: { x: task.pickup.x, y: task.pickup.y },
            dropoff: { x: task.dropoff.x, y: task.dropoff.y },
            priority: task.priority,
        }, { sender: 'server', delay: AUCTION_CONSTANTS.ANNOUNCE_DELAY });
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
                this.emit(`[AUCTION] Task ${taskId} deferred (no eligible robot)`);
                continue;
            }
            this.auctionInFlightId = taskId;
            this.emit(`[AUCTION] Task ${taskId} announced to ${this.agents.size} robot(s)`);
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

    anyEligibleRobot() {
        return this.robots.some(
            (r) => r.online && (r.currentTaskId === null || r.status === ROBOT_STATUS.COMPLETED)
        );
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
                this.emit(`[AUCTION] Task ${taskId} deferred (no eligible robot)`);
            } else {
                const retries = (this.auctionRetries.get(taskId) || 0) + 1;
                if (retries <= MAX_AUCTION_RETRIES) {
                    this.auctionRetries.set(taskId, retries);
                    this.auctionQueue.unshift(taskId);
                    this.emit(`[AUCTION] Task ${taskId} re-queued for auction (attempt ${retries})`);
                } else {
                    this.auctionRetries.delete(taskId);
                    this.waitingTasks.add(taskId);
                    this.emit(`[AUCTION] Task ${taskId} deferred (commit failed ${retries}×)`);
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

    setAuctionEnabled(enabled) {
        this.auctionEnabled = Boolean(enabled);
        if (!this.auctionEnabled) {
            this.auctionInFlightId = null;
            this.auctionQueue = [];
            this.auctionRetries.clear();
            this.waitingTasks.clear();
        }
        this.emit(`[AUCTION] auto-assign via auction ${this.auctionEnabled ? 'enabled' : 'disabled'}`);
    }

    applyLogisticsLayout(config) {
        if (!config) return;
        this.layoutConfig = config;
        this.warehouse.setLogisticsLayout(config);

        // Clear existing agents
        for (const agent of this.agents.values()) {
            agent.destroy();
        }
        this.agents.clear();
        this.warehouse.robots = [];

        // Clear tasks & auctions
        this.warehouse.tasks = [];
        this.auctionQueue = [];
        this.auctionInFlightId = null;
        this.auctionRetries.clear();
        this.waitingTasks.clear();
        this.resultSeen.clear();
        this.auctions = [];

        // Spawn robots from config
        if (config.robots && config.robots.length > 0) {
            for (let i = 0; i < config.robots.length; i++) {
                const rSpec = config.robots[i];
                const robot = new Robot(rSpec.id, rSpec.x, rSpec.y, 0, rSpec.radius || 0.4);
                robot.maxSpeed = rSpec.maxSpeed || 2.0;
                robot.color = ROBOT_COLORS[i % ROBOT_COLORS.length];
                this.robots.push(robot);
                this.spawnAgent(robot);
            }
            this.selectedRobotId = this.robots[0].id;
        } else {
            this.createDefaultFleet();
        }

        const shelfCount = config.shelves ? config.shelves.length : 0;
        const dockCount = config.deliveryZone ? config.deliveryZone.stations.length : 0;
        const padCount = config.chargingZone ? config.chargingZone.pads.length : 0;
        this.emit(`[WAREHOUSE] Logistics layout applied: ${config.width}×${config.height}m (${shelfCount} shelves, ${dockCount} delivery docks, ${padCount} charging pads, ${this.robots.length} AMRs)`);
    }

    createDefaultFleet() {
        this.addRobot('AMR1', 5, 5);
        this.addRobot('AMR2', 14, 7);
        this.addRobot('AMR3', 24, 6);
        if (!this.selectedRobotId) this.selectedRobotId = 'AMR1';
    }

    addRobot(id, x, y) {
        let robotId = id;
        if (!robotId) {
            let n = this.robots.length + 1;
            while (this.robots.some((r) => r.id === `AMR${n}`)) n += 1;
            robotId = `AMR${n}`;
        }
        let robotX = x;
        let robotY = y;
        if (robotX === undefined || robotY === undefined) {
            const p = this.warehouse.randomFreePoint(1);
            robotX = p ? p.x : 3 + Math.random() * (this.width - 6);
            robotY = p ? p.y : 3 + Math.random() * (this.height - 6);
        }
        const robot = new Robot(robotId, robotX, robotY);
        robot.x = robot.clampToBounds(robot.x, robot.radius, this.width);
        robot.y = robot.clampToBounds(robot.y, robot.radius, this.height);
        robot.color = ROBOT_COLORS[this.robots.length % ROBOT_COLORS.length];
        this.robots.push(robot);
        this.spawnAgent(robot);
        if (!this.selectedRobotId) this.selectedRobotId = robot.id;
        this.emit(`Robot ${robot.id} added at (${robot.x.toFixed(1)}, ${robot.y.toFixed(1)})`);
        return robot;
    }

    getSelectedRobot() {
        return this.robots.find((r) => r.id === this.selectedRobotId) || null;
    }

    setSelectedRobot(id) {
        this.selectedRobotId = id;
    }

    getRobotAt(x, y, radius = 0.5) {
        return this.robots.find((r) => {
            const dx = r.x - x;
            const dy = r.y - y;
            return Math.sqrt(dx * dx + dy * dy) < r.radius + radius;
        });
    }

    getObstacle(id) {
        return this.warehouse.getObstacle(id);
    }

    getObstacleAt(x, y) {
        return this.obstacles.find((o) => o.containsPoint(x, y));
    }

    setDimensions(width, height) {
        const w = Math.round(clampNum(width, MIN_DIM, MAX_DIM));
        const h = Math.round(clampNum(height, MIN_DIM, MAX_DIM));
        this.warehouse.setDimensions(w, h);
        for (const robot of this.robots) {
            robot.x = clampNum(robot.x, robot.radius, w - robot.radius);
            robot.y = clampNum(robot.y, robot.radius, h - robot.radius);
        }
        for (const task of this.tasks) {
            if (task.status !== TASK_STATUS.PENDING) continue;
            if (this.warehouse.isValidPoint(task.pickup.x, task.pickup.y, TASK_POINT_PADDING) &&
                this.warehouse.isValidPoint(task.dropoff.x, task.dropoff.y, TASK_POINT_PADDING)) {
                continue;
            }
            task.status = TASK_STATUS.CANCELLED;
            this.waitingTasks.delete(task.id);
            this.bus.publish(TOPICS.TASK_CANCELLED, { taskId: task.id }, { sender: 'server' });
            this.emit(`Task ${task.id} cancelled (outside updated warehouse)`);
        }
        this.auctionQueue = this.auctionQueue.filter((id) => {
            const t = this.tasks.find((tt) => tt.id === id);
            return t && t.status === TASK_STATUS.PENDING;
        });
        if (this.auctionInFlightId) {
            const t = this.tasks.find((tt) => tt.id === this.auctionInFlightId);
            if (!t || t.status !== TASK_STATUS.PENDING) this.auctionInFlightId = null;
        }
        this.advanceAuction();
        this.emit(`Warehouse resized to ${w}×${h}`);
    }

    addObstacle(x, y, w, h) {
        const obs = this.warehouse.addObstacle(x, y, w, h);
        this.emit(`Obstacle ${obs.id} added (${obs.width.toFixed(1)}×${obs.height.toFixed(1)}m)`);
        return obs;
    }

    removeObstacle(id) {
        this.warehouse.removeObstacle(id);
        if (this.selectedObstacleId === id) this.selectedObstacleId = null;
        this.emit(`Obstacle ${id} removed`);
    }

    moveObstacle(id, x, y) {
        this.warehouse.moveObstacle(id, x, y);
    }

    createTask(pickup, dropoff, announce = false) {
        const error = validateTask(this.warehouse, pickup, dropoff);
        if (error) {
            this.emit(error);
            return null;
        }
        const task = new Task(pickup, dropoff);
        this.tasks.push(task);
        this.emit(`Task ${task.id} created (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
        if (announce) {
            this.auctionQueue.push(task.id);
            this.advanceAuction();
        }
        return task;
    }

    generateSameDropoff(dropoff, pickupPoints) {
        if (!dropoff || !this.warehouse.isValidPoint(dropoff.x, dropoff.y, TASK_POINT_PADDING)) {
            this.emit('Same-dropoff generation failed: dropoff is outside the warehouse or inside an obstacle');
            return 0;
        }
        let created = 0;
        for (const p of pickupPoints) {
            if (this.createTask(p, dropoff, true)) created += 1;
        }
        this.emit(`Same-dropoff generation: ${created} task(s) created`);
        return created;
    }

    generateRandomTasks(count) {
        const target = Math.max(0, Math.min(count, MAX_RANDOM_TASKS));
        const isLogistics = this.warehouse.shelves && this.warehouse.shelves.length > 0 &&
                            this.warehouse.getDeliveryStations && this.warehouse.getDeliveryStations().length > 0;
        let created = 0;
        let attempts = 0;
        while (created < target && attempts < 2000) {
            attempts += 1;
            const pickup = isLogistics ? this.warehouse.getRandomPickPoint() : this.warehouse.randomFreePoint();
            const dropoff = isLogistics ? this.warehouse.getRandomDeliveryPoint() : this.warehouse.randomFreePoint();
            if (pickup && dropoff && !validateTask(this.warehouse, pickup, dropoff)) {
                const task = new Task(pickup, dropoff);
                this.tasks.push(task);
                this.emit(`Task ${task.id} created (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
                this.auctionQueue.push(task.id);
                this.advanceAuction();
                created += 1;
            }
        }
        this.emit(`Random generation: ${created} task(s) created`);
        return created;
    }

    assignTask(taskId, robotId, options = {}) {
        const task = this.tasks.find((t) => t.id === taskId);
        const robot = this.robots.find((r) => r.id === robotId);
        if (!task) {
            this.emit(`Cannot assign: task ${taskId} not found`);
            return false;
        }
        if (!robot) {
            this.emit(`Cannot assign ${task.id}: robot ${robotId} not found`);
            return false;
        }
        if (!robot.online) {
            this.emit(`Cannot assign ${task.id}: ${robot.id} is offline`);
            return false;
        }
        if (robot.currentTaskId && robot.status !== ROBOT_STATUS.COMPLETED) {
            this.emit(`Cannot assign ${task.id}: ${robot.id} is busy with ${robot.currentTaskId}`);
            return false;
        }
        if (task.status !== TASK_STATUS.PENDING) {
            this.emit(`Cannot assign ${task.id}: task is ${task.status}`);
            return false;
        }
        if (options.source === 'auction' && !this.auctionEnabled) {
            this.emit(`Cannot assign ${task.id}: auction disabled`);
            return false;
        }
        task.assignedRobotId = robot.id;
        task.status = TASK_STATUS.ASSIGNED;
        robot.startTask(task, this.obstacles, this.bounds);
        this.auctionQueue = this.auctionQueue.filter((id) => id !== task.id);
        this.auctionRetries.delete(task.id);
        this.waitingTasks.delete(task.id);
        if (this.auctionInFlightId === task.id) {
            this.auctionInFlightId = null;
            this.advanceAuction();
        }
        this.bus.publish(TOPICS.TASK_ASSIGNED, {
            taskId: task.id,
            robotId: robot.id,
            source: options.source || 'manual',
        }, { sender: options.source === 'auction' ? robot.id : 'dashboard' });
        const via = options.source === 'auction' ? ' (via auction)' : '';
        this.emit(`${robot.id} started task ${task.id}${via}`);
        return true;
    }

    cancelTask(taskId) {
        const task = this.tasks.find((t) => t.id === taskId);
        if (!task) return;
        if (task.status === TASK_STATUS.ASSIGNED ||
            task.status === TASK_STATUS.PICKING_UP ||
            task.status === TASK_STATUS.DELIVERING) {
            const robot = this.robots.find((r) => r.id === task.assignedRobotId);
            if (robot && robot.currentTaskId === task.id) robot.cancelTask();
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
        this.bus.publish(TOPICS.TASK_CANCELLED, { taskId: task.id }, { sender: 'dashboard' });
        this.emit(`Task ${task.id} cancelled`);
    }

    moveRobotTo(id, x, y) {
        const robot = this.robots.find((r) => r.id === id);
        if (!robot) return;
        if (robot.currentTaskId) {
            const task = this.tasks.find((t) => t.id === robot.currentTaskId);
            if (task && task.assignedRobotId === robot.id &&
                (task.status === TASK_STATUS.ASSIGNED ||
                 task.status === TASK_STATUS.PICKING_UP ||
                 task.status === TASK_STATUS.DELIVERING)) {
                task.status = TASK_STATUS.CANCELLED;
                task.assignedRobotId = null;
                this.emit(`Task ${task.id} cancelled (manual move of ${robot.id})`);
            }
        }
        robot.setTarget(x, y);
        this.emit(`${robot.id} target set to (${x.toFixed(2)}, ${y.toFixed(2)})`);
    }

    syncTasks() {
        for (const task of this.tasks) {
            if (!task.assignedRobotId) continue;
            const robot = this.robots.find((r) => r.id === task.assignedRobotId);
            if (!robot || robot.currentTaskId !== task.id) continue;
            if (robot.status === ROBOT_STATUS.COMPLETED) {
                if (task.status !== TASK_STATUS.COMPLETED) {
                    task.status = TASK_STATUS.COMPLETED;
                    task.completedAt = Date.now();
                }
            } else if (robot.status === ROBOT_STATUS.FAILED || !robot.online) {
                if (task.status !== TASK_STATUS.FAILED) task.status = TASK_STATUS.FAILED;
            } else if (robot.status === ROBOT_STATUS.PICKING) {
                if (task.status !== TASK_STATUS.PICKING_UP) task.status = TASK_STATUS.PICKING_UP;
            } else if (robot.status === ROBOT_STATUS.MOVING_TO_DROPOFF || robot.status === ROBOT_STATUS.DROPPING) {
                if (task.status !== TASK_STATUS.DELIVERING) task.status = TASK_STATUS.DELIVERING;
            }
        }
    }

    pause() {
        if (!this.running) return;
        this.running = false;
        this.emit('Simulation paused');
    }

    resume() {
        if (this.running) return;
        this.running = true;
        this.emit('Simulation resumed');
    }

    togglePaused() {
        if (this.running) {
            this.pause();
        } else {
            this.resume();
        }
    }

    setSpeed(speed) {
        this.speed = speed;
        this.emit(`Simulation speed set to ${speed}x`);
    }

    reset() {
        if (this.bus && typeof this.bus.dispose === 'function') this.bus.dispose();
        this.warehouse = new Warehouse(DEFAULT_WIDTH, DEFAULT_HEIGHT);
        this.selectedRobotId = null;
        this.selectedObstacleId = null;
        this.running = true;
        this.speed = 1;
        this.time = 0;
        this.bus = createTransport(() => this.time, this.transportOptions);
        this.agents = new Map();
        this.auctions = [];
        this.auctionEnabled = true;
        this.auctionQueue = [];
        this.auctionInFlightId = null;
        this.auctionRetries.clear();
        this.waitingTasks.clear();
        this.resultSeen.clear();
        if (this.layoutConfig) {
            this.applyLogisticsLayout(this.layoutConfig);
        } else {
            this.createDefaultFleet();
        }
        this.bindCommunication();
        this.emit('Simulation reset');
    }
}

export function createSimulation(width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT, transportOptions) {
    return new Simulation(width, height, transportOptions);
}