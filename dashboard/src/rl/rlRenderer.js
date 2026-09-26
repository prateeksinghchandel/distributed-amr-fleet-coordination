/**
 * rlRenderer — draws the *actual* Python Gymnasium environment state.
 *
 * The Python backend streams full snapshots over WebSocket; this module is a
 * pure Canvas2D painter over that data. Robots, obstacles, goals, global
 * paths, RL trajectories, lidar rays and collision radii are all taken from
 * the snapshot — nothing is simulated client-side.
 */
import { getGridSpacing, formatWorldCoord } from '../rendering/coordinates.js';
import { withAlpha } from '../theme/palette.js';

const LIDAR_RAYS = 36;

export function drawRlSnapshot(ctx, snap, camera, canvasW, canvasH, P) {
    ctx.clearRect(0, 0, canvasW, canvasH);
    drawBackground(ctx, canvasW, canvasH, P);
    drawGrid(ctx, camera, canvasW, canvasH, P);
    if (!snap) return;
    drawBounds(ctx, snap, camera, canvasW, canvasH, P);
    drawObstacles(ctx, snap, camera, canvasW, canvasH, P);
    drawDetectionRange(ctx, snap, camera, canvasW, canvasH, P);
    drawGlobalPaths(ctx, snap, camera, canvasW, canvasH, P);
    drawTrajectories(ctx, snap, camera, canvasW, canvasH, P);
    drawLidar(ctx, snap, camera, canvasW, canvasH, P);
    drawGoals(ctx, snap, camera, canvasW, canvasH, P);
    drawRobots(ctx, snap, camera, canvasW, canvasH, P);
}

function drawBackground(ctx, w, h, P) {
    ctx.fillStyle = P.canvasBg;
    ctx.fillRect(0, 0, w, h);
}

function drawGrid(ctx, camera, w, h, P) {
    const spacing = getGridSpacing(camera.zoom);
    const halfW = w / 2 / camera.zoom;
    const halfH = h / 2 / camera.zoom;
    const startX = Math.floor((camera.x - halfW) / spacing) * spacing;
    const endX = Math.ceil((camera.x + halfW) / spacing) * spacing;
    const startY = Math.floor((camera.y - halfH) / spacing) * spacing;
    const endY = Math.ceil((camera.y + halfH) / spacing) * spacing;

    ctx.strokeStyle = P.gridLine;
    ctx.lineWidth = 0.5;
    for (let x = startX; x <= endX; x += spacing) {
        const s = worldToScreen(camera, x, 0, w, h);
        ctx.beginPath(); ctx.moveTo(s.x, 0); ctx.lineTo(s.x, h); ctx.stroke();
    }
    for (let y = startY; y <= endY; y += spacing) {
        const s = worldToScreen(camera, 0, y, w, h);
        ctx.beginPath(); ctx.moveTo(0, s.y); ctx.lineTo(w, s.y); ctx.stroke();
    }

    ctx.fillStyle = P.gridLabel;
    ctx.font = `${Math.max(10, Math.min(14, 12 * camera.zoom))}px monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let x = startX; x <= endX; x += spacing) {
        if (Math.abs(x) < spacing * 0.01) continue;
        const s = worldToScreen(camera, x, 0, w, h);
        ctx.fillText(`${formatWorldCoord(x)}m`, s.x, 2);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let y = startY; y <= endY; y += spacing) {
        if (Math.abs(y) < spacing * 0.01) continue;
        const s = worldToScreen(camera, 0, y, w, h);
        ctx.fillText(`${formatWorldCoord(y)}m`, w - 4, s.y);
    }
}

function drawBounds(ctx, snap, camera, w, h, P) {
    const b = snap.bounds || {};
    const bl = worldToScreen(camera, 0, 0, w, h);
    const tr = worldToScreen(camera, b.width || 30, b.height || 20, w, h);
    ctx.strokeStyle = P.accentBorder;
    ctx.lineWidth = 2;
    ctx.strokeRect(bl.x, bl.y, tr.x - bl.x, tr.y - bl.y);
}

function drawObstacles(ctx, snap, camera, w, h, P) {
    for (const o of snap.obstacles || []) {
        const bl = worldToScreen(camera, o.x, o.y, w, h);
        const tr = worldToScreen(camera, o.x + o.width, o.y + o.height, w, h);
        const ow = tr.x - bl.x;
        const oh = tr.y - bl.y;
        const isWall = o.type === 'wall';
        ctx.fillStyle = isWall ? withAlpha(P.accent, 0.22) : P.obstacleFill;
        ctx.fillRect(bl.x, bl.y, ow, oh);
        ctx.strokeStyle = isWall ? P.accent : P.obstacleStroke;
        ctx.lineWidth = 1.5;
        if (!isWall) ctx.setLineDash([4, 4]);
        ctx.strokeRect(bl.x, bl.y, ow, oh);
        ctx.setLineDash([]);
        if (ow > 26 && oh > 26) {
            ctx.fillStyle = P.obstacleText;
            ctx.font = '10px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(o.id || '', bl.x + ow / 2, bl.y + oh / 2);
        }
    }
    for (const d of snap.dynamic_obstacles || []) {
        const bl = worldToScreen(camera, d.x, d.y, w, h);
        const tr = worldToScreen(camera, d.x + d.width, d.y + d.height, w, h);
        ctx.fillStyle = withAlpha('#e84393', 0.35);
        ctx.fillRect(bl.x, bl.y, tr.x - bl.x, tr.y - bl.y);
        ctx.strokeStyle = '#e84393';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(bl.x, bl.y, tr.x - bl.x, tr.y - bl.y);
    }
}

function drawDetectionRange(ctx, snap, camera, w, h, P) {
    const rl = rlRobot(snap);
    if (!rl || !snap.lidar) return;
    const s = worldToScreen(camera, rl.x, rl.y, w, h);
    const r = snap.lidar.range * camera.zoom;
    ctx.strokeStyle = withAlpha(P.info, 0.18);
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
}

function drawGlobalPaths(ctx, snap, camera, w, h, P) {
    for (const robot of snap.robots || []) {
        const path = robot.path;
        if (!path || path.length < 2) continue;
        const pts = path.map(([px, py]) => worldToScreen(camera, px, py, w, h));
        ctx.beginPath();
        pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
        ctx.strokeStyle = robot.rl ? withAlpha(P.accent, 0.8) : withAlpha(P.textDim, 0.4);
        ctx.lineWidth = robot.rl ? 2 : 1.2;
        ctx.setLineDash([6, 5]);
        ctx.stroke();
        ctx.setLineDash([]);
    }
}

function drawTrajectories(ctx, snap, camera, w, h, P) {
    for (const robot of snap.robots || []) {
        const traj = robot.trajectory;
        if (!traj || traj.length < 2) continue;
        // Downsample long trajectories so drawing stays cheap.
        const step = Math.max(1, Math.floor(traj.length / 600));
        const pts = [];
        for (let i = 0; i < traj.length; i += step) {
            pts.push(worldToScreen(camera, traj[i][0], traj[i][1], w, h));
        }
        ctx.beginPath();
        pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
        ctx.strokeStyle = robot.rl ? withAlpha(P.warning, 0.75) : withAlpha(P.info, 0.25);
        ctx.lineWidth = robot.rl ? 1.8 : 1;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.stroke();
    }
}

function drawLidar(ctx, snap, camera, w, h, P) {
    const rl = rlRobot(snap);
    const lidar = snap.lidar;
    if (!rl || !lidar || !lidar.rays) return;
    const origin = worldToScreen(camera, rl.x, rl.y, w, h);
    const range = lidar.range || 8;
    for (let i = 0; i < LIDAR_RAYS; i++) {
        const ray = lidar.rays[i];
        if (!ray) continue;
        const end = worldToScreen(camera, rl.x + ray.dx * ray.distance,
                                  rl.y + ray.dy * ray.distance, w, h);
        const frac = Math.max(0, Math.min(1, ray.distance / range));
        const color = rayColor(frac, P);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(end.x, end.y, 1.8 * (0.5 + 0.5 * frac), 0, Math.PI * 2);
        ctx.fill();
    }
}

function hexRgb(hex) {
    const clean = String(hex || '#888888').replace('#', '');
    if (clean.length !== 6) return [136, 136, 136];
    return [parseInt(clean.slice(0, 2), 16),
            parseInt(clean.slice(2, 4), 16),
            parseInt(clean.slice(4, 6), 16)];
}

function rayColor(frac, P) {
    // frac=1 -> far/clear (green), frac=0 -> imminent return (red)
    const near = hexRgb(P.danger);
    const far = hexRgb(P.success);
    const mix = (a, b) => Math.round(a + (b - a) * frac);
    return `rgb(${mix(near[0], far[0])}, ${mix(near[1], far[1])}, ${mix(near[2], far[2])})`;
}

function drawGoals(ctx, snap, camera, w, h, P) {
    for (const robot of snap.robots || []) {
        const g = robot.goal;
        if (!g) continue;
        const s = worldToScreen(camera, g.x, g.y, w, h);
        const ring = robot.rl ? P.success : withAlpha(P.info, 0.55);
        ctx.strokeStyle = robot.rl ? P.success : ring;
        ctx.lineWidth = robot.rl ? 2.5 : 1.5;
        const radius = Math.max(6, 0.4 * camera.zoom);
        ctx.beginPath();
        ctx.arc(s.x, s.y, radius, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(s.x - radius * 1.6, s.y);
        ctx.lineTo(s.x + radius * 1.6, s.y);
        ctx.moveTo(s.x, s.y - radius * 1.6);
        ctx.lineTo(s.x, s.y + radius * 1.6);
        ctx.lineWidth = 1.2;
        ctx.stroke();
    }
}

function drawRobots(ctx, snap, camera, w, h, P) {
    for (const robot of snap.robots || []) {
        const s = worldToScreen(camera, robot.x, robot.y, w, h);
        const r = Math.max(4, robot.radius * camera.zoom * (robot.rl ? 1.15 : 1));

        // collision radius
        ctx.strokeStyle = withAlpha(P.danger, 0.3);
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);

        const body = robot.rl ? P.accent : P.info;
        const alpha = robot.collided ? 0.35 : robot.reached ? 0.85 : 1;
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
        ctx.fillStyle = robot.rl ? body : withAlpha(body, 0.8);
        ctx.fill();
        ctx.strokeStyle = robot.rl ? P.selectionOutline : withAlpha(P.selectionOutline, 0.4);
        ctx.lineWidth = robot.rl ? 2.5 : 1;
        ctx.stroke();
        ctx.globalAlpha = 1;

        // heading
        const hx = Math.cos(robot.heading) * r;
        const hy = Math.sin(robot.heading) * r;
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(s.x + hx, s.y + hy);
        ctx.strokeStyle = robot.rl ? '#ffffff' : P.text;
        ctx.lineWidth = robot.rl ? 2.5 : 1.5;
        ctx.stroke();

        // status marks
        if (robot.collided) {
            const x = Math.max(3, r * 0.5);
            ctx.strokeStyle = P.danger;
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            ctx.moveTo(s.x - x, s.y - x); ctx.lineTo(s.x + x, s.y + x);
            ctx.moveTo(s.x + x, s.y - x); ctx.lineTo(s.x - x, s.y + x);
            ctx.stroke();
        } else if (robot.reached) {
            ctx.strokeStyle = P.success;
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            ctx.arc(s.x, s.y, r * 1.35, 0, Math.PI * 2);
            ctx.stroke();
        }

        // label
        ctx.fillStyle = robot.rl ? '#fff' : P.text;
        ctx.font = `bold ${Math.max(10, Math.min(14, 12 * camera.zoom))}px monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(`${robot.id}${robot.rl ? ' (RL)' : ''}`, s.x, s.y - r - 5);
    }
}

function rlRobot(snap) {
    if (!snap || !snap.rl_agent) return null;
    return (snap.robots || []).find((r) => r.id === snap.rl_agent) || null;
}

function worldToScreen(camera, wx, wy, canvasW, canvasH) {
    return {
        x: (wx - camera.x) * camera.zoom + canvasW / 2,
        y: (wy - camera.y) * camera.zoom + canvasH / 2,
    };
}