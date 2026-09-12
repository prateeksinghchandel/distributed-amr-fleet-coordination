import React from 'react';
import WarehousePanel from './WarehousePanel.jsx';
import TaskPanel from './TaskPanel.jsx';

export default function Sidebar({ simulation, mode, setMode, onMutate }) {
    return (
        <div style={{
            width: 260,
            minWidth: 260,
            background: '#16213e',
            color: '#e0e0e0',
            display: 'flex',
            flexDirection: 'column',
            overflowY: 'auto',
            borderRight: '2px solid #0f3460',
            fontFamily: 'monospace'
        }}>
            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <h2 style={{ margin: 0, fontSize: 14, color: '#e94560' }}>AMR FLEET</h2>
                <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>
                    {simulation.width} × {simulation.height} m · {simulation.robots.length} robots · {simulation.tasks.length} tasks
                </div>
            </div>
            <WarehousePanel simulation={simulation} mode={mode} setMode={setMode} onMutate={onMutate} />
            <TaskPanel simulation={simulation} onMutate={onMutate} />
        </div>
    );
}