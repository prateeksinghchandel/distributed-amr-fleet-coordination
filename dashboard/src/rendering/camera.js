export class Camera {
    constructor() {
        this.x = 0;
        this.y = 0;
        this.zoom = 1;
    }

    worldToScreen(worldX, worldY, canvasWidth, canvasHeight) {
        const screenX = (worldX - this.x) * this.zoom + canvasWidth / 2;
        const screenY = (worldY - this.y) * this.zoom + canvasHeight / 2;
        return { x: screenX, y: screenY };
    }

    screenToWorld(screenX, screenY, canvasWidth, canvasHeight) {
        const worldX = (screenX - canvasWidth / 2) / this.zoom + this.x;
        const worldY = (screenY - canvasHeight / 2) / this.zoom + this.y;
        return { x: worldX, y: worldY };
    }

    zoomAtPoint(delta, screenX, screenY, canvasWidth, canvasHeight) {
        const worldPos = this.screenToWorld(screenX, screenY, canvasWidth, canvasHeight);

        this.zoom = Math.max(0.4, Math.min(20, this.zoom * (1 + delta)));

        const newWorldPos = this.screenToWorld(screenX, screenY, canvasWidth, canvasHeight);
        this.x += worldPos.x - newWorldPos.x;
        this.y += worldPos.y - newWorldPos.y;
    }

    reset() {
        this.x = 0;
        this.y = 0;
        this.zoom = 1;
    }
}
