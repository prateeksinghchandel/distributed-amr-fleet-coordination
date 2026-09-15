import React from 'react';
import DebugPanel from './DebugPanel.jsx';

export default function BottomPanel({ logs, debug, fleet }) {
    return (
        <div style={{
            height: 180,
            borderTop: '2px solid #0f3460',
            background: '#16213e',
            display: 'flex',
            fontFamily: 'monospace',
            color: '#e0e0e0',
            minHeight: 0
        }}>
            <div style={{ flex: 1, overflowY: 'auto', padding: 10 }}>
                <div style={{ color: '#e94560', fontWeight: 'bold', marginBottom: 6, letterSpacing: 1 }}>
                    EVENT LOG
                </div>
                {logs.slice().reverse().map((log, i) => (
                    <div key={`${log}-${i}`} style={{ padding: '1px 0', color: '#aaa', borderBottom: '1px solid #222', wordBreak: 'break-all', fontSize: 11 }}>
                        {log}
                    </div>
                ))}
            </div>
            <div style={{
                width: 320,
                minWidth: 320,
                borderLeft: '1px solid #0f3460',
                padding: 10,
                overflowY: 'auto'
            }}>
                <DebugPanel debug={debug} fleet={fleet} />
            </div>
        </div>
    );
}