import { Robot } from './Robot.js';
import { Obstacle } from './Obstacle.js';
import { Task } from './Task.js';

export class World {
    constructor(width = 50, height = 30) {
        this.width = width;
        this.height = height;
        this.robots = [];
        this.obstacles = [];
        this.tasks = [];
        this.selectedRobotId = null;
        this.selectedObstacleId = null;
    }

    addRobot(id, x = 5 + Math.random() * 40, y = 3 + Math.random() * 24) {
        const robot = new Robot(id, x, y);
        this.robots.push(robot);
        if (!this.selectedRobotId) {
            this.selectedRobotId = robot.id;
        }
    }

    getSelectedRobot() {
        return this.robots.find(r => r.id === this.selectedRobotId) || null;
    }

    setSelectedRobot(id) {
        this.selectedRobotId = id;
    }

    addObstacle(x, y, width, height) {
        const obs = new Obstacle(x, y, width, height);
        this.obstacles.push(obs);
        return obs;
    }

    removeObstacle(id) {
        this.obstacles = this.obstacles.filter(o => o.id !== id);
    }

    getObstacle(id) {
        return this.obstacles.find(o => o.id === id);
    }

    addTask(x, y) {
        this.tasks.push(new Task(x, y));
    }

    removeTask(id) {
        this.tasks = this.tasks.filter(t => t.id !== id);
    }

    update(dt) {
        const robot = this.getSelectedRobot();
        if (robot) {
            robot.update(dt, this.obstacles);
        }
    }

    getRobotAt(x, y, radius = 0.5) {
        return this.robots.find(r => {
            const dx = r.x - x;
            const dy = r.y - y;
            return Math.sqrt(dx * dx + dy * dy) < r.radius + radius;
        });
    }

    getObstacleAt(x, y) {
        return this.obstacles.find(o => o.containsPoint(x, y));
    }
}

export function createWorld(width = 50, height = 30) {
    const world = new World(width, height);
    world.addRobot("R1", 10, 5);
    return world;
}
