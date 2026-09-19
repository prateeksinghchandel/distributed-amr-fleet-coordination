import { useMemo, useState } from 'react';
import { useTheme } from '../theme/ThemeContext.jsx';
import { ui } from '../theme/ui.js';

export const LOG_CATEGORIES = ['ALL', 'TASKS', 'AUCTIONS', 'ROBOTS', 'SYSTEM', 'WARNINGS'];

/** Heuristic classifier for categorizing structured + legacy log lines. */
export function categorizeLog(message) {
    const s = String(message || '');
    const lower = s.toLowerCase();
    if (/\[auction\]|auction|bid\b|winner|committed for/.test(lower)) return 'AUCTIONS';
    if (/\[zenoh\]|connected|disconnected|world state|server|fleet manager/.test(lower)) return 'SYSTEM';
    if (/task\b|pickup|dropoff|deliver|create|assigned|cancelled|completed|auctioned/.test(lower)) return 'TASKS';
    if (/\bamr\d+\b|robot|telemetry|battery|charging|blocked/.test(lower)) return 'ROBOTS';
    if (/warn|error|fail|crashed|no winner|conflict|reject|retry|⚠/.test(lower)) return 'WARNINGS';
    return 'SYSTEM';
}

const CATEGORY_COLOR = (P) => ({
    ALL: P.text,
    TASKS: P.info,
    AUCTIONS: P.accent,
    ROBOTS: P.success,
    SYSTEM: P.textMuted,
    WARNINGS: P.warning,
});

export default function EventLog({ fleet, height = 190 }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [filter, setFilter] = useState('ALL');

    const entries = useMemo(() => {
        const rows = fleet.logs.slice(-300);
        if (filter === 'ALL') return rows;
        return rows.filter((e) => categorizeLog(e.message) === filter);
    }, [fleet.logs, filter]);

    const colorOf = CATEGORY_COLOR(P);

    return (
        <div
            style={{
                height,
                minHeight: 120,
                background: P.surface,
                borderTop: `1px solid ${P.border}`,
                display: 'flex',
                flexDirection: 'column',
                fontFamily: 'monospace',
                color: P.text,
            }}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 12px', borderBottom: `1px solid ${P.border}` }}>
                <span style={S.sectionTitle}>EVENT LOG</span>
                <span style={{ flex: 1 }} />
                {LOG_CATEGORIES.map((cat) => (
                    <button
                        key={cat}
                        onClick={() => setFilter(cat)}
                        style={{
                            ...S.button(filter === cat ? 'primary' : 'default'),
                            ...(filter === cat ? {} : { color: colorOf[cat], borderColor: colorOf[cat] }),
                        }}
                    >
                        {cat}
                    </button>
                ))}
                <button style={{ ...S.button('danger') }} onClick={() => fleet.clearLogs()}>
                    CLEAR
                </button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '6px 12px', fontSize: 11, lineHeight: 1.7 }}>
                {entries.length === 0 && (
                    <div style={{ color: P.textDim }}>No events{filter !== 'ALL' ? ` in ${filter}` : ''}.</div>
                )}
                {entries.map((e, i) => {
                    const cat = categorizeLog(e.message);
                    const color = cat === 'WARNINGS'
                        ? (/error|fail|crashed|rejected|no winner|conflict/.test(e.message) ? P.logErr : P.logWarn)
                        : colorOf[cat];
                    return (
                        <div key={`${e.time}-${i}`} style={{ display: 'flex', gap: 8 }}>
                            <span style={{ color: P.textDim, flex: '0 0 64px' }}>{e.time}</span>
                            <span style={{ color, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{e.message}</span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}