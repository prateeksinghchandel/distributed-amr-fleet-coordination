import { Warehouse, clampNum } from './Warehouse.js';
import { Robot, ROBOT_STATUS } from './Robot.js';
import { Task, TASK_STATUS } from './Task.js';
import { validateTask, TASK_POINT_PADDING } from './TaskGenerator.js';

export const DEFAULT_WIDTH = 30;
export const DEFAULT_HEIGHT = 20;
export const MIN_DIM = 4;
export const MAX_DIM = 100;
export const MAX_RANDOM_TASKS = 100;

const ROBOT_COLORS = ['#00c8ff', '#00d084', '#ffa94d', '#f06595', '#b197fc', '#ffd43b'];
const MAX_EVENTS = 500;

export class Simulation {
    constructor(width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT) {
        this.warehouse = new Warehouse(width, height);
        this.selectedRobotId = null;
        this.selectedObstacleId = null;
        this.running = true;
        this.speed = 1;
        this.time = 0;
        this.events = [];
        this.onEvent = null;
        this.createDefaultFleet();
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
        for (const robot of this.robots) {
            robot.update(dt, this.obstacles, this.bounds, (msg) => this.emit(msg));
        }
        this.syncTasks();
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
            this.emit(`Task ${task.id} cancelled (outside updated warehouse)`);
        }
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

    createTask(pickup, dropoff) {
        const error = validateTask(this.warehouse, pickup, dropoff);
        if (error) {
            this.emit(error);
            return null;
        }
        const task = new Task(pickup, dropoff);
        this.tasks.push(task);
        this.emit(`Task ${task.id} created (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
        return task;
    }

    generateSameDropoff(dropoff, pickupPoints) {
        if (!dropoff || !this.warehouse.isValidPoint(dropoff.x, dropoff.y, TASK_POINT_PADDING)) {
            this.emit('Same-dropoff generation failed: dropoff is outside the warehouse or inside an obstacle');
            return 0;
        }
        let created = 0;
        for (const p of pickupPoints) {
            if (this.createTask(p, dropoff)) created += 1;
        }
        this.emit(`Same-dropoff generation: ${created} task(s) created`);
        return created;
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
                this.emit(`Task ${task.id} created (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}) → (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)})`);
                created += 1;
            }
        }
        this.emit(`Random generation: ${created} task(s) created`);
        return created;
    }

    assignTask(taskId, robotId) {
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
        task.assignedRobotId = robot.id;
        task.status = TASK_STATUS.ASSIGNED;
        robot.startTask(task);
        this.emit(`${robot.id} started task ${task.id}`);
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
        this.warehouse = new Warehouse(DEFAULT_WIDTH, DEFAULT_HEIGHT);
        this.selectedRobotId = null;
        this.selectedObstacleId = null;
        this.running = true;
        this.speed = 1;
        this.time = 0;
        this.createDefaultFleet();
        this.emit('Simulation reset');
    }
}

export function createSimulation(width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT) {
    return new Simulation(width, height);
}