import React, { useState } from 'react';

const sectionHeader = { fontSize: 11, color: '#888', marginBottom: 6, letterSpacing: 1 };

export default function WarehousePanel({ fleet }) {
    const warehouse = fleet.warehouse;
    const [dimError] = useState(null);

    if (!warehouse) {
        return (
            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <div style={sectionHeader}>WAREHOUSE</div>
                <div style={{ fontSize: 11, color: '#666' }}>
                    Waiting for world/state from the coordinator…
                </div>
            </div>
        );
    }

    const deliveryDocks = warehouse.deliveryDocks ? warehouse.deliveryDocks.length : 0;
    const chargingPads = warehouse.chargingPads ? warehouse.chargingPads.length : 0;
    const shelvesCount = warehouse.shelves ? warehouse.shelves.length : 0;

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <div style={sectionHeader}>WAREHOUSE</div>
                <button
                    style={{
                        padding: '3px 8px', background: '#0f3460', color: '#00c8ff',
                        border: '1px solid #00c8ff', borderRadius: 3, cursor: 'not-allowed',
                        fontSize: 10, fontFamily: 'inherit', opacity: 0.45
                    }}
                    disabled
                    title="Warehouse layout is published by the backend — read-only"
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
            <div style={{ fontSize: 11, color: '#888' }}>
                Current: {warehouse.width} × {warehouse.height} m
            </div>
            <div style={{ fontSize: 10, color: '#666', marginBottom: 6 }}>
                Layout comes live from the Python coordinator (world/state) — editing is disabled in the dashboard.
            </div>

            <div style={{ ...sectionHeader, marginTop: 10 }}>OBSTACLES</div>
            {warehouse.obstacles.length === 0 && (
                <div style={{ fontSize: 11, color: '#666' }}>No obstacles yet</div>
            )}
            {warehouse.obstacles.map((o) => (
                <div
                    key={o.id}
                    style={{
                        padding: '3px 8px', borderRadius: 3, fontSize: 11, marginBottom: 2,
                        background: 'transparent',
                        borderLeft: '3px solid transparent',
                        display: 'flex', justifyContent: 'space-between'
                    }}
                >
                    <span>{o.id} ({o.x.toFixed(1)},{o.y.toFixed(1)}) {o.width.toFixed(1)}×{o.height.toFixed(1)}</span>
                    {o.type && <span style={{ color: '#ffa94d' }}>{o.type}</span>}
                </div>
            ))}
            {dimError && <div style={{ color: '#e94560', fontSize: 11, marginBottom: 6 }}>{dimError}</div>}
        </div>
    );
}