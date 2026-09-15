import React, { useState } from 'react';
import { WAREHOUSE_PRESETS, buildWarehouseConfig } from '../simulation/WarehouseBuilder.js';

export default function WarehouseBuilderModal({ isOpen, onClose, onBuild }) {
    const [presetKey, setPresetKey] = useState('ECOMMERCE');
    const [width, setWidth] = useState(30);
    const [height, setHeight] = useState(20);
    const [deliveryWidth, setDeliveryWidth] = useState(4.5);
    const [deliveryDocks, setDeliveryDocks] = useState(3);
    const [chargingWidth, setChargingWidth] = useState(4.5);
    const [chargingPads, setChargingPads] = useState(3);
    const [shelfRows, setShelfRows] = useState(2);
    const [shelfCols, setShelfCols] = useState(3);
    const [shelfWidth, setShelfWidth] = useState(3.5);
    const [shelfDepth, setShelfDepth] = useState(1.6);
    const [robotCount, setRobotCount] = useState(3);

    if (!isOpen) return null;

    const selectPreset = (key) => {
        setPresetKey(key);
        const p = WAREHOUSE_PRESETS[key];
        if (!p) return;
        setWidth(p.width);
        setHeight(p.height);
        setDeliveryWidth(p.deliveryWidth);
        setDeliveryDocks(p.deliveryDocks);
        setChargingWidth(p.chargingWidth);
        setChargingPads(p.chargingPads);
        setShelfRows(p.shelfRows);
        setShelfCols(p.shelfCols);
        setShelfWidth(p.shelfWidth);
        setShelfDepth(p.shelfDepth);
        setRobotCount(p.robotCount);
    };

    const handleBuild = () => {
        const config = buildWarehouseConfig({
            width: Number(width),
            height: Number(height),
            deliveryWidth: Number(deliveryWidth),
            deliveryDocks: Number(deliveryDocks),
            chargingWidth: Number(chargingWidth),
            chargingPads: Number(chargingPads),
            shelfRows: Number(shelfRows),
            shelfCols: Number(shelfCols),
            shelfWidth: Number(shelfWidth),
            shelfDepth: Number(shelfDepth),
            robotCount: Number(robotCount),
        });
        onBuild(config);
        onClose();
    };

    const totalShelves = shelfRows * shelfCols;

    return (
        <div style={overlayStyle}>
            <div style={modalStyle}>
                {/* Header */}
                <div style={headerStyle}>
                    <div>
                        <h3 style={{ margin: 0, color: '#e94560', fontSize: 16 }}>BUILD NEW WAREHOUSE</h3>
                        <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>
                            Configure dimensions for warehouse, delivery docks, charging bays, and storage shelves
                        </div>
                    </div>
                    <button style={closeButtonStyle} onClick={onClose}>✕</button>
                </div>

                {/* Presets */}
                <div style={{ marginBottom: 16 }}>
                    <div style={labelStyle}>LAYOUT PRESETS</div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        {Object.entries(WAREHOUSE_PRESETS).map(([key, p]) => (
                            <button
                                key={key}
                                style={presetButtonStyle(presetKey === key)}
                                onClick={() => selectPreset(key)}
                            >
                                <div style={{ fontWeight: 'bold' }}>{p.name}</div>
                                <div style={{ fontSize: 10, color: '#aaa', marginTop: 2 }}>
                                    {p.width}×{p.height}m · {p.robotCount} AMRs
                                </div>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Parameters Grid */}
                <div style={gridContainerStyle}>
                    {/* Warehouse Dimensions */}
                    <div style={cardStyle}>
                        <div style={sectionTitleStyle}>1. WAREHOUSE BOUNDS</div>
                        <div style={rowStyle}>
                            <label style={inputLabelStyle}>Width (m)
                                <input
                                    type="number"
                                    min="12"
                                    max="100"
                                    value={width}
                                    onChange={(e) => { setWidth(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                            <label style={inputLabelStyle}>Height (m)
                                <input
                                    type="number"
                                    min="10"
                                    max="100"
                                    value={height}
                                    onChange={(e) => { setHeight(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                        </div>
                    </div>

                    {/* Delivery Area */}
                    <div style={cardStyle}>
                        <div style={sectionTitleStyle}>2. DELIVERY STATIONS (LEFT)</div>
                        <div style={rowStyle}>
                            <label style={inputLabelStyle}>Zone Width (m)
                                <input
                                    type="number"
                                    min="2"
                                    max="15"
                                    step="0.5"
                                    value={deliveryWidth}
                                    onChange={(e) => { setDeliveryWidth(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                            <label style={inputLabelStyle}>Docks Count
                                <input
                                    type="number"
                                    min="1"
                                    max="8"
                                    value={deliveryDocks}
                                    onChange={(e) => { setDeliveryDocks(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                        </div>
                    </div>

                    {/* AMR Charging Area */}
                    <div style={cardStyle}>
                        <div style={sectionTitleStyle}>3. AMR CHARGING HUB (RIGHT)</div>
                        <div style={rowStyle}>
                            <label style={inputLabelStyle}>Zone Width (m)
                                <input
                                    type="number"
                                    min="2"
                                    max="15"
                                    step="0.5"
                                    value={chargingWidth}
                                    onChange={(e) => { setChargingWidth(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                            <label style={inputLabelStyle}>Charging Bays
                                <input
                                    type="number"
                                    min="1"
                                    max="12"
                                    value={chargingPads}
                                    onChange={(e) => { setChargingPads(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                        </div>
                    </div>

                    {/* Storage Shelves */}
                    <div style={cardStyle}>
                        <div style={sectionTitleStyle}>4. STORAGE SHELVES (CENTER)</div>
                        <div style={rowStyle}>
                            <label style={inputLabelStyle}>Shelf Rows
                                <input
                                    type="number"
                                    min="1"
                                    max="6"
                                    value={shelfRows}
                                    onChange={(e) => { setShelfRows(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                            <label style={inputLabelStyle}>Shelf Cols
                                <input
                                    type="number"
                                    min="1"
                                    max="8"
                                    value={shelfCols}
                                    onChange={(e) => { setShelfCols(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                        </div>
                        <div style={{ ...rowStyle, marginTop: 6 }}>
                            <label style={inputLabelStyle}>Rack W (m)
                                <input
                                    type="number"
                                    min="1.5"
                                    max="8"
                                    step="0.5"
                                    value={shelfWidth}
                                    onChange={(e) => { setShelfWidth(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                            <label style={inputLabelStyle}>Rack Depth (m)
                                <input
                                    type="number"
                                    min="0.8"
                                    max="4"
                                    step="0.2"
                                    value={shelfDepth}
                                    onChange={(e) => { setShelfDepth(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                        </div>
                    </div>

                    {/* AMR Fleet */}
                    <div style={{ ...cardStyle, gridColumn: 'span 2' }}>
                        <div style={sectionTitleStyle}>5. AMR ROBOT FLEET</div>
                        <div style={rowStyle}>
                            <label style={inputLabelStyle}>Number of AMRs
                                <input
                                    type="number"
                                    min="1"
                                    max={chargingPads}
                                    value={robotCount}
                                    onChange={(e) => { setRobotCount(e.target.value); setPresetKey('CUSTOM'); }}
                                    style={inputStyle}
                                />
                            </label>
                            <div style={{ fontSize: 11, color: '#aaa', display: 'flex', alignItems: 'center' }}>
                                Robots spawn directly on charging bays and recharge when idle.
                            </div>
                        </div>
                    </div>
                </div>

                {/* Summary Banner */}
                <div style={summaryBannerStyle}>
                    <span>Warehouse: <b>{width}×{height}m</b></span>
                    <span>·</span>
                    <span>Delivery Docks: <b>{deliveryDocks}</b></span>
                    <span>·</span>
                    <span>Charging Bays: <b>{chargingPads}</b></span>
                    <span>·</span>
                    <span>Storage Shelves: <b>{totalShelves}</b></span>
                    <span>·</span>
                    <span>Active AMRs: <b>{robotCount}</b></span>
                </div>

                {/* Footer Buttons */}
                <div style={footerStyle}>
                    <button style={cancelButtonStyle} onClick={onClose}>Cancel</button>
                    <button style={buildButtonStyle} onClick={handleBuild}>Build Warehouse</button>
                </div>
            </div>
        </div>
    );
}

const overlayStyle = {
    position: 'fixed',
    top: 0,
    left: 0,
    width: '100vw',
    height: '100vh',
    background: 'rgba(5, 5, 15, 0.82)',
    backdropFilter: 'blur(4px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 9999,
    fontFamily: 'monospace',
};

const modalStyle = {
    width: 680,
    maxHeight: '90vh',
    background: '#16213e',
    border: '2px solid #0f3460',
    borderRadius: 8,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    boxShadow: '0 10px 35px rgba(0, 0, 0, 0.6)',
    color: '#e0e0e0',
};

const headerStyle = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    borderBottom: '1px solid #0f3460',
    paddingBottom: 12,
    marginBottom: 14,
};

const closeButtonStyle = {
    background: 'transparent',
    border: 'none',
    color: '#aaa',
    fontSize: 16,
    cursor: 'pointer',
};

const labelStyle = {
    fontSize: 11,
    color: '#888',
    letterSpacing: 1,
    marginBottom: 6,
};

const presetButtonStyle = (active) => ({
    flex: 1,
    padding: '8px 10px',
    background: active ? '#0f3460' : '#0a0a1a',
    border: active ? '2px solid #00c8ff' : '1px solid #0f3460',
    borderRadius: 6,
    cursor: 'pointer',
    color: active ? '#ffffff' : '#aaa',
    textAlign: 'left',
    fontSize: 11,
    fontFamily: 'inherit',
});

const gridContainerStyle = {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 10,
    overflowY: 'auto',
    maxHeight: '52vh',
    paddingRight: 4,
};

const cardStyle = {
    background: '#0a0a1a',
    border: '1px solid #0f3460',
    borderRadius: 6,
    padding: 10,
};

const sectionTitleStyle = {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#00c8ff',
    letterSpacing: 0.8,
    marginBottom: 8,
};

const rowStyle = {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
};

const inputLabelStyle = {
    fontSize: 11,
    color: '#ccc',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    flex: 1,
};

const inputStyle = {
    background: '#16213e',
    border: '1px solid #0f3460',
    borderRadius: 4,
    color: '#ffffff',
    padding: '4px 8px',
    fontFamily: 'inherit',
    fontSize: 12,
};

const summaryBannerStyle = {
    background: 'rgba(0, 200, 255, 0.08)',
    border: '1px solid rgba(0, 200, 255, 0.3)',
    borderRadius: 4,
    padding: '8px 12px',
    fontSize: 11,
    color: '#00c8ff',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 14,
};

const footerStyle = {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: 10,
    borderTop: '1px solid #0f3460',
    paddingTop: 14,
    marginTop: 14,
};

const cancelButtonStyle = {
    padding: '6px 14px',
    background: '#0a0a1a',
    border: '1px solid #0f3460',
    color: '#aaa',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: 'inherit',
};

const buildButtonStyle = {
    padding: '6px 16px',
    background: '#e94560',
    border: '1px solid #e94560',
    color: '#ffffff',
    fontWeight: 'bold',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: 'inherit',
    boxShadow: '0 2px 10px rgba(233, 69, 96, 0.4)',
};
