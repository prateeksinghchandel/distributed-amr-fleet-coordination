export const TASK_STATUS = {
    PENDING: 'PENDING',
    ASSIGNED: 'ASSIGNED',
    PICKING_UP: 'PICKING_UP',
    DELIVERING: 'DELIVERING',
    COMPLETED: 'COMPLETED',
    FAILED: 'FAILED',
    CANCELLED: 'CANCELLED',
};

export const TASK_PRIORITY = {
    NORMAL: 'NORMAL',
};

let nextTaskId = 1;

export class Task {
    constructor(pickup, dropoff, priority = TASK_PRIORITY.NORMAL) {
        this.id = `T${nextTaskId++}`;
        this.pickup = { x: pickup.x, y: pickup.y };
        this.dropoff = { x: dropoff.x, y: dropoff.y };
        this.status = TASK_STATUS.PENDING;
        this.assignedRobotId = null;
        this.priority = priority;
        this.createdAt = Date.now();
        this.completedAt = null;
    }
}

export function createTask(pickup, dropoff, priority) {
    return new Task(pickup, dropoff, priority);
}