import React from 'react';

const SPEEDS = [0.5, 1, 2, 5];

export default function Header({ simulation, onMutate, onReset, onResetCamera }) {
    const buttonStyle = {
        padding: '4px 10px',
        background: '#16213e',
        color: '#e0e0e0',
        border: '1px solid #0f3460',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 12,
        fontFamily: 'inherit'
    };

    return (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '6px 16px',
            background: '#0f3460',
            borderBottom: '2px solid #e94560',
            fontFamily: 'monospace',
            color: '#e0e0e0'
        }}>
            <div style={{ fontWeight: 'bold', color: '#e94560', fontSize: 15 }}>
                AMR FLEET SIMULATION TESTBED
            </div>
            <div style={{ fontSize: 12, color: simulation.running ? '#00ff88' : '#e94560' }}>
                SIMULATION: {simulation.stateLabel}
            </div>
            <div style={{ flex: 1 }} />
            <button
                style={buttonStyle}
                onClick={() => { simulation.togglePaused(); onMutate(); }}
            >
                {simulation.running ? 'Pause' : 'Resume'}
            </button>
            <button style={{ ...buttonStyle, borderColor: '#e94560' }} onClick={onReset}>
                Reset
            </button>
            <button style={buttonStyle} onClick={onResetCamera}>
                Reset View
            </button>
            <div style={{ display: 'flex', gap: 4 }}>
                {SPEEDS.map((s) => (
                    <button
                        key={s}
                        style={{
                            ...buttonStyle,
                            background: simulation.speed === s ? '#e94560' : '#16213e',
                            fontWeight: simulation.speed === s ? 'bold' : 'normal'
                        }}
                        onClick={() => { simulation.setSpeed(s); onMutate(); }}
                    >
                        {s}x
                    </button>
                ))}
            </div>
        </div>
    );
}