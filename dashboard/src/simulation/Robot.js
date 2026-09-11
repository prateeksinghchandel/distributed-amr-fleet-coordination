export class Robot {
    constructor(id, x, y, heading = 0, radius = 0.4) {
        this.id = id;
        this.x = x;
        this.y = y;
        this.heading = heading;
        this.radius = radius;
        this.speed = 0;
        this.maxSpeed = 2.0;
        this.targetX = null;
        this.targetY = null;
        this.status = "IDLE";
        this.blocked = false;
    }

    setTarget(x, y) {
        this.targetX = x;
        this.targetY = y;
        this.status = "MOVING";
        this.blocked = false;
    }

    update(dt, obstacles = [], bounds = null) {
        if (this.status !== "MOVING" || this.targetX === null || this.targetY === null) {
            this.speed = 0;
            return;
        }

        // Keep the robot's whole footprint inside the warehouse, not just its
        // centre point.  Clamp targets as well as the next motion step so an
        // externally supplied target cannot move a robot outside the world.
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
            this.status = "IDLE";
            this.targetX = null;
            this.targetY = null;
            return;
        }

        const vx = (dx / dist) * this.maxSpeed;
        const vy = (dy / dist) * this.maxSpeed;
        const moveDist = this.maxSpeed * dt;

        if (moveDist >= dist) {
            this.x = this.targetX;
            this.y = this.targetY;
            this.speed = this.maxSpeed;
        } else {
            let newX = this.x + vx * dt;
            let newY = this.y + vy * dt;

            if (this.checkCollision(newX, newY, obstacles) || !this.isWithinBounds(newX, newY, bounds)) {
                this.blocked = true;
                this.speed = 0;
                return;
            }

            this.x = newX;
            this.y = newY;
            this.speed = this.maxSpeed;
        }

        this.heading = Math.atan2(dy, dx);
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
        // A warehouse smaller than a robot can only safely hold its centre.
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
