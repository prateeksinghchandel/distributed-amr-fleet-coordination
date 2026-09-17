import { Camera } from './camera.js';
import { formatWorldCoord, getGridSpacing } from './coordinates.js';

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

    render(scene, mouseWorldX, mouseWorldY, obstacleDrag) {
        const { ctx, canvas } = this;
        const w = canvas.width;
        const h = canvas.height;
        const warehouse = scene.warehouse;

        ctx.clearRect(0, 0, w, h);

        this.drawBackground(w, h);
        this.drawGrid(w, h);
        this.drawBoundary(warehouse);
        this.drawLogisticsZones(warehouse);
        this.drawObstacles(scene.obstacles, warehouse.shelves);
        this.drawTasks(scene.tasks);
        this.drawRobotPaths(scene.robots);
        this.drawRobots(scene.robots, scene.selectedRobotId);
        this.drawTargetIndicator(scene);
        if (obstacleDrag) this.drawObstaclePreview(obstacleDrag);
        this.drawMouseCoords(mouseWorldX, mouseWorldY);
    }

    drawBackground(w, h) {
        const { ctx } = this;
        ctx.fillStyle = '#1a1a2e';
        ctx.fillRect(0, 0, w, h);
    }

    drawGrid(w, h) {
        const { ctx, camera } = this;
        const spacing = getGridSpacing(camera.zoom);
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

    drawBoundary(warehouse) {
        const { ctx, camera, canvas } = this;
        const bl = camera.worldToScreen(0, 0, canvas.width, canvas.height);
        const tr = camera.worldToScreen(warehouse.width, warehouse.height, canvas.width, canvas.height);

        ctx.strokeStyle = '#e94560';
        ctx.lineWidth = 2;
        ctx.strokeRect(bl.x, bl.y, tr.x - bl.x, tr.y - bl.y);

        ctx.fillStyle = '#e94560';
        ctx.font = '12px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(`(${formatWorldCoord(0)}, ${formatWorldCoord(0)})`, bl.x + 4, bl.y - 2);
        ctx.textAlign = 'right';
        ctx.fillText(`(${formatWorldCoord(warehouse.width)}, ${formatWorldCoord(warehouse.height)})`, tr.x - 4, tr.y + 12);
    }

    drawLogisticsZones(warehouse) {
        if (!warehouse) return;
        const { ctx, camera, canvas } = this;

        // 1. Delivery Zone on Left
        if (warehouse.deliveryZone) {
            const dz = warehouse.deliveryZone;
            const bl = camera.worldToScreen(dz.x, dz.y, canvas.width, canvas.height);
            const tr = camera.worldToScreen(dz.x + dz.width, dz.y + dz.height, canvas.width, canvas.height);
            const zw = tr.x - bl.x;
            const zh = tr.y - bl.y;

            // Subtle zone background
            ctx.fillStyle = 'rgba(0, 180, 255, 0.06)';
            ctx.fillRect(bl.x, bl.y, zw, zh);
            ctx.strokeStyle = 'rgba(0, 200, 255, 0.35)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.strokeRect(bl.x, bl.y, zw, zh);
            ctx.setLineDash([]);

            // Zone title
            ctx.fillStyle = '#00c8ff';
            ctx.font = 'bold 11px monospace';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            ctx.fillText('DELIVERY ZONE (DROPOFF)', bl.x + 8, bl.y + 6);

            // Individual delivery docks
            for (const st of dz.stations || []) {
                const sbl = camera.worldToScreen(st.x, st.y, canvas.width, canvas.height);
                const str = camera.worldToScreen(st.x + st.width, st.y + st.height, canvas.width, canvas.height);
                const dw = str.x - sbl.x;
                const dh = str.y - sbl.y;

                ctx.fillStyle = 'rgba(0, 80, 140, 0.6)';
                ctx.fillRect(sbl.x, sbl.y, dw, dh);
                ctx.strokeStyle = '#00c8ff';
                ctx.lineWidth = 1.5;
                ctx.strokeRect(sbl.x, sbl.y, dw, dh);

                // Conveyor dock lines
                ctx.strokeStyle = 'rgba(0, 200, 255, 0.25)';
                ctx.lineWidth = 1;
                const step = Math.max(6, 8 * camera.zoom);
                for (let lx = sbl.x + step; lx < sbl.x + dw; lx += step) {
                    ctx.beginPath();
                    ctx.moveTo(lx, sbl.y);
                    ctx.lineTo(lx, sbl.y + dh);
                    ctx.stroke();
                }

                // Dock Label
                ctx.fillStyle = '#ffffff';
                ctx.font = 'bold 10px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(st.id, sbl.x + dw / 2, sbl.y + dh / 2);
            }
        }

        // 2. AMR Charging Area on Right
        if (warehouse.chargingZone) {
            const cz = warehouse.chargingZone;
            const bl = camera.worldToScreen(cz.x, cz.y, canvas.width, canvas.height);
            const tr = camera.worldToScreen(cz.x + cz.width, cz.y + cz.height, canvas.width, canvas.height);
            const zw = tr.x - bl.x;
            const zh = tr.y - bl.y;

            // Subtle zone background
            ctx.fillStyle = 'rgba(0, 255, 136, 0.05)';
            ctx.fillRect(bl.x, bl.y, zw, zh);
            ctx.strokeStyle = 'rgba(0, 255, 136, 0.35)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.strokeRect(bl.x, bl.y, zw, zh);
            ctx.setLineDash([]);

            // Zone title
            ctx.fillStyle = '#00ff88';
            ctx.font = 'bold 11px monospace';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'top';
            ctx.fillText('AMR CHARGING HUB', tr.x - 8, bl.y + 6);

            // Individual charging pads
            for (const pad of cz.pads || []) {
                const pbl = camera.worldToScreen(pad.x, pad.y, canvas.width, canvas.height);
                const ptr = camera.worldToScreen(pad.x + pad.width, pad.y + pad.height, canvas.width, canvas.height);
                const pw = ptr.x - pbl.x;
                const ph = ptr.y - pbl.y;

                ctx.fillStyle = 'rgba(0, 90, 50, 0.55)';
                ctx.fillRect(pbl.x, pbl.y, pw, ph);
                ctx.strokeStyle = '#00ff88';
                ctx.lineWidth = 1.5;
                ctx.strokeRect(pbl.x, pbl.y, pw, ph);

                // Corner brackets on charging pad
                const cl = Math.min(6, pw * 0.25);
                ctx.strokeStyle = '#ffd43b';
                ctx.lineWidth = 1.5;
                // Top-left
                ctx.beginPath();
                ctx.moveTo(pbl.x + cl, pbl.y); ctx.lineTo(pbl.x, pbl.y); ctx.lineTo(pbl.x, pbl.y + cl);
                // Bottom-right
                ctx.moveTo(ptr.x - cl, ptr.y); ctx.lineTo(ptr.x, ptr.y); ctx.lineTo(ptr.x, ptr.y - cl);
                ctx.stroke();

                // Lightning symbol & label
                ctx.fillStyle = '#ffd43b';
                ctx.font = 'bold 11px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(`⚡ ${pad.id}`, pbl.x + pw / 2, pbl.y + ph / 2);
            }
        }
    }

    drawObstacles(obstacles, shelves = []) {
        const { ctx, camera, canvas } = this;
        const shelfIds = new Set((shelves || []).map((s) => s.id));

        for (const obs of obstacles) {
            const bl = camera.worldToScreen(obs.x, obs.y, canvas.width, canvas.height);
            const tr = camera.worldToScreen(obs.x + obs.width, obs.y + obs.height, canvas.width, canvas.height);
            const w = tr.x - bl.x;
            const h = tr.y - bl.y;

            const isShelf = obs.type === 'shelf' || shelfIds.has(obs.id);

            if (isShelf) {
                // Warehouse rack styling
                ctx.fillStyle = '#2c251f';
                ctx.fillRect(bl.x, bl.y, w, h);
                ctx.strokeStyle = '#ffa94d';
                ctx.lineWidth = 1.5;
                ctx.strokeRect(bl.x, bl.y, w, h);

                // Shelf internal compartment lines
                const slots = 3;
                ctx.strokeStyle = 'rgba(255, 169, 77, 0.35)';
                ctx.lineWidth = 1;
                for (let s = 1; s < slots; s++) {
                    const cx = bl.x + (w / slots) * s;
                    ctx.beginPath();
                    ctx.moveTo(cx, bl.y);
                    ctx.lineTo(cx, bl.y + h);
                    ctx.stroke();
                }

                ctx.fillStyle = '#ffd43b';
                ctx.font = 'bold 10px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(obs.id, bl.x + w / 2, bl.y + h / 2);
            } else {
                // Standard obstacle styling
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

        // Draw pick-face indicators along shelf aisles
        if (shelves && shelves.length > 0) {
            for (const shelf of shelves) {
                if (!shelf.pickPoints) continue;
                for (const pt of shelf.pickPoints) {
                    const spt = camera.worldToScreen(pt.x, pt.y, canvas.width, canvas.height);
                    ctx.beginPath();
                    ctx.arc(spt.x, spt.y, 2, 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(255, 212, 59, 0.45)';
                    ctx.fill();
                }
            }
        }
    }

    drawTasks(tasks) {
        const { ctx, camera, canvas } = this;
        for (const task of tasks) {
            const style = taskStyle(task.status);
            const pickup = camera.worldToScreen(task.pickup.x, task.pickup.y, canvas.width, canvas.height);
            const dropoff = camera.worldToScreen(task.dropoff.x, task.dropoff.y, canvas.width, canvas.height);

            ctx.beginPath();
            ctx.moveTo(pickup.x, pickup.y);
            ctx.lineTo(dropoff.x, dropoff.y);
            ctx.strokeStyle = style.line;
            ctx.lineWidth = 1.2;
            ctx.setLineDash([4, 4]);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.strokeStyle = style.color;
            ctx.lineWidth = 2;
            ctx.fillStyle = 'transparent';
            ctx.beginPath();
            ctx.arc(pickup.x, pickup.y, 6, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = style.color;
            ctx.font = 'bold 9px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText('P', pickup.x, pickup.y + 8);

            ctx.beginPath();
            ctx.arc(dropoff.x, dropoff.y, 7, 0, Math.PI * 2);
            ctx.fillStyle = style.color;
            ctx.fill();
            ctx.beginPath();
            ctx.arc(dropoff.x, dropoff.y, 3, 0, Math.PI * 2);
            ctx.fillStyle = '#1a1a2e';
            ctx.fill();
            ctx.fillText('D', dropoff.x, dropoff.y + 10);

            const label = task.assignedRobotId ? `${task.id} · ${task.assignedRobotId}` : task.id;
            ctx.fillStyle = style.text;
            ctx.font = '10px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            const midX = (pickup.x + dropoff.x) / 2;
            const midY = (pickup.y + dropoff.y) / 2 - 8;
            ctx.fillText(label, midX, midY);
        }
    }

    drawRobotPaths(robots) {
        const { ctx, camera, canvas } = this;
        for (const robot of robots) {
            if (robot.targetX == null || robot.targetY == null) continue;
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

    drawRobots(robots, selectedRobotId) {
        const { ctx, camera, canvas } = this;
        for (const robot of robots) {
            const screen = camera.worldToScreen(robot.x, robot.y, canvas.width, canvas.height);
            const r = robot.radius * camera.zoom;
            const isSelected = selectedRobotId === robot.id;

            ctx.globalAlpha = robot.online ? (isSelected ? 1 : 0.75) : 0.3;
            ctx.beginPath();
            ctx.arc(screen.x, screen.y, Math.max(4, r), 0, Math.PI * 2);
            ctx.fillStyle = robot.online ? robot.color : '#555577';
            ctx.fill();
            ctx.globalAlpha = 1;

            ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(255,255,255,0.5)';
            ctx.lineWidth = isSelected ? 2.5 : 1;
            ctx.stroke();

            if (!robot.online) {
                ctx.beginPath();
                ctx.moveTo(screen.x - r, screen.y - r);
                ctx.lineTo(screen.x + r, screen.y + r);
                ctx.moveTo(screen.x + r, screen.y - r);
                ctx.lineTo(screen.x - r, screen.y + r);
                ctx.strokeStyle = '#e94560';
                ctx.lineWidth = 2;
                ctx.stroke();
                continue;
            }

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
            ctx.fillText(robot.id, screen.x, screen.y - r - 6);
        }
    }

    drawTargetIndicator(scene) {
        const { ctx, camera, canvas } = this;
        const robot = scene.selectedRobot;
        if (!robot || robot.targetX == null || robot.targetY == null) return;

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

function taskStyle(status) {
    switch (status) {
        case 'COMPLETED':
            return { color: '#2ecc71', line: 'rgba(46,204,113,0.35)', text: '#2ecc71' };
        case 'CANCELLED':
        case 'FAILED':
            return { color: '#e74c3c', line: 'rgba(231,76,60,0.35)', text: '#e74c3c' };
        case 'PICKING_UP':
        case 'DELIVERING':
            return { color: '#00c8ff', line: 'rgba(0,200,255,0.5)', text: '#00c8ff' };
        default:
            return { color: '#f5a623', line: 'rgba(245,166,35,0.5)', text: '#f5a623' };
    }
}