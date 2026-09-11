import { Camera } from './camera.js';
import { formatWorldCoord } from './coordinates.js';

export class Renderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.camera = new Camera();
    }

    resize() {
        const container = this.canvas.parentElement;
        this.canvas.width = container.clientWidth;
        this.canvas.height = container.clientHeight;
    }

    render(world, mouseWorldX, mouseWorldY, obstacleDrag) {
        const { ctx, canvas } = this;
        const w = canvas.width;
        const h = canvas.height;

        ctx.clearRect(0, 0, w, h);

        this.drawBackground(w, h);
        this.drawGrid(world, w, h);
        this.drawBoundary(world);
        this.drawObstacles(world);
        this.drawTasks(world);
        this.drawRobotPaths(world);
        this.drawRobots(world);
        this.drawTargetIndicator(world);
        if (obstacleDrag) this.drawObstaclePreview(obstacleDrag);
        this.drawMouseCoords(mouseWorldX, mouseWorldY);
    }

    drawBackground(w, h) {
        const { ctx } = this;
        ctx.fillStyle = '#1a1a2e';
        ctx.fillRect(0, 0, w, h);
    }

    drawGrid(world, w, h) {
        const { ctx, camera } = this;
        const spacing = camera.getGridSpacing();
        const halfW = w / 2 / camera.zoom;
        const halfH = h / 2 / camera.zoom;

        const startX = Math.floor((camera.x - halfW) / spacing) * spacing;
        const endX = Math.ceil((camera.x + halfW) / spacing) * spacing;
        const startY = Math.floor((camera.y - halfH) / spacing) * spacing;
        const endY = Math.ceil((camera.y + halfH) / spacing) * spacing;

        ctx.strokeStyle = '#2a2a4a';
        ctx.lineWidth = 0.5;

        for (let x = startX; x <= endX; x += spacing) {
            const screen = camera.worldToScreen(x, 0, w, h);
            ctx.beginPath();
            ctx.moveTo(screen.x, 0);
            ctx.lineTo(screen.x, h);
            ctx.stroke();
        }

        for (let y = startY; y <= endY; y += spacing) {
            const screen = camera.worldToScreen(0, y, w, h);
            ctx.beginPath();
            ctx.moveTo(0, screen.y);
            ctx.lineTo(w, screen.y);
            ctx.stroke();
        }

        ctx.fillStyle = '#4a4a6a';
        ctx.font = `${Math.max(10, Math.min(14, 12 * camera.zoom))}px monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';

        for (let x = startX; x <= endX; x += spacing) {
            if (Math.abs(x) < spacing * 0.01) continue;
            const screen = camera.worldToScreen(x, 0, w, h);
            ctx.fillText(`${formatWorldCoord(x)}m`, screen.x, 2);
        }

        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        for (let y = startY; y <= endY; y += spacing) {
            if (Math.abs(y) < spacing * 0.01) continue;
            const screen = camera.worldToScreen(0, y, w, h);
            ctx.fillText(`${formatWorldCoord(y)}m`, w - 4, screen.y);
        }
    }

    drawBoundary(world) {
        const { ctx, camera, canvas } = this;
        const bl = camera.worldToScreen(0, 0, canvas.width, canvas.height);
        const tr = camera.worldToScreen(world.width, world.height, canvas.width, canvas.height);

        ctx.strokeStyle = '#e94560';
        ctx.lineWidth = 2;
        ctx.strokeRect(bl.x, bl.y, tr.x - bl.x, tr.y - bl.y);

        ctx.fillStyle = '#e94560';
        ctx.font = '12px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(`(${formatWorldCoord(0)}, ${formatWorldCoord(0)})`, bl.x + 4, bl.y - 2);
        ctx.textAlign = 'right';
        ctx.fillText(`(${formatWorldCoord(world.width)}, ${formatWorldCoord(world.height)})`, tr.x - 4, tr.y + 12);
    }

    drawObstacles(world) {
        const { ctx, camera, canvas } = this;
        for (const obs of world.obstacles) {
            const bl = camera.worldToScreen(obs.x, obs.y, canvas.width, canvas.height);
            const tr = camera.worldToScreen(obs.x + obs.width, obs.y + obs.height, canvas.width, canvas.height);
            const w = tr.x - bl.x;
            const h = tr.y - bl.y;

            ctx.fillStyle = '#555577';
            ctx.fillRect(bl.x, bl.y, w, h);
            ctx.strokeStyle = '#e94560';
            ctx.lineWidth = 1.5;
            ctx.strokeRect(bl.x, bl.y, w, h);

            ctx.fillStyle = '#aaaacc';
            ctx.font = '10px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(obs.id, bl.x + w / 2, bl.y + h / 2);
        }
    }

    drawTasks(world) {
        const { ctx, camera, canvas } = this;
        for (const task of world.tasks) {
            const screen = camera.worldToScreen(task.x, task.y, canvas.width, canvas.height);

            ctx.beginPath();
            ctx.arc(screen.x, screen.y, 6, 0, Math.PI * 2);
            ctx.fillStyle = '#f5a623';
            ctx.fill();
            ctx.strokeStyle = '#ff6b35';
            ctx.lineWidth = 2;
            ctx.stroke();

            ctx.beginPath();
            ctx.arc(screen.x, screen.y, 3, 0, Math.PI * 2);
            ctx.fillStyle = '#fff3cd';
            ctx.fill();

            ctx.fillStyle = '#f5a623';
            ctx.font = '10px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(task.id, screen.x, screen.y - 10);
        }
    }

    drawRobotPaths(world) {
        const { ctx, camera, canvas } = this;
        for (const robot of world.robots) {
            if (robot.targetX !== null && robot.targetY !== null) {
                const start = camera.worldToScreen(robot.x, robot.y, canvas.width, canvas.height);
                const end = camera.worldToScreen(robot.targetX, robot.targetY, canvas.width, canvas.height);

                ctx.beginPath();
                ctx.moveTo(start.x, start.y);
                ctx.lineTo(end.x, end.y);
                ctx.strokeStyle = 'rgba(0, 200, 255, 0.3)';
                ctx.lineWidth = 1;
                ctx.setLineDash([5, 5]);
                ctx.stroke();
                ctx.setLineDash([]);
            }
        }
    }

    drawRobots(world) {
        const { ctx, camera, canvas } = this;
        for (const robot of world.robots) {
            const screen = camera.worldToScreen(robot.x, robot.y, canvas.width, canvas.height);
            const r = robot.radius * camera.zoom;
            const isSelected = world.selectedRobotId === robot.id;

            ctx.beginPath();
            ctx.arc(screen.x, screen.y, Math.max(4, r), 0, Math.PI * 2);
            ctx.fillStyle = isSelected ? '#00c8ff' : '#0066cc';
            ctx.fill();
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = isSelected ? 2 : 1;
            ctx.stroke();

            const dirX = Math.cos(robot.heading) * r;
            const dirY = Math.sin(robot.heading) * r;
            ctx.beginPath();
            ctx.moveTo(screen.x, screen.y);
            ctx.lineTo(screen.x + dirX, screen.y + dirY);
            ctx.strokeStyle = '#00ff88';
            ctx.lineWidth = 2.5;
            ctx.stroke();

            ctx.fillStyle = '#fff';
            ctx.font = `bold ${Math.max(10, Math.min(14, 12 * camera.zoom))}px monospace`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(robot.id, screen.x, screen.y - r - 4);
        }
    }

    drawTargetIndicator(world) {
        const { ctx, camera, canvas } = this;
        const robot = world.getSelectedRobot();
        if (!robot || robot.targetX === null) return;

        const screen = camera.worldToScreen(robot.targetX, robot.targetY, canvas.width, canvas.height);

        ctx.beginPath();
        ctx.arc(screen.x, screen.y, 8, 0, Math.PI * 2);
        ctx.strokeStyle = '#00ff88';
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.beginPath();
        ctx.moveTo(screen.x - 12, screen.y);
        ctx.lineTo(screen.x + 12, screen.y);
        ctx.moveTo(screen.x, screen.y - 12);
        ctx.lineTo(screen.x, screen.y + 12);
        ctx.strokeStyle = '#00ff88';
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    drawMouseCoords(worldX, worldY) {
        if (worldX === null || worldY === null) return;
        const { ctx } = this;
        ctx.fillStyle = '#8888aa';
        ctx.font = '11px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(`World: X ${worldX.toFixed(2)}m  Y ${worldY.toFixed(2)}m`, 8, 8);
    }

    drawObstaclePreview(drag) {
        const { ctx, camera, canvas } = this;
        const x = Math.min(drag.startX, drag.endX);
        const y = Math.min(drag.startY, drag.endY);
        const w = Math.abs(drag.endX - drag.startX);
        const h = Math.abs(drag.endY - drag.startY);

        const bl = camera.worldToScreen(x, y, canvas.width, canvas.height);
        const tr = camera.worldToScreen(x + w, y + h, canvas.width, canvas.height);
        const sw = tr.x - bl.x;
        const sh = tr.y - bl.y;

        ctx.fillStyle = 'rgba(85, 85, 119, 0.5)';
        ctx.fillRect(bl.x, bl.y, sw, sh);
        ctx.strokeStyle = '#e94560';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(bl.x, bl.y, sw, sh);
        ctx.setLineDash([]);

        ctx.fillStyle = '#e94560';
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${w.toFixed(1)}×${h.toFixed(1)}m`, bl.x + sw / 2, bl.y + sh / 2);
    }
}
