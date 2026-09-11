import React from 'react';
import RobotPanel from './RobotPanel.jsx';
import DebugPanel from './DebugPanel.jsx';

export default function Sidebar({ world, mode, setMode, onResetCamera, onAddRobot, onLog, debug, onMutate }) {
    const robot = world.getSelectedRobot();

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
        }}>
            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <h2 style={{ margin: 0, fontSize: 16, color: '#e94560' }}>AMR Fleet Dashboard</h2>
                <button onClick={onResetCamera} style={{
                    marginTop: 6, width: '100%', padding: '4px 8px',
                    background: '#0f3460', color: '#e0e0e0', border: '1px solid #e94560',
                    borderRadius: 4, cursor: 'pointer', fontSize: 12
                }}>Reset View</button>
            </div>

            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>WAREHOUSE</div>
                <div style={{ fontSize: 13 }}>Width: {world.width} m</div>
                <div style={{ fontSize: 13 }}>Height: {world.height} m</div>
            </div>

            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>CONTROLS</div>
                <button onClick={() => setMode('default')} style={modeButton(mode === 'default')}>
                    Navigate
                </button>
                <button onClick={() => setMode('move-robot')} style={modeButton(mode === 'move-robot')}>
                    Move Robot
                </button>
                <button onClick={onAddRobot} style={modeButton(false)}>
                    Add Robot
                </button>
                <button onClick={() => setMode('add-obstacle')} style={modeButton(mode === 'add-obstacle')}>
                    Add Obstacle
                </button>
                <button onClick={() => setMode('move-obstacle')} style={modeButton(mode === 'move-obstacle')}>
                    Move Obstacle
                </button>
                <button onClick={() => setMode('add-task')} style={modeButton(mode === 'add-task')}>
                    Add Task
                </button>
            </div>

            <div style={{ padding: 12, borderBottom: '1px solid #0f3460', flexGrow: 1 }}>
                <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>ROBOTS</div>
                {world.robots.map(r => (
                    <div key={r.id} onClick={() => { world.selectedRobotId = r.id; onMutate(); }} style={{
                        padding: '4px 8px', cursor: 'pointer', borderRadius: 3,
                        background: world.selectedRobotId === r.id ? '#0f3460' : 'transparent',
                        borderLeft: world.selectedRobotId === r.id ? '3px solid #e94560' : '3px solid transparent',
                        fontSize: 13, marginBottom: 2
                    }}>
                        {r.id} - {r.status}
                    </div>
                ))}
                {world.obstacles.length > 0 && (
                    <div style={{ fontSize: 11, color: '#888', marginTop: 8, marginBottom: 6 }}>OBSTACLES</div>
                )}
                {world.obstacles.map(o => (
                    <div key={o.id} onClick={() => { world.selectedObstacleId = o.id; onMutate(); }} style={{
                        padding: '4px 8px', cursor: 'pointer', borderRadius: 3,
                        background: world.selectedObstacleId === o.id ? '#0f3460' : 'transparent',
                        borderLeft: world.selectedObstacleId === o.id ? '3px solid #e94560' : '3px solid transparent',
                        fontSize: 11, marginBottom: 2, display: 'flex', justifyContent: 'space-between'
                    }}>
                        <span>{o.id}</span>
                        <span style={{ color: '#e94560', cursor: 'pointer' }} onClick={(e) => {
                            e.stopPropagation();
                            world.removeObstacle(o.id);
                            world.selectedObstacleId = null;
                            onMutate();
                            onLog(`Obstacle ${o.id} removed`);
                        }}>✕</span>
                    </div>
                ))}
            </div>

            <RobotPanel robot={robot} />

            <DebugPanel debug={debug} />
        </div>
    );
}

function modeButton(active) {
    return {
        marginBottom: 4, width: '100%', padding: '5px 8px',
        background: active ? '#e94560' : '#0f3460',
        color: '#e0e0e0', border: 'none', borderRadius: 4,
        cursor: 'pointer', fontSize: 12, textAlign: 'left'
    };
}