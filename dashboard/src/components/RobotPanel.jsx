import React from 'react';
import { formatWorldCoord, formatHeading } from '../rendering/coordinates.js';

export default function RobotPanel({ robot }) {
    if (!robot) {
        return (
            <div style={{ color: '#888' }}>No robot selected</div>
        );
    }

    return (
        <div style={{ borderTop: '1px solid #0f3460', paddingTop: 6 }}>
            <div style={{ fontSize: 12, fontWeight: 'bold', color: robot.color, marginBottom: 4 }}>
                Robot {robot.id}
            </div>
            <div style={{ fontSize: 11, color: '#aaa' }}>
                <div>Position: ({formatWorldCoord(robot.x)}, {formatWorldCoord(robot.y)})</div>
                <div>Heading: {formatHeading(robot.heading)}</div>
                <div>Status: <span style={{ color: statusColor(robot) }}>
                    {robot.status}{robot.blocked ? ' (BLOCKED)' : ''}
                </span></div>
                <div>Battery: {Math.round(robot.battery)}%</div>
                <div>Connection: {robot.online ? 'ONLINE' : 'OFFLINE'}</div>
                <div>Task: {robot.currentTaskId || '—'}</div>
                {robot.lastTelemetryAt && (
                    <div>Last telemetry: {new Date(robot.lastTelemetryAt).toLocaleTimeString()}</div>
                )}
            </div>
        </div>
    );
}

function statusColor(robot) {
    if (!robot.online) return '#e94560';
    if (robot.status === 'COMPLETED') return '#2ecc71';
    if (robot.status === 'MOVING' ||
        robot.status === 'MOVING_TO_PICKUP' ||
        robot.status === 'MOVING_TO_DROPOFF' ||
        robot.status === 'PICKING' ||
        robot.status === 'DROPPING') return '#00c8ff';
    if (robot.status === 'CHARGING') return '#ffd43b';
    return '#aaa';
}