import React from 'react';
import RobotPanel from './RobotPanel.jsx';

export default function DebugPanel({ debug, simulation }) {
    const mouse = debug.mouseWorld;

    return (
        <div>
            <div style={{ color: '#e94560', fontWeight: 'bold', marginBottom: 4 }}>DEV / DEBUG</div>
            <div style={{ fontSize: 11, color: '#aaa' }}>
                <div>FPS: {debug.fps || '—'}</div>
                <div>Canvas: {debug.canvasSize || '—'}</div>
                <div>World: {debug.worldSize || '—'}</div>
                <div>Camera X: {(debug.x || 0).toFixed(2)}</div>
                <div>Camera Y: {(debug.y || 0).toFixed(2)}</div>
                <div>Camera Zoom: {(debug.zoom || 1).toFixed(2)}</div>
                <div>Grid Spacing: {debug.gridSpacing || '—'} m</div>
                {mouse && mouse.x !== null && (
                    <div>Mouse: ({mouse.x.toFixed(2)}, {mouse.y.toFixed(2)})</div>
                )}
                <div style={{ marginTop: 4 }}>Sim time: {simulation.time.toFixed(1)} s</div>
                <div>Speed: {simulation.speed}x</div>
                <div>Robots: {simulation.robots.length}</div>
                <div>Tasks: {simulation.tasks.length}</div>
                <div>Obstacles: {simulation.obstacles.length}</div>
                <div>Events: {simulation.events.length}</div>
            </div>
            <div style={{ marginTop: 10 }}>
                <RobotPanel robot={simulation.getSelectedRobot()} />
            </div>
        </div>
    );
}