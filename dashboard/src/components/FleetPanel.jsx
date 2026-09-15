import React from 'react';

export default function FleetPanel({ fleet }) {
    return (
        <div style={{
            width: 230,
            minWidth: 230,
            background: '#16213e',
            borderLeft: '2px solid #0f3460',
            padding: 12,
            overflowY: 'auto',
            fontFamily: 'monospace'
        }}>
            <div style={{ fontSize: 11, color: '#888', marginBottom: 6, letterSpacing: 1, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>FLEET</span>
                <button
                    style={{
                        padding: '2px 8px', background: '#0f3460', color: '#e0e0e0',
                        border: '1px solid #555577', borderRadius: 3, cursor: 'not-allowed',
                        fontSize: 11, fontFamily: 'inherit', opacity: 0.45
                    }}
                    disabled
                    title="Robots are spawned by the backend — read-only"
                >
                    + Add Robot
                </button>
            </div>
            {fleet.robotsList.length === 0 && (
                <div style={{ fontSize: 11, color: '#666' }}>
                    Waiting for robots/telemetry…
                </div>
            )}
            {fleet.robotsList.map((r) => {
                const selected = fleet.selectedRobotId === r.id;
                return (
                    <div
                        key={r.id}
                        onClick={() => fleet.setSelectedRobot(r.id)}
                        style={{
                            padding: 8,
                            marginBottom: 6,
                            borderRadius: 4,
                            cursor: 'pointer',
                            border: selected ? '2px solid' : '1px solid #0f3460',
                            borderColor: selected ? r.color : undefined,
                            background: selected ? '#0f3460' : '#0a0a1a'
                        }}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ width: 10, height: 10, borderRadius: '50%', background: r.online ? r.color : '#555577', display: 'inline-block' }} />
                            <span style={{ fontWeight: 'bold', fontSize: 13, color: '#e0e0e0' }}>{r.id}</span>
                            <span style={{ fontSize: 10, color: r.online ? '#00ff88' : '#e94560' }}>
                                {r.online ? 'ONLINE' : 'OFFLINE'}
                            </span>
                            <span style={{ marginLeft: 'auto', fontSize: 10, color: '#aaa' }}>
                                {Math.round(r.battery)}%
                            </span>
                        </div>
                        <div style={{ fontSize: 11, color: '#aaa', marginTop: 4 }}>
                            {r.status}
                            {r.blocked && <span style={{ color: '#e94560' }}> · BLOCKED</span>}
                            {r.currentTaskId && <span style={{ color: '#00c8ff' }}> · {r.currentTaskId}</span>}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}