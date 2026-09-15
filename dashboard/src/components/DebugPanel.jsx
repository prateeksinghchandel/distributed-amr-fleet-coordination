import React from 'react';
import RobotPanel from './RobotPanel.jsx';

export default function DebugPanel({ debug, fleet }) {
    const mouse = debug.mouseWorld;

    return (
        <div>
            <div style={{ color: '#e94560', fontWeight: 'bold', marginBottom: 4 }}>DEV / DEBUG</div>
            <div style={{ fontSize: 11, color: '#aaa' }}>
                <div>Connection: {fleet.connection.status}</div>
                <div>Endpoint: {fleet.conn.url}</div>
                <div>FPS: {debug.fps || '—'}</div>
                <div>Canvas: {debug.canvasSize || '—'}</div>
                <div>Camera X: {(debug.x || 0).toFixed(2)}</div>
                <div>Camera Y: {(debug.y || 0).toFixed(2)}</div>
                <div>Camera Zoom: {(debug.zoom || 1).toFixed(2)}</div>
                <div>Grid Spacing: {debug.gridSpacing || '—'} m</div>
                {mouse && mouse.x !== null && (
                    <div>Mouse: ({mouse.x.toFixed(2)}, {mouse.y.toFixed(2)})</div>
                )}
                <div style={{ marginTop: 4 }}>Robots: {fleet.robotsList.length}</div>
                <div>Tasks: {fleet.tasksList.length}</div>
                <div>Obstacles: {fleet.warehouse ? fleet.warehouse.obstacles.length : 0}</div>
                <div>Auctions: {fleet.auctions.length}</div>
                <div>Last update: {fleet.lastUpdate ? new Date(fleet.lastUpdate).toLocaleTimeString() : '—'}</div>
                <div>Logs: {fleet.logs.length}</div>
            </div>
            <div style={{ marginTop: 10 }}>
                <RobotPanel robot={fleet.getSelectedRobot()} />
            </div>
        </div>
    );
}