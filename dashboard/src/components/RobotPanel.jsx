import React from 'react';
import { formatWorldCoord, formatHeading } from '../rendering/coordinates.js';

export default function RobotPanel({ robot }) {
    if (!robot) {
        return (
            <div style={{ color: '#888' }}>No robot selected</div>
        );
    }

    const targetX = robot.targetX !== null ? formatWorldCoord(robot.targetX) : '—';
    const targetY = robot.targetY !== null ? formatWorldCoord(robot.targetY) : '—';

    return (
        <div style={{ borderTop: '1px solid #0f3460', paddingTop: 6 }}>
            <div style={{ fontSize: 12, fontWeight: 'bold', color: robot.color, marginBottom: 4 }}>
                Robot {robot.id}
            </div>
            <div style={{ fontSize: 11, color: '#aaa' }}>
                <div>Position: ({formatWorldCoord(robot.x)}, {formatWorldCoord(robot.y)})</div>
                <div>Velocity: ({robot.velocity.x.toFixed(2)}, {robot.velocity.y.toFixed(2)}) m/s</div>
                <div>Heading: {formatHeading(robot.heading)}</div>
                <div>Speed: {robot.speed.toFixed(2)} m/s</div>
                <div>Status: <span style={{ color: statusColor(robot) }}>{robot.status}{robot.blocked ? ' (BLOCKED)' : ''}</span></div>
                <div>Battery: {Math.round(robot.battery)}%</div>
                <div>Connection: {robot.online ? 'ONLINE' : 'OFFLINE'}</div>
                <div>Task: {robot.currentTaskId || '—'}</div>
                <div>Target: ({targetX}, {targetY})</div>
            </div>
        </div>
    );
}

function statusColor(robot) {
    if (!robot.online) return '#e94560';
    if (robot.status === 'COMPLETED') return '#2ecc71';
    if (robot.status === 'MOVING' ||
        robot.status === 'MOVING_TO_PICKUP' ||
        robot.status === 'MOVING_TO_DROPOFF') return '#00c8ff';
    if (robot.status === 'PICKING' || robot.status === 'DROPPING') return '#ffd43b';
    return '#aaa';
}