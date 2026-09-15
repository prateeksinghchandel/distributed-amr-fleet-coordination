import React, { useState } from 'react';
import { MIN_DIM, MAX_DIM } from '../simulation/Simulation.js';

const sectionHeader = { fontSize: 11, color: '#888', marginBottom: 6, letterSpacing: 1 };

export default function WarehousePanel({ simulation, mode, setMode, onMutate, onOpenBuilder }) {
    const [draftW, setDraftW] = useState(String(simulation.width));
    const [draftH, setDraftH] = useState(String(simulation.height));
    const [dimError, setDimError] = useState(null);

    const applyDimensions = () => {
        const w = Number.parseFloat(draftW);
        const h = Number.parseFloat(draftH);
        if (!Number.isFinite(w) || !Number.isFinite(h)) {
            setDimError('Enter numeric dimensions');
            return;
        }
        if (w < MIN_DIM || h < MIN_DIM || w > MAX_DIM || h > MAX_DIM) {
            setDimError(`Dimensions must be ${MIN_DIM}–${MAX_DIM} m`);
            return;
        }
        setDimError(null);
        simulation.setDimensions(w, h);
        onMutate();
    };

    const deliveryDocks = simulation.warehouse.getDeliveryStations ? simulation.warehouse.getDeliveryStations().length : 0;
    const chargingPads = simulation.warehouse.getChargingPads ? simulation.warehouse.getChargingPads().length : 0;
    const shelvesCount = simulation.warehouse.shelves ? simulation.warehouse.shelves.length : 0;

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <div style={sectionHeader}>WAREHOUSE</div>
                <button
                    style={{
                        padding: '3px 8px', background: '#0f3460', color: '#00c8ff',
                        border: '1px solid #00c8ff', borderRadius: 3, cursor: 'pointer', fontSize: 10, fontFamily: 'inherit'
                    }}
                    onClick={onOpenBuilder}
                >
                    ⚙ Build
                </button>
            </div>
            {(deliveryDocks > 0 || chargingPads > 0 || shelvesCount > 0) && (
                <div style={{ fontSize: 10, color: '#00c8ff', background: 'rgba(0,200,255,0.06)', padding: 6, borderRadius: 3, marginBottom: 8 }}>
                    <div>📍 Left: {deliveryDocks} Delivery Dock(s)</div>
                    <div>⚡ Right: {chargingPads} Charging Bay(s)</div>
                    <div>📦 Center: {shelvesCount} Storage Rack(s)</div>
                </div>
            )}
            <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                <label style={{ fontSize: 12 }}>W
                    <input
                        type="number"
                        style={inputStyle}
                        value={draftW}
                        min={MIN_DIM}
                        max={MAX_DIM}
                        onChange={(e) => setDraftW(e.target.value)}
                    />
                </label>
                <label style={{ fontSize: 12 }}>H
                    <input
                        type="number"
                        style={inputStyle}
                        value={draftH}
                        min={MIN_DIM}
                        max={MAX_DIM}
                        onChange={(e) => setDraftH(e.target.value)}
                    />
                </label>
                <button style={buttonStyle('apply')} onClick={applyDimensions}>Apply</button>
            </div>
            {dimError && <div style={{ color: '#e94560', fontSize: 11, marginBottom: 6 }}>{dimError}</div>}
            <div style={{ fontSize: 11, color: '#888' }}>
                Current: {simulation.width} × {simulation.height} m
            </div>

            <div style={{ ...sectionHeader, marginTop: 10 }}>OBSTACLES</div>
            {simulation.obstacles.length === 0 && (
                <div style={{ fontSize: 11, color: '#666' }}>No obstacles yet</div>
            )}
            {simulation.obstacles.map((o) => (
                <div
                    key={o.id}
                    onClick={() => { simulation.selectedObstacleId = o.id; onMutate(); }}
                    style={{
                        padding: '3px 8px', cursor: 'pointer', borderRadius: 3, fontSize: 11, marginBottom: 2,
                        background: simulation.selectedObstacleId === o.id ? '#0f3460' : 'transparent',
                        borderLeft: simulation.selectedObstacleId === o.id ? '3px solid #e94560' : '3px solid transparent',
                        display: 'flex', justifyContent: 'space-between'
                    }}
                >
                    <span>{o.id} ({o.x.toFixed(1)},{o.y.toFixed(1)}) {o.width.toFixed(1)}×{o.height.toFixed(1)}</span>
                    <span
                        style={{ color: '#e94560', cursor: 'pointer' }}
                        onClick={(e) => {
                            e.stopPropagation();
                            simulation.removeObstacle(o.id);
                            onMutate();
                        }}
                    >
                        ✕
                    </span>
                </div>
            ))}

            <div style={{ ...sectionHeader, marginTop: 10 }}>CONTROLS</div>
            <button style={modeButton(mode === 'default')} onClick={() => setMode('default')}>Navigate</button>
            <button style={modeButton(mode === 'move-robot')} onClick={() => setMode('move-robot')}>Move Robot</button>
            <button style={modeButton(mode === 'add-obstacle')} onClick={() => setMode('add-obstacle')}>Add Obstacle</button>
            <button style={modeButton(mode === 'move-obstacle')} onClick={() => setMode('move-obstacle')}>Move Obstacle</button>
            {mode === 'add-obstacle' && <div style={hintStyle}>Drag on the canvas to draw an obstacle</div>}
            {mode === 'move-obstacle' && <div style={hintStyle}>Drag an obstacle on the canvas</div>}
        </div>
    );
}

const inputStyle = {
    width: 46,
    marginLeft: 4,
    padding: '2px 4px',
    background: '#0a0a1a',
    color: '#e0e0e0',
    border: '1px solid #0f3460',
    borderRadius: 3,
    fontSize: 12,
    fontFamily: 'inherit'
};

function buttonStyle(_kind) {
    return {
        padding: '4px 10px',
        background: '#16213e',
        color: '#e0e0e0',
        border: '1px solid #e94560',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 12,
        fontFamily: 'inherit'
    };
}

function modeButton(active) {
    return {
        marginBottom: 4,
        width: '100%',
        padding: '5px 8px',
        background: active ? '#e94560' : '#0f3460',
        color: '#e0e0e0',
        border: 'none',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 12,
        textAlign: 'left',
        fontFamily: 'inherit'
    };
}

const hintStyle = {
    fontSize: 11,
    color: '#ffd43b',
    marginTop: 4,
    marginBottom: 4
};