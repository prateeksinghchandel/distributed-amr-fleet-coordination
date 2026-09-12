import React, { useState } from 'react';
import { parsePointList } from '../simulation/TaskGenerator.js';

const sectionHeader = { fontSize: 11, color: '#888', marginBottom: 6, letterSpacing: 1 };

export default function TaskPanel({ simulation, onMutate }) {
    const [genMode, setGenMode] = useState('manual');
    const [pX, setPX] = useState('5');
    const [pY, setPY] = useState('8');
    const [dX, setDX] = useState('25');
    const [dY, setDY] = useState('15');
    const [sDX, setSDX] = useState('26');
    const [sDY, setSDY] = useState('15');
    const [pickupList, setPickupList] = useState('4, 4\n14, 6\n24, 9');
    const [count, setCount] = useState('5');
    const [assignSel, setAssignSel] = useState({});

    const createManual = () => {
        simulation.createTask(
            { x: Number.parseFloat(pX), y: Number.parseFloat(pY) },
            { x: Number.parseFloat(dX), y: Number.parseFloat(dY) }
        );
        onMutate();
    };

    const createSameDropoff = () => {
        const { points, invalid } = parsePointList(pickupList);
        for (const msg of invalid) simulation.emit(msg);
        simulation.generateSameDropoff(
            { x: Number.parseFloat(sDX), y: Number.parseFloat(sDY) },
            points
        );
        onMutate();
    };

    const createRandom = () => {
        simulation.generateRandomTasks(Number.parseFloat(count) || 0);
        onMutate();
    };

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
            <div style={sectionHeader}>TASK GENERATOR</div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
                {[['manual', 'Manual'], ['same', 'Same Dropoff'], ['random', 'Random']].map(([key, label]) => (
                    <button
                        key={key}
                        style={tabStyle(genMode === key)}
                        onClick={() => setGenMode(key)}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {genMode === 'manual' && (
                <div>
                    <div style={labelStyle}>Pickup</div>
                    <PointRow x={pX} y={pY} onX={setPX} onY={setPY} />
                    <div style={labelStyle}>Dropoff</div>
                    <PointRow x={dX} y={dY} onX={setDX} onY={setDY} />
                    <button style={actionButtonStyle} onClick={createManual}>Create Task</button>
                </div>
            )}

            {genMode === 'same' && (
                <div>
                    <div style={labelStyle}>Common dropoff</div>
                    <PointRow x={sDX} y={sDY} onX={setSDX} onY={setSDY} />
                    <div style={labelStyle}>Pickups (one &quot;x, y&quot; per line)</div>
                    <textarea
                        style={textareaStyle}
                        rows={4}
                        value={pickupList}
                        onChange={(e) => setPickupList(e.target.value)}
                    />
                    <button style={actionButtonStyle} onClick={createSameDropoff}>Generate</button>
                </div>
            )}

            {genMode === 'random' && (
                <div>
                    <div style={labelStyle}>Number of tasks</div>
                    <input
                        type="number"
                        style={numInputStyle}
                        value={count}
                        min="1"
                        onChange={(e) => setCount(e.target.value)}
                    />
                    <button style={actionButtonStyle} onClick={createRandom}>Generate</button>
                </div>
            )}

            <div style={{ ...sectionHeader, marginTop: 14 }}>TASKS ({simulation.tasks.length})</div>
            {simulation.tasks.length === 0 && (
                <div style={{ fontSize: 11, color: '#666' }}>No tasks yet</div>
            )}
            {simulation.tasks.slice().reverse().map((task) => (
                <TaskRow
                    key={task.id}
                    task={task}
                    simulation={simulation}
                    assignSel={assignSel[task.id] || ''}
                    onAssignSel={(v) => setAssignSel((prev) => ({ ...prev, [task.id]: v }))}
                    onMutate={onMutate}
                />
            ))}
        </div>
    );
}

function TaskRow({ task, simulation, assignSel, onAssignSel, onMutate }) {
    const pending = task.status === 'PENDING';
    const active = task.status === 'ASSIGNED' || task.status === 'PICKING_UP' || task.status === 'DELIVERING';
    const available = simulation.robots.filter(
        (r) => r.online && (r.currentTaskId === null || r.status === 'COMPLETED')
    );

    const assign = () => {
        if (!assignSel) return;
        simulation.assignTask(task.id, assignSel);
        onMutate();
    };

    return (
        <div data-task={task.id} style={{
            padding: 6,
            marginBottom: 4,
            borderRadius: 3,
            border: '1px solid #0f3460',
            background: '#0a0a1a',
            fontSize: 11
        }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 'bold', color: '#e0e0e0' }}>{task.id}</span>
                <span style={{ color: taskStatusColor(task.status) }}>{task.status}</span>
            </div>
            <div style={{ color: '#aaa', marginTop: 2 }}>
                P({task.pickup.x.toFixed(1)}, {task.pickup.y.toFixed(1)})
                {' → '}
                D({task.dropoff.x.toFixed(1)}, {task.dropoff.y.toFixed(1)})
            </div>
            {task.assignedRobotId && (
                <div style={{ color: '#00c8ff', marginTop: 2 }}>Assigned to {task.assignedRobotId}</div>
            )}
            {pending && (
                <div style={{ display: 'flex', gap: 4, marginTop: 4, alignItems: 'center' }}>
                    <select
                        style={selectStyle}
                        value={assignSel}
                        onChange={(e) => onAssignSel(e.target.value)}
                    >
                        <option value="">Robot…</option>
                        {available.map((r) => (
                            <option key={r.id} value={r.id}>{r.id}</option>
                        ))}
                    </select>
                    <button style={actionButtonStyle} onClick={assign}>Assign</button>
                </div>
            )}
            {active && (
                <button
                    style={{ ...actionButtonStyle, marginTop: 4, background: '#3a1a1a', borderColor: '#e94560' }}
                    onClick={() => { simulation.cancelTask(task.id); onMutate(); }}
                >
                    Cancel task
                </button>
            )}
        </div>
    );
}

function taskStatusColor(status) {
    switch (status) {
        case 'COMPLETED': return '#2ecc71';
        case 'CANCELLED':
        case 'FAILED': return '#e74c3c';
        case 'PICKING_UP':
        case 'DELIVERING':
        case 'ASSIGNED': return '#00c8ff';
        default: return '#f5a623';
    }
}

function PointRow({ x, y, onX, onY }) {
    return (
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
            <input type="number" style={numInputStyle} value={x} onChange={(e) => onX(e.target.value)} />
            <input type="number" style={numInputStyle} value={y} onChange={(e) => onY(e.target.value)} />
        </div>
    );
}

const labelStyle = { fontSize: 11, color: '#888', marginBottom: 3 };

const numInputStyle = {
    width: '100%', padding: '3px 4px', background: '#0a0a1a', color: '#e0e0e0',
    border: '1px solid #0f3460', borderRadius: 3, fontSize: 12,
    boxSizing: 'border-box', fontFamily: 'inherit'
};

const textareaStyle = {
    width: '100%', padding: '4px', background: '#0a0a1a', color: '#e0e0e0',
    border: '1px solid #0f3460', borderRadius: 3, fontSize: 11,
    boxSizing: 'border-box', fontFamily: 'inherit', resize: 'vertical', marginBottom: 6
};

const selectStyle = {
    flex: 1, padding: '3px 4px', background: '#0a0a1a', color: '#e0e0e0',
    border: '1px solid #0f3460', borderRadius: 3, fontSize: 11, fontFamily: 'inherit'
};

const actionButtonStyle = {
    padding: '4px 10px', background: '#0f3460', color: '#e0e0e0',
    border: '1px solid #00c8ff', borderRadius: 4, cursor: 'pointer',
    fontSize: 11, fontFamily: 'inherit'
};

function tabStyle(active) {
    return {
        flex: 1, padding: '4px 0', background: active ? '#e94560' : '#0f3460',
        color: '#e0e0e0', border: 'none', borderRadius: 4, cursor: 'pointer',
        fontSize: 11, fontFamily: 'inherit'
    };
}