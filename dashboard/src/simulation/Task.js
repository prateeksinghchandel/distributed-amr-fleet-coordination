let nextId = 1;

export class Task {
    constructor(x, y) {
        this.id = `T${nextId++}`;
        this.x = x;
        this.y = y;
        this.status = "pending";
    }
}

export function createTask(x, y) {
    return new Task(x, y);
}
