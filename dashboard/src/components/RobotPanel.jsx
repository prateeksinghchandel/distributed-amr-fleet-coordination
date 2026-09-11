import React from 'react';
import { formatWorldCoord, formatHeading } from '../rendering/coordinates.js';

export default function RobotPanel({ robot }) {
    if (!robot) {
        return (
            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <div style={{ fontSize: 13, color: '#888' }}>No robot selected</div>
            </div>
        );
    }

    const targetX = robot.targetX !== null ? formatWorldCoord(robot.targetX) : '—';
    const targetY = robot.targetY !== null ? formatWorldCoord(robot.targetY) : '—';

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
            <div style={{ fontSize: 14, fontWeight: 'bold', color: '#00c8ff', marginBottom: 8 }}>
                Robot {robot.id}
            </div>
            <div style={{ fontSize: 12 }}>
                <div style={{ marginBottom: 4 }}>
                    <span style={{ color: '#888' }}>Position</span>
                </div>
                <div>X: {formatWorldCoord(robot.x)} m</div>
                <div>Y: {formatWorldCoord(robot.y)} m</div>
                <div style={{ marginTop: 4 }}>
                    <span style={{ color: '#888' }}>Heading</span>
                </div>
                <div>{formatHeading(robot.heading)}</div>
                <div style={{ marginTop: 4 }}>
                    <span style={{ color: '#888' }}>Speed</span>
                </div>
                <div>{robot.speed.toFixed(2)} m/s</div>
                <div style={{ marginTop: 4 }}>
                    <span style={{ color: '#888' }}>Status</span>
                </div>
                <div style={{ color: robot.status === 'MOVING' ? '#00ff88' : robot.status === 'IDLE' ? '#aaaaaa' : '#e94560' }}>
                    {robot.status}{robot.blocked ? ' (BLOCKED)' : ''}
                </div>
                <div style={{ marginTop: 4 }}>
                    <span style={{ color: '#888' }}>Target</span>
                </div>
                <div>X: {targetX} m</div>
                <div>Y: {targetY} m</div>
            </div>
        </div>
    );
}
