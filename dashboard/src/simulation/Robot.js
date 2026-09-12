export const ROBOT_STATUS = {
    IDLE: 'IDLE',
    MOVING: 'MOVING',
    MOVING_TO_PICKUP: 'MOVING_TO_PICKUP',
    PICKING: 'PICKING',
    MOVING_TO_DROPOFF: 'MOVING_TO_DROPOFF',
    DROPPING: 'DROPPING',
    COMPLETED: 'COMPLETED',
    FAILED: 'FAILED',
};

const TASK_PHASE_SECONDS = 0.8;
const BATTERY_DRAIN_PER_SECOND = 0.3;

export class Robot {
    constructor(id, x, y, heading = 0, radius = 0.4) {
        this.id = id;
        this.x = x;
        this.y = y;
        this.heading = heading;
        this.radius = radius;
        this.speed = 0;
        this.maxSpeed = 2.0;
        this.velocity = { x: 0, y: 0 };
        this.targetX = null;
        this.targetY = null;
        this.status = ROBOT_STATUS.IDLE;
        this.blocked = false;
        this.online = true;
        this.battery = 100;
        this.color = '#0066cc';

        this.currentTaskId = null;
        this.currentPath = [];
        this.currentWaypointIndex = -1;

        this.taskStage = null;
        this.taskPickup = null;
        this.taskDropoff = null;
        this.phaseTimer = 0;
    }

    setStatus(status) {
        this.status = status;
    }

    setTarget(x, y) {
        this.clearTaskState();
        this.targetX = x;
        this.targetY = y;
        this.status = ROBOT_STATUS.MOVING;
        this.blocked = false;
    }

    startTask(task) {
        this.currentTaskId = task.id;
        this.taskPickup = { x: task.pickup.x, y: task.pickup.y };
        this.taskDropoff = { x: task.dropoff.x, y: task.dropoff.y };
        this.taskStage = 'toPickup';
        this.phaseTimer = 0;
        this.targetX = this.taskPickup.x;
        this.targetY = this.taskPickup.y;
        this.status = ROBOT_STATUS.MOVING_TO_PICKUP;
        this.blocked = false;
    }

    cancelTask() {
        this.clearTaskState();
    }

    clearTaskState() {
        this.currentTaskId = null;
        this.taskStage = null;
        this.taskPickup = null;
        this.taskDropoff = null;
        this.targetX = null;
        this.targetY = null;
        this.status = ROBOT_STATUS.IDLE;
        this.blocked = false;
    }

    update(dt, obstacles, bounds, emit) {
        this.drainBattery(dt, emit);

        if (!this.online) {
            this.speed = 0;
            this.velocity = { x: 0, y: 0 };
            return;
        }

        if (this.taskStage === 'picking' || this.taskStage === 'dropping') {
            this.speed = 0;
            this.velocity = { x: 0, y: 0 };
            this.phaseTimer -= dt;
            if (this.phaseTimer <= 0) {
                if (this.taskStage === 'picking') {
                    this.beginDropoff();
                } else {
                    this.finishTask(emit);
                }
            }
            return;
        }

        const hasTarget =
            (this.taskStage === 'toPickup' || this.taskStage === 'toDropoff') ||
            (this.taskStage === null && this.status === ROBOT_STATUS.MOVING);
        if (!hasTarget || this.targetX === null || this.targetY === null) {
            this.speed = 0;
            this.velocity = { x: 0, y: 0 };
            return;
        }

        const arrived = this.moveToward(dt, obstacles, bounds);
        if (arrived) {
            if (this.taskStage === 'toPickup') {
                this.beginPicking(emit);
            } else if (this.taskStage === 'toDropoff') {
                this.beginDropping(emit);
            } else {
                this.targetX = null;
                this.targetY = null;
                this.status = ROBOT_STATUS.IDLE;
            }
        }
    }

    moveToward(dt, obstacles, bounds) {
        if (bounds) {
            this.targetX = this.clampToBounds(this.targetX, this.radius, bounds.width);
            this.targetY = this.clampToBounds(this.targetY, this.radius, bounds.height);
            this.x = this.clampToBounds(this.x, this.radius, bounds.width);
            this.y = this.clampToBounds(this.y, this.radius, bounds.height);
        }

        const dx = this.targetX - this.x;
        const dy = this.targetY - this.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < 0.05) {
            this.x = this.targetX;
            this.y = this.targetY;
            this.speed = 0;
            this.velocity = { x: 0, y: 0 };
            return true;
        }

        const dirX = dx / dist;
        const dirY = dy / dist;
        const step = this.maxSpeed * dt;

        let newX = this.x + dirX * step;
        let newY = this.y + dirY * step;
        if (step >= dist) {
            newX = this.targetX;
            newY = this.targetY;
        }

        if (this.checkCollision(newX, newY, obstacles) || !this.isWithinBounds(newX, newY, bounds)) {
            this.blocked = true;
            this.speed = 0;
            this.velocity = { x: 0, y: 0 };
            return false;
        }

        this.x = newX;
        this.y = newY;
        this.speed = this.maxSpeed;
        this.velocity = { x: dirX * this.speed, y: dirY * this.speed };
        this.heading = Math.atan2(dy, dx);
        return false;
    }

    beginPicking(emit) {
        this.taskStage = 'picking';
        this.phaseTimer = TASK_PHASE_SECONDS;
        this.targetX = null;
        this.targetY = null;
        this.status = ROBOT_STATUS.PICKING;
        if (emit) emit(`Robot ${this.id} reached pickup (task ${this.currentTaskId})`);
    }

    beginDropoff() {
        this.taskStage = 'toDropoff';
        this.phaseTimer = 0;
        this.targetX = this.taskDropoff.x;
        this.targetY = this.taskDropoff.y;
        this.status = ROBOT_STATUS.MOVING_TO_DROPOFF;
        this.blocked = false;
    }

    beginDropping(emit) {
        this.taskStage = 'dropping';
        this.phaseTimer = TASK_PHASE_SECONDS;
        this.targetX = null;
        this.targetY = null;
        this.status = ROBOT_STATUS.DROPPING;
        if (emit) emit(`Robot ${this.id} reached dropoff (task ${this.currentTaskId})`);
    }

    finishTask(emit) {
        this.taskStage = null;
        this.targetX = null;
        this.targetY = null;
        this.status = ROBOT_STATUS.COMPLETED;
        if (emit) emit(`Robot ${this.id} completed task ${this.currentTaskId}`);
    }

    drainBattery(dt, emit) {
        if (!this.online) return;
        if (this.speed > 0.01) {
            this.battery = Math.max(0, this.battery - BATTERY_DRAIN_PER_SECOND * dt);
        }
        if (this.battery <= 0) {
            this.online = false;
            this.status = ROBOT_STATUS.FAILED;
            this.targetX = null;
            this.targetY = null;
            this.blocked = false;
            if (emit) emit(`Robot ${this.id} failed (battery depleted)`);
        }
    }

    checkCollision(newX, newY, obstacles) {
        for (const obs of obstacles) {
            const closestX = Math.max(obs.x, Math.min(newX, obs.x + obs.width));
            const closestY = Math.max(obs.y, Math.min(newY, obs.y + obs.height));
            const dx = newX - closestX;
            const dy = newY - closestY;
            if (dx * dx + dy * dy < this.radius * this.radius) {
                return true;
            }
        }
        return false;
    }

    clampToBounds(value, radius, size) {
        const min = Math.min(radius, size / 2);
        const max = Math.max(min, size - radius);
        return Math.max(min, Math.min(value, max));
    }

    isWithinBounds(x, y, bounds) {
        return !bounds || (
            x - this.radius >= 0 &&
            x + this.radius <= bounds.width &&
            y - this.radius >= 0 &&
            y + this.radius <= bounds.height
        );
    }
}

export function createRobot(id, x, y) {
    return new Robot(id, x, y);
}