let nextId = 1;

export class Obstacle {
    constructor(x, y, width, height) {
        this.id = `O${nextId++}`;
        this.x = x;
        this.y = y;
        this.width = width;
        this.height = height;
    }

    containsPoint(px, py) {
        return px >= this.x && px <= this.x + this.width &&
               py >= this.y && py <= this.y + this.height;
    }

    inflatedContains(px, py, padding) {
        return px >= this.x - padding && px <= this.x + this.width + padding &&
               py >= this.y - padding && py <= this.y + this.height + padding;
    }
}

export function createObstacle(x, y, width, height) {
    return new Obstacle(x, y, width, height);
}