/**
 * DistributedFleetState.js — read-only store for the distributed fleet.
 *
 * The browser dashboard does NOT run a simulation. This store subscribes to
 * the Python fleet's Zenoh topics through a ConnectionManager and mirrors a
 * read-friendly snapshot of robots, tasks, auctions and warehouse state for
 * the React components. All authority lives in the Python coordinator: we
 * apply the same task-status derivation the backend uses (sync_tasks), we
 * never fabricate telemetry, and every command issued here is a real Zenoh
 * command published on the control/* topics.
 */

import { ConnectionManager, CONNECTION_STATUS, resolveDashboardUrl } from './ConnectionManager.js';
import { TOPICS } from '../simulation/messages/topics.js';

export const TASK_STATUS = {
    PENDING: 'PENDING',
    ASSIGNED: 'ASSIGNED',
    PICKING_UP: 'PICKING_UP',
    DELIVERING: 'DELIVERING',
    COMPLETED: 'COMPLETED',
    CANCELLED: 'CANCELLED',
    FAILED: 'FAILED',
};

const ROBOT_COLORS = ['#00c8ff', '#00d084', '#ffa94d', '#f06595', '#b197fc', '#ffd43b'];
const STALE_TELEMETRY_S = 5.0;
const MAX_EVENTS = 400;
const MAX_AUCTION_HISTORY = 20;
const ROBOT_RADIUS = 0.4;

const STATUS_RANK = {
    PENDING: 1,
    ASSIGNED: 2,
    PICKING_UP: 3,
    DELIVERING: 4,
    COMPLETED: 5,
    CANCELLED: 5,
    FAILED: 5,
};

export class DistributedFleetState {
    constructor(connection = null, options = {}) {
        this.conn = connection || new ConnectionManager({
            url: options.url || resolveDashboardUrl(),
            onLog: (msg) => this.addLog(msg),
        });
        this.conn.onStatus = (info) => this._onConnectionStatus(info);
        this.conn.onLog = (msg) => this.addLog(msg);
        this.emitThrottleMs = options.emitThrottleMs ?? 100;
        this.staleTelemetryS = options.staleTelemetryS ?? STALE_TELEMETRY_S;

        this.robots = new Map();
        this.tasks = new Map();
        this.taskOrder = [];
        this.auctions = [];
        this.liveBids = new Map();
        this.warehouse = null;
        this.logs = [];
        this.lastUpdate = 0;
        this.selectedRobotId = null;
        this.connection = {
            status: this.conn.status,
            detail: this.conn.statusDetail,
            since: this.conn.connectedAt,
        };

        this._listeners = new Set();
        this._unsubs = [];
        this._emitTimer = null;
        this._dirty = false;
        this._pruneTimer = setInterval(() => this._pruneStale(), 1000);
        if (this._pruneTimer.unref) this._pruneTimer.unref();
        this._colorIndex = 0;

        this._subscribeTopics();
    }

    get robotsList() {
        return [...this.robots.values()];
    }

    get tasksList() {
        return this.taskOrder.map((id) => this.tasks.get(id)).filter(Boolean);
    }

    get isConnected() {
        return this.connection.status === CONNECTION_STATUS.CONNECTED;
    }

    // ------------------------------------------------------------------
    // Lifecycle / reactivity
    // ------------------------------------------------------------------

    connect() {
        return this.conn.connect();
    }

    disconnect() {
        return this.conn.disconnect();
    }

    retryConnection() {
        return this.conn.retryConnection();
    }

    dispose() {
        if (this._pruneTimer) clearInterval(this._pruneTimer);
        if (this._emitTimer) clearTimeout(this._emitTimer);
        for (const unsub of this._unsubs) unsub();
        this._unsubs = [];
        this._listeners.clear();
        return this.conn.dispose();
    }

    subscribe(listener) {
        this._listeners.add(listener);
        return () => this._listeners.delete(listener);
    }

    _emit() {
        if (this._emitTimer) return;
        this._emitTimer = setTimeout(() => {
            this._emitTimer = null;
            this._notify();
        }, this.emitThrottleMs);
        if (this._emitTimer.unref) this._emitTimer.unref();
    }

    _notify() {
        if (this._listeners.size === 0) return;
        for (const listener of [...this._listeners]) {
            try {
                listener(this);
            } catch (err) {
                this.addLog(`listener error: ${err && err.message ? err.message : err}`);
            }
        }
    }

    _markUpdated() {
        this.lastUpdate = Date.now();
        this._emit();
    }

    addLog(message) {
        const entry = { time: new Date().toLocaleTimeString(), message };
        this.logs.push(entry);
        if (this.logs.length > MAX_EVENTS) this.logs.splice(0, this.logs.length - MAX_EVENTS);
        this._markUpdated();
    }

    _onConnectionStatus({ status, detail }) {
        this.connection = {
            status,
            detail,
            since: status === CONNECTION_STATUS.CONNECTED ? this.conn.connectedAt : null,
        };
        const labels = {
            [CONNECTION_STATUS.DISCONNECTED]: 'Connection disconnected',
            [CONNECTION_STATUS.CONNECTING]: 'Connecting to Zenoh…',
            [CONNECTION_STATUS.CONNECTED]: `Connected to ${this.conn.url}`,
            [CONNECTION_STATUS.ERROR]: `Connection error: ${detail || 'unknown'}`,
        };
        this.addLog(labels[status] || String(status));
    }

    // ------------------------------------------------------------------
    // Subscription
    // ------------------------------------------------------------------

    _subscribeTopics() {
        const subscriptions = [
            [TOPICS.WORLD_STATE, (m) => this.onWorldState(m.payload)],
            [TOPICS.ROBOT_TELEMETRY, (m) => this.onTelemetry(m.payload)],
            [TOPICS.TASK_NEW, (m) => this.onTaskNew(m.payload)],
            [TOPICS.TASK_ASSIGNED, (m) => this.onTaskAssigned(m.payload)],
            [TOPICS.TASK_CANCELLED, (m) => this.onTaskCancelled(m.payload)],
            [TOPICS.BID_PLACED, (m) => this.onBidPlaced(m.payload)],
            [TOPICS.AUCTION_RESULT, (m) => this.onAuctionResult(m.payload)],
        ];
        for (const [topic, handler] of subscriptions) {
            try {
                this._unsubs.push(this.conn.subscribe(topic, handler));
            } catch (err) {
                this.addLog(`subscribe failed for ${topic}: ${err && err.message ? err.message : err}`);
            }
        }
    }

    // ------------------------------------------------------------------
    // Inbound handlers (Python backend payload schemas)
    // ------------------------------------------------------------------

    onWorldState(payload) {
        if (!payload || typeof payload.width !== 'number' || typeof payload.height !== 'number') return;
        payload = {
            width: payload.width,
            height: payload.height,
            obstacles: payload.obstacles || [],
            chargingPads: payload.chargingPads || [],
            deliveryDocks: payload.deliveryDocks || [],
            roster: payload.roster || [],
        };
        this.warehouse = this._buildWarehouse(payload);
        this._applyRosterColors(payload.roster);
        this.addLog(`World state: ${payload.width}×${payload.height}m, ` +
            `${payload.roster.length} robot(s), ${payload.obstacles.length} obstacle(s)`);
        this._markUpdated();
    }

    onTelemetry(payload) {
        if (!payload || typeof payload.robotId !== 'string' || typeof payload.x !== 'number') return;
        const robotId = payload.robotId;
        const robot = this.robots.get(robotId);
        const fresh = this._buildRobot(payload, robot);
        this.robots.set(robotId, fresh);
        if (robot && !robot.online && fresh.online) {
            this.addLog(`${robotId} back online`);
        }
        if (this.selectedRobotId === null && this.robots.size > 0) {
            this.selectedRobotId = this.robotsList[0].id;
        }
        this._syncTaskStatuses();
        if (!robot || robot.status !== fresh.status || robot.currentTaskId !== fresh.currentTaskId) {
            this.addLog(`${robotId}: ${fresh.status}${fresh.currentTaskId ? ` (${fresh.currentTaskId})` : ''}`);
        }
        this._markUpdated();
    }

    onTaskNew(payload) {
        if (!payload || typeof payload.taskId !== 'string') return;
        const existing = this.tasks.get(payload.taskId);
        const task = {
            id: payload.taskId,
            taskId: payload.taskId,
            pickup: {
                x: payload.pickup && typeof payload.pickup.x === 'number' ? payload.pickup.x : 0,
                y: payload.pickup && typeof payload.pickup.y === 'number' ? payload.pickup.y : 0,
            },
            dropoff: {
                x: payload.dropoff && typeof payload.dropoff.x === 'number' ? payload.dropoff.x : 0,
                y: payload.dropoff && typeof payload.dropoff.y === 'number' ? payload.dropoff.y : 0,
            },
            priority: typeof payload.priority === 'number' ? payload.priority : 1,
            status: existing ? existing.status : TASK_STATUS.PENDING,
            assignedRobotId: existing ? existing.assignedRobotId : null,
            createdAt: existing ? existing.createdAt : Date.now(),
            updatedAt: Date.now(),
            completedAt: existing ? existing.completedAt : null,
        };
        if (!existing) {
            this.tasks.set(payload.taskId, task);
            this.taskOrder.push(payload.taskId);
            this.addLog(`Task ${payload.taskId} announced ` +
                `(${task.pickup.x.toFixed(1)}, ${task.pickup.y.toFixed(1)}) → ` +
                `(${task.dropoff.x.toFixed(1)}, ${task.dropoff.y.toFixed(1)})`);
        } else {
            this.tasks.set(payload.taskId, task);
        }
        this._markUpdated();
    }

    onTaskAssigned(payload) {
        if (!payload || typeof payload.taskId !== 'string' || typeof payload.robotId !== 'string') return;
        const task = this.tasks.get(payload.taskId);
        if (!task) return;
        this._setTaskStatus(task, TASK_STATUS.ASSIGNED);
        task.assignedRobotId = payload.robotId;
        task.updatedAt = Date.now();
        this.addLog(`${payload.robotId} started task ${payload.taskId}` +
            (payload.source === 'manual' ? ' (manual)' : ' (via auction)'));
        this._markUpdated();
    }

    onTaskCancelled(payload) {
        if (!payload || typeof payload.taskId !== 'string') return;
        const task = this.tasks.get(payload.taskId);
        if (!task) return;
        if (task.status === TASK_STATUS.COMPLETED || task.status === TASK_STATUS.FAILED) return;
        task.status = TASK_STATUS.CANCELLED;
        task.assignedRobotId = null;
        task.updatedAt = Date.now();
        this.addLog(`Task ${payload.taskId} cancelled`);
        this._markUpdated();
    }

    onBidPlaced(payload) {
        if (!payload || typeof payload.taskId !== 'string' || typeof payload.robotId !== 'string') return;
        let taskBids = this.liveBids.get(payload.taskId);
        if (!taskBids) {
            taskBids = new Map();
            this.liveBids.set(payload.taskId, taskBids);
        }
        taskBids.set(payload.robotId, {
            robotId: payload.robotId,
            bid: typeof payload.bid === 'number' ? payload.bid : null,
            costs: payload.costs || null,
            reason: payload.reason || null,
        });
        this._markUpdated();
    }

    onAuctionResult(payload) {
        if (!payload || typeof payload.taskId !== 'string') return;
        const taskId = payload.taskId;
        const winner = payload.winner || null;
        const committed = Boolean(payload.committed);
        const bids = Array.isArray(payload.bids) ? payload.bids : [];
        this.liveBids.delete(taskId);
        this.auctions.unshift({
            taskId,
            winner,
            bids: bids.map((b) => ({
                robotId: b.robotId,
                bid: typeof b.bid === 'number' ? b.bid : null,
                costs: b.costs || null,
                reason: b.reason || null,
            })),
            committed,
            reason: winner ? (committed ? null : 'commit failed') : 'no eligible robot',
            time: new Date().toLocaleTimeString(),
        });
        if (this.auctions.length > MAX_AUCTION_HISTORY) this.auctions.pop();
        this.addLog(`[AUCTION] Task ${taskId} → ${winner || 'no winner'}${committed ? '' : ' (not committed)'}`);
        this._markUpdated();
    }

    // ------------------------------------------------------------------
    // Status derivation (mirrors backend task_manager.sync_tasks)
    // ------------------------------------------------------------------

    _setTaskStatus(task, status) {
        if (STATUS_RANK[status] >= STATUS_RANK[task.status]) task.status = status;
        if (status === TASK_STATUS.COMPLETED) task.completedAt = Date.now();
        task.updatedAt = Date.now();
    }

    _syncTaskStatuses() {
        let changed = false;
        for (const task of this.tasksList) {
            if (!task.assignedRobotId) continue;
            const robot = this.robots.get(task.assignedRobotId);
            if (!robot || robot.currentTaskId !== task.id) continue;
            if (robot.status === 'COMPLETED') {
                if (task.status !== TASK_STATUS.COMPLETED) {
                    task.status = TASK_STATUS.COMPLETED;
                    task.completedAt = Date.now();
                    task.updatedAt = Date.now();
                    changed = true;
                }
            } else if (robot.status === 'FAILED' || !robot.online) {
                if (task.status !== TASK_STATUS.FAILED) {
                    task.status = TASK_STATUS.FAILED;
                    task.updatedAt = Date.now();
                    changed = true;
                }
            } else if (robot.status === 'PICKING') {
                if (task.status !== TASK_STATUS.PICKING_UP) {
                    task.status = TASK_STATUS.PICKING_UP;
                    task.updatedAt = Date.now();
                    changed = true;
                }
            } else if (robot.status === 'MOVING_TO_DROPOFF' || robot.status === 'DROPPING') {
                if (task.status !== TASK_STATUS.DELIVERING) {
                    task.status = TASK_STATUS.DELIVERING;
                    task.updatedAt = Date.now();
                    changed = true;
                }
            }
        }
        return changed;
    }

    // ------------------------------------------------------------------
    // Derived views
    // ------------------------------------------------------------------

    _buildRobot(payload, existing = null) {
        const rosterIndex = this._rosterIndex(payload.robotId);
        return {
            id: payload.robotId,
            robotId: payload.robotId,
            x: payload.x,
            y: payload.y,
            heading: typeof payload.heading === 'number' ? payload.heading : 0,
            status: payload.status || 'IDLE',
            battery: typeof payload.battery === 'number' ? payload.battery : 100,
            currentTaskId: payload.currentTaskId || null,
            blocked: Boolean(payload.blocked),
            online: payload.online !== false,
            color: rosterIndex >= 0 ? ROBOT_COLORS[rosterIndex % ROBOT_COLORS.length] : this._colorFor(existing),
            radius: ROBOT_RADIUS,
            lastTelemetryAt: Date.now(),
        };
    }

    _rosterIndex(robotId) {
        if (this.warehouse && this.warehouse.roster) {
            const idx = this.warehouse.roster.findIndex((r) => r.id === robotId);
            if (idx >= 0) return idx;
        }
        return -1;
    }

    _colorFor(existing) {
        if (existing && existing.color) return existing.color;
        const color = ROBOT_COLORS[this._colorIndex % ROBOT_COLORS.length];
        this._colorIndex += 1;
        return color;
    }

    _applyRosterColors(roster) {
        if (!Array.isArray(roster)) return;
        for (let i = 0; i < roster.length; i += 1) {
            const robot = this.robots.get(roster[i].id);
            if (robot) robot.color = ROBOT_COLORS[i % ROBOT_COLORS.length];
        }
    }

    _buildWarehouse(payload) {
        const deliveryDocks = payload.deliveryDocks || [];
        const chargingPads = payload.chargingPads || [];
        const shelves = (payload.obstacles || []).filter((o) => o.type === 'shelf');
        return {
            width: payload.width,
            height: payload.height,
            roster: payload.roster || [],
            obstacles: payload.obstacles || [],
            chargingPads,
            deliveryDocks,
            shelves: shelves.map((s) => ({ ...s, pickPoints: [] })),
            deliveryZone: this._zoneFromRects(deliveryDocks, 'stations'),
            chargingZone: this._zoneFromRects(chargingPads, 'pads'),
        };
    }

    _zoneFromRects(items, key) {
        if (!items || items.length === 0) return null;
        const xs = items.map((i) => i.x);
        const ys = items.map((i) => i.y);
        const xe = items.map((i) => i.x + (i.width || 0));
        const ye = items.map((i) => i.y + (i.height || 0));
        const zone = {
            x: Math.min(...xs),
            y: Math.min(...ys),
            width: Math.max(...xe) - Math.min(...xs),
            height: Math.max(...ye) - Math.min(...ys),
            [key]: items.map((i) => ({ id: i.id, x: i.x, y: i.y, width: i.width, height: i.height })),
        };
        return zone;
    }

    _pruneStale() {
        const now = Date.now();
        let changed = false;
        for (const robot of this.robots.values()) {
            if (robot.online && now - robot.lastTelemetryAt > this.staleTelemetryS * 1000) {
                robot.online = false;
                changed = true;
            }
        }
        if (changed) {
            this._syncTaskStatuses();
            this._markUpdated();
        }
    }

    // ------------------------------------------------------------------
    // Selection (local UI state only — no backend involvement)
    // ------------------------------------------------------------------

    getSelectedRobot() {
        return this.robots.get(this.selectedRobotId) || null;
    }

    setSelectedRobot(id) {
        this.selectedRobotId = id;
        this._markUpdated();
    }

    selectRobotAt(x, y, radius = 0.5) {
        for (const robot of this.robots.values()) {
            const dx = robot.x - x;
            const dy = robot.y - y;
            if (Math.sqrt(dx * dx + dy * dy) < robot.radius + radius) {
                this.setSelectedRobot(robot.id);
                return robot;
            }
        }
        return null;
    }

    // ------------------------------------------------------------------
    // Commands — real Zenoh control/* publishes to the Python coordinator
    // ------------------------------------------------------------------

    createTask(pickup, dropoff, priority = 1) {
        if (!this.isConnected) {
            this.addLog('Command rejected: not connected');
            return false;
        }
        const ok = this.conn.publish(TOPICS.CONTROL_TASK_CREATE, {
            pickup: { x: pickup.x, y: pickup.y },
            dropoff: { x: dropoff.x, y: dropoff.y },
            priority,
        });
        if (ok) this.addLog(`Requested creation of task (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → ` +
            `(${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
        return ok;
    }

    generateRandomTasks(count) {
        if (!this.isConnected) {
            this.addLog('Command rejected: not connected');
            return false;
        }
        const n = Math.max(0, Math.min(Math.trunc(Number(count)) || 0, 100));
        const ok = this.conn.publish(TOPICS.CONTROL_TASK_CREATE, { randomCount: n });
        if (ok) this.addLog(`Requested generation of ${n} random task(s)`);
        return ok;
    }

    assignTask(taskId, robotId) {
        if (!this.isConnected) {
            this.addLog('Command rejected: not connected');
            return false;
        }
        const ok = this.conn.publish(TOPICS.CONTROL_TASK_ASSIGN, { taskId, robotId });
        if (ok) this.addLog(`Requested manual assignment of ${taskId} → ${robotId}`);
        return ok;
    }

    cancelTask(taskId) {
        if (!this.isConnected) {
            this.addLog('Command rejected: not connected');
            return false;
        }
        const task = this.tasks.get(taskId);
        if (task && (task.status === TASK_STATUS.COMPLETED || task.status === TASK_STATUS.CANCELLED ||
                     task.status === TASK_STATUS.FAILED)) {
            this.addLog(`Cannot cancel ${taskId}: task is ${task.status}`);
            return false;
        }
        const ok = this.conn.publish(TOPICS.CONTROL_TASK_CANCEL, { taskId });
        if (ok) this.addLog(`Requested cancellation of task ${taskId}`);
        return ok;
    }

    // ------------------------------------------------------------------
    // Renderer scene (pure derived snapshot — mirrors warehouse view)
    // ------------------------------------------------------------------

    getScene() {
        return {
            warehouse: this.warehouse || {
                width: 0,
                height: 0,
                obstacles: [],
                shelves: [],
                deliveryZone: null,
                chargingZone: null,
            },
            obstacles: this.warehouse ? this.warehouse.obstacles : [],
            tasks: this.tasksList,
            robots: this.robotsList,
            selectedRobotId: this.selectedRobotId,
            selectedRobot: this.getSelectedRobot(),
            warehouseExists: Boolean(this.warehouse),
        };
    }
}

export function createDistributedFleetState(options = {}) {
    const state = new DistributedFleetState(null, options);
    state.connect();
    return state;
}