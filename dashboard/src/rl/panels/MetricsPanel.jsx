import React from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { fmt, fmtInt } from '../format.js';

const COMPONENT_NAMES = {
    progress: 'Progress',
    goal: 'Goal',
    collision: 'Collision',
    danger: 'Danger',
    stopping: 'Stopping',
    path_deviation: 'Path dev.',
    oscillation: 'Oscillation',
};

export default function MetricsPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const m = rl.metrics || {};
    const snap = rl.snapshot || {};

    const rows = [
        ['Episodes', fmtInt(m.episodes)],
        ['Total steps', fmtInt(m.total_steps)],
        ['Avg reward', fmt(m.avg_reward)],
        ['Success rate', m.success_rate != null ? `${fmt(m.success_rate, 1)}%` : '—'],
        ['Collision rate', m.collision_rate != null ? `${fmt(m.collision_rate, 1)}%` : '—'],
        ['Near-collision', m.near_collision_rate != null ? `${fmt(m.near_collision_rate, 1)}%` : '—'],
        ['Avg episode time', m.avg_time != null ? `${fmt(m.avg_time)}s` : '—'],
        ['Avg goal time', m.avg_goal_time != null ? `${fmt(m.avg_goal_time)}s` : '—'],
        ['Avg distance', m.avg_distance != null ? `${fmt(m.avg_distance)}m` : '—'],
        ['Avg length', fmtInt(m.avg_length)],
    ];

    const comps = snap.reward_components || m.components || {};
    const compEntries = Object.entries(COMPONENT_NAMES)
        .filter(([k]) => k in comps)
        .map(([k, label]) => ({ k, label, v: comps[k] }));

    const barFill = (v) => {
        if (v > 0) return P.success;
        if (v < -5) return P.danger;
        if (v < 0) return P.warning;
        return P.textMuted;
    };
    const maxAbs = Math.max(1, ...compEntries.map((c) => Math.abs(c.v)));

    return (
        <div>
            <div style={S.panelHeader}>
                <span style={S.sectionTitle}>Metrics</span>
                {rl.status && (
                    <span style={{ ...S.dim, fontSize: 10 }}>
                        steps/s {rl.status.steps_per_second === 'max' ? 'MAX' : rl.status.steps_per_second}
                    </span>
                )}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginBottom: 10 }}>
                {rows.map(([label, value]) => (
                    <div key={label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, lineHeight: 1.6 }}>
                        <span style={S.dim}>{label}</span>
                        <span style={{ color: P.text, fontWeight: 'bold' }}>{value}</span>
                    </div>
                ))}
            </div>

            <div style={{ ...S.label }}>Reward components (last step)</div>
            {compEntries.length === 0 ? (
                <div style={{ ...S.dim, fontSize: 11 }}>No reward data yet — start training or step.</div>
            ) : (
                compEntries.map((c) => (
                    <div key={c.k} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                        <span style={{ ...S.dim, width: 72, fontSize: 10, flex: '0 0 auto' }}>{c.label}</span>
                        <div style={{ flex: 1, height: 6, background: P.surfaceElevated, borderRadius: 3, overflow: 'hidden' }}>
                            <div style={{
                                width: `${Math.min(100, (Math.abs(c.v) / maxAbs) * 100)}%`,
                                height: '100%',
                                background: barFill(c.v),
                                marginLeft: c.v < 0 ? `${(Math.abs(c.v) / maxAbs) * 50}%` : 0,
                                float: c.v < 0 ? 'right' : 'left',
                            }} />
                        </div>
                        <span style={{ width: 52, textAlign: 'right', fontSize: 10, color: P.text }}>{fmt(c.v, 1)}</span>
                    </div>
                ))
            )}

            {snap.reward != null && (
                <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                    <span style={S.dim}>Step {snap.step}/{snap.max_steps} (t={fmt(snap.time)}s)</span>
                    <span style={{ color: P.accent, fontWeight: 'bold' }}>reward {fmt(snap.reward)}</span>
                </div>
            )}
        </div>
    );
}