import { Obstacle } from './Obstacle.js';

const DEFAULT_POINT_PADDING = 0.5;

export class Warehouse {
    constructor(width = 30, height = 20) {
        this.width = width;
        this.height = height;
        this.obstacles = [];
        this.pickupLocations = [];
        this.dropoffLocations = [];
        this.robots = [];
        this.tasks = [];
    }

    get bounds() {
        return { width: this.width, height: this.height };
    }

    inBounds(x, y, padding = 0) {
        return x >= padding && x <= this.width - padding &&
               y >= padding && y <= this.height - padding;
    }

    isFree(x, y, padding = 0) {
        for (const obs of this.obstacles) {
            if (obs.inflatedContains(x, y, padding)) return false;
        }
        return true;
    }

    isValidPoint(x, y, padding = DEFAULT_POINT_PADDING) {
        return this.inBounds(x, y, padding) && this.isFree(x, y, padding);
    }

    addObstacle(x, y, w, h) {
        const width = Math.min(Math.max(w, 0.3), this.width);
        const height = Math.min(Math.max(h, 0.3), this.height);
        const ox = clampNum(x, 0, this.width - width);
        const oy = clampNum(y, 0, this.height - height);
        const obs = new Obstacle(ox, oy, width, height);
        this.obstacles.push(obs);
        return obs;
    }

    removeObstacle(id) {
        this.obstacles = this.obstacles.filter((o) => o.id !== id);
    }

    getObstacle(id) {
        return this.obstacles.find((o) => o.id === id);
    }

    moveObstacle(id, x, y) {
        const obs = this.getObstacle(id);
        if (!obs) return;
        obs.x = clampNum(x, 0, this.width - obs.width);
        obs.y = clampNum(y, 0, this.height - obs.height);
    }

    setDimensions(width, height) {
        this.width = width;
        this.height = height;
        for (const obs of this.obstacles) {
            obs.width = Math.min(obs.width, this.width);
            obs.height = Math.min(obs.height, this.height);
            obs.x = clampNum(obs.x, 0, this.width - obs.width);
            obs.y = clampNum(obs.y, 0, this.height - obs.height);
        }
    }

    randomFreePoint(padding = DEFAULT_POINT_PADDING) {
        for (let i = 0; i < 500; i++) {
            const x = padding + Math.random() * (this.width - 2 * padding);
            const y = padding + Math.random() * (this.height - 2 * padding);
            if (this.isFree(x, y, padding)) return { x, y };
        }
        return null;
    }
}

export function clampNum(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

export function createWarehouse(width = 30, height = 20) {
    return new Warehouse(width, height);
}