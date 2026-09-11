import React from 'react';

export default function DebugPanel({ debug }) {
    if (!debug) return null;

    const mouse = debug.mouseWorld;

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460', fontSize: 11 }}>
            <div style={{ color: '#e94560', fontWeight: 'bold', marginBottom: 4 }}>DEBUG</div>
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
        </div>
    );
}
