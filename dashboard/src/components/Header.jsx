import React from 'react';
import { CONNECTION_STATUS } from '../distributed/ConnectionManager.js';

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

const STATUS_STYLES = {
    [CONNECTION_STATUS.CONNECTED]: { color: '#00ff88', label: 'CONNECTED' },
    [CONNECTION_STATUS.CONNECTING]: { color: '#ffd43b', label: 'CONNECTING' },
    [CONNECTION_STATUS.ERROR]: { color: '#e94560', label: 'ERROR' },
    [CONNECTION_STATUS.DISCONNECTED]: { color: '#aaaaaa', label: 'DISCONNECTED' },
};

export default function Header({ fleet, onResetCamera }) {
    const conn = fleet.connection;
    const style = STATUS_STYLES[conn.status] || STATUS_STYLES[CONNECTION_STATUS.DISCONNECTED];
    const robotsOnline = fleet.robotsList.filter((r) => r.online).length;

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
                AMR FLEET — DISTRIBUTED
            </div>
            <span
                title={conn.detail || ''}
                style={{
                    fontSize: 11,
                    color: style.color,
                    border: `1px solid ${style.color}`,
                    borderRadius: 3,
                    padding: '2px 8px',
                    cursor: 'default'
                }}
            >
                ZENOH {style.label}
            </span>
            <div style={{ fontSize: 12, color: '#aaa' }}>
                {robotsOnline}/{fleet.robotsList.length} robots online · {fleet.tasksList.length} tasks
            </div>
            <div style={{ flex: 1 }} />
            {(conn.status === CONNECTION_STATUS.ERROR || conn.status === CONNECTION_STATUS.DISCONNECTED) && (
                <button
                    style={{ ...buttonStyle, borderColor: '#00ff88', color: '#00ff88', fontWeight: 'bold' }}
                    onClick={() => fleet.retryConnection()}
                >
                    Reconnect
                </button>
            )}
            <button
                style={{ ...buttonStyle, opacity: 0.45, cursor: 'not-allowed' }}
                title="View control only — backend is authoritative"
                disabled
            >
                ⚙ Build Warehouse
            </button>
            <button style={buttonStyle} onClick={onResetCamera}>
                Reset View
            </button>
        </div>
    );
}