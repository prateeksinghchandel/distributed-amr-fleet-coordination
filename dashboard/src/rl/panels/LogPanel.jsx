import React from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';

export default function LogPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const logs = rl.logs || [];

    return (
        <div>
            <div style={{ ...S.panelHeader, marginBottom: 4 }}>
                <span style={S.sectionTitle}>Trainer log</span>
                <span style={{ ...S.dim, fontSize: 10 }}>{logs.length} lines</span>
            </div>
            {logs.length === 0 ? (
                <div style={{ ...S.dim, fontSize: 11 }}>No log output yet — server events appear here (trainer thread, evaluations, checkpoints, errors).</div>
            ) : (
                <div style={{
                    fontFamily: 'monospace',
                    fontSize: 10,
                    lineHeight: 1.5,
                    background: P.canvasBg,
                    border: `1px solid ${P.border}`,
                    borderRadius: 4,
                    padding: 6,
                    maxHeight: 220,
                    overflowY: 'auto',
                }}>
                    {logs.map((l, i) => {
                        const upper = String(l.level || l.message || '').toUpperCase();
                        const color = upper.includes('ERROR') ? P.logErr
                            : upper.includes('WARN') || upper.includes('WARNING') ? P.logWarn
                            : P.textMuted;
                        return (
                            <div key={i} style={{ color }}>
                                <span style={{ color: P.textDim }}>[{l.ts || '--:--:--'}]</span> {l.message}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}