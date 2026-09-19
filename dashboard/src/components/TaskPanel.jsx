import { useState } from 'react';
import { useTheme } from '../theme/ThemeContext.jsx';
import { ui, taskColor } from '../theme/ui.js';

const TASK_TERMINAL = new Set(['COMPLETED', 'CANCELLED', 'FAILED']);

export default function TaskPanel({ fleet, onSelectTask, maxTasks = 50 }) {
    const { palette: P } = useTheme();
    const S = ui(P);
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
    const [selectedId, setSelectedId] = useState(null);
    const connected = fleet.isConnected;
    const n = Math.min(fleet.tasksList.length, Math.max(0, maxTasks));
    const ordered = [...fleet.tasksList].sort((a, b) => {
        const aTerm = TASK_TERMINAL.has(a.status);
        const bTerm = TASK_TERMINAL.has(b.status);
        if (aTerm !== bTerm) return aTerm ? 1 : -1;
        return (b.updatedAt || 0) - (a.updatedAt || 0) || (b.createdAt || 0) - (a.createdAt || 0);
    });

    const createManual = () => {
        fleet.createTask(
            { x: Number.parseFloat(pX), y: Number.parseFloat(pY) },
            { x: Number.parseFloat(dX), y: Number.parseFloat(dY) }
        );
    };

    const createSameDropoff = () => {
        const { points } = parsePointList(pickupList);
        const dropoff = { x: Number.parseFloat(sDX), y: Number.parseFloat(sDY) };
        for (const p of points) {
            fleet.createTask(p, dropoff);
        }
    };

    const createRandom = () => {
        fleet.generateRandomTasks(Number.parseInt(count, 10) || 0);
    };

    const selectTask = (taskId) => {
        setSelectedId(taskId);
        if (onSelectTask) onSelectTask(taskId);
    };

    return (
        <div style={{ padding: 12 }}>
            <div style={S.sectionTitle}>TASK GENERATOR</div>
            <div style={{ fontSize: 10, color: P.textDim, marginBottom: 6 }}>
                Commands are sent to the Python coordinator ({fleet.conn.url}) — tasks appear once auctioned.
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
                {[['manual', 'Manual'], ['same', 'Same Dropoff'], ['random', 'Random']].map(([key, label]) => (
                    <button key={key} style={S.tab(genMode === key)} onClick={() => setGenMode(key)}>
                        {label}
                    </button>
                ))}
            </div>

            {genMode === 'manual' && (
                <div>
                    <div style={S.label}>Pickup</div>
                    <PointRow x={pX} y={pY} onX={setPX} onY={setPY} />
                    <div style={S.label}>Dropoff</div>
                    <PointRow x={dX} y={dY} onX={setDX} onY={setDY} />
                    <button style={S.button('primary')} disabled={!connected} onClick={createManual}>Create Task</button>
                </div>
            )}

            {genMode === 'same' && (
                <div>
                    <div style={S.label}>Common dropoff</div>
                    <PointRow x={sDX} y={sDY} onX={setSDX} onY={setSDY} />
                    <div style={S.label}>Pickups (one &quot;x, y&quot; per line)</div>
                    <textarea style={S.textarea} rows={4} value={pickupList} onChange={(e) => setPickupList(e.target.value)} />
                    <button style={S.button('primary')} disabled={!connected} onClick={createSameDropoff}>Generate</button>
                </div>
            )}

            {genMode === 'random' && (
                <div>
                    <div style={S.label}>Number of tasks</div>
                    <input style={S.input} type="number" value={count} min="1" onChange={(e) => setCount(e.target.value)} />
                    <div style={{ height: 4 }} />
                    <button style={S.button('primary')} disabled={!connected} onClick={createRandom}>Generate</button>
                </div>
            )}

            <div style={{ ...S.sectionTitle, marginTop: 14 }}>
                TASKS ({n}{fleet.tasksList.length > n ? ` of ${fleet.tasksList.length}` : ''})
            </div>
            {fleet.tasksList.length === 0 && (
                <div style={{ fontSize: 11, color: P.textDim }}>No tasks yet</div>
            )}
            {ordered.slice(0, maxTasks).map((task) => (
                <TaskRow
                    key={task.id}
                    task={task}
                    fleet={fleet}
                    connected={connected}
                    isSelected={selectedId === task.id}
                    onSelect={() => selectTask(task.id)}
                    assignSel={assignSel[task.id] || ''}
                    onAssignSel={(v) => setAssignSel((prev) => ({ ...prev, [task.id]: v }))}
                />
            ))}
        </div>
    );
}

function TaskRow({ task, fleet, connected, isSelected, onSelect, assignSel, onAssignSel }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const pending = task.status === 'PENDING';
    const active = task.status === 'ASSIGNED' || task.status === 'PICKING_UP' || task.status === 'DELIVERING';
    const available = fleet.robotsList.filter(
        (r) => r.online && (r.currentTaskId === null || r.status === 'COMPLETED')
    );
    const statusColor = taskColor(P, task.status);

    const assign = () => {
        if (!assignSel) return;
        fleet.assignTask(task.id, assignSel);
    };

    return (
        <div
            data-task={task.id}
            onClick={onSelect}
            style={{
                padding: 6,
                marginBottom: 4,
                borderRadius: 3,
                cursor: 'pointer',
                border: `1px solid ${isSelected ? P.accent : P.border}`,
                background: isSelected ? P.surfaceActive : P.surfaceElevated,
                fontSize: 11,
            }}
        >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 'bold', color: P.text }}>{task.id}</span>
                <span style={{ color: statusColor }}>{task.status}</span>
            </div>
            <div style={{ color: P.textMuted, marginTop: 2 }}>
                P({task.pickup.x.toFixed(1)}, {task.pickup.y.toFixed(1)})
                {' → '}
                D({task.dropoff.x.toFixed(1)}, {task.dropoff.y.toFixed(1)})
            </div>
            {task.assignedRobotId && (
                <div style={{ color: P.info, marginTop: 2 }}>Assigned to {task.assignedRobotId}</div>
            )}
            {pending && (
                <div style={{ display: 'flex', gap: 4, marginTop: 4, alignItems: 'center' }}
                    onClick={(e) => e.stopPropagation()}>
                    <select
                        style={S.select}
                        value={assignSel}
                        disabled={!connected || available.length === 0}
                        onChange={(e) => onAssignSel(e.target.value)}
                    >
                        <option value="">Robot…</option>
                        {available.map((r) => (
                            <option key={r.id} value={r.id}>{r.id}</option>
                        ))}
                    </select>
                    <button style={S.button('primary')} disabled={!connected || !assignSel} onClick={assign}>Assign</button>
                </div>
            )}
            {active && (
                <button
                    style={{ ...S.button('danger'), marginTop: 4 }}
                    disabled={!connected}
                    onClick={(e) => { e.stopPropagation(); fleet.cancelTask(task.id); }}
                >
                    Cancel task
                </button>
            )}
        </div>
    );
}

function parsePointList(text) {
    const points = [];
    const invalid = [];
    const lines = String(text).split('\n');
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        const parts = line.split(',').map((s) => Number.parseFloat(s.trim()));
        if (parts.length === 2 && Number.isFinite(parts[0]) && Number.isFinite(parts[1])) {
            points.push({ x: parts[0], y: parts[1] });
        } else {
            invalid.push(`Invalid point on line ${i + 1}: "${lines[i]}"`);
        }
    }
    return { points, invalid };
}

function PointRow({ x, y, onX, onY }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    return (
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
            <input type="number" style={S.input} value={x} onChange={(e) => onX(e.target.value)} />
            <input type="number" style={S.input} value={y} onChange={(e) => onY(e.target.value)} />
        </div>
    );
}