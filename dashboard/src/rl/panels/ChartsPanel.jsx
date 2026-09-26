import React, { useMemo } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';

function MiniChart({ values, color, height = 90, label }) {
    const { palette: P } = useTheme();
    const W = 280;
    const PAD = 4;

    if (!values || values.length === 0) {
        return (
            <div style={{ fontSize: 11, color: P.textMuted, height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                no data yet
            </div>
        );
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const n = values.length;
    const pts = values.map((v, i) => {
        const x = PAD + (i / Math.max(1, n - 1)) * (W - 2 * PAD);
        const y = PAD + (1 - (v - min) / span) * (height - 2 * PAD);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');

    const lastAvg = values.slice(-20);
    const avg = (lastAvg.reduce((a, b) => a + b, 0) / Math.max(1, lastAvg.length)).toFixed(2);

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                <span style={{ fontSize: 11, color: P.textMuted }}>{label}</span>
                <span style={{ fontSize: 11, color: P.text }}>
                    avg <b style={{ color }}>{avg}</b>
                </span>
            </div>
            <svg width="100%" height={height} viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none">
                <polygon
                    points={`${PAD},${height - PAD} ${pts} ${W - PAD},${height - PAD}`}
                    fill={color}
                    opacity={0.15}
                />
                <polyline
                    points={pts}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.5}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                />
            </svg>
        </div>
    );
}

const TRANSFORMERS = {
    reward: {
        label: 'Episode reward',
        color: (P) => P.accent,
        series: (episodes) => episodes.map((e) => e.reward),
    },
    success_rate: {
        label: 'Success rate (rolling 25)',
        color: (P) => P.success,
        series: (episodes) => rollingRate(episodes.map((e) => e.success), 25),
    },
    collision_rate: {
        label: 'Collision rate (rolling 25)',
        color: (P) => P.danger,
        series: (episodes) => rollingRate(episodes.map((e) => e.collision), 25),
    },
    near_collisions: {
        label: 'Near collisions (rolling 25)',
        color: (P) => P.warning,
        series: (episodes) => rollingMean(episodes.map((e) => e.near_collisions || 0), 25),
    },
    episode_time: {
        label: 'Episode time (s)',
        color: (P) => P.info,
        series: (episodes) => episodes.map((e) => e.time),
    },
    policy_loss: {
        label: 'Policy loss',
        color: (P) => P.info,
        series: (updates) => updates.map((u) => u.policy_loss),
    },
    value_loss: {
        label: 'Value loss',
        color: (P) => P.warning,
        series: (updates) => updates.map((u) => u.value_loss),
    },
    entropy: {
        label: 'Policy entropy',
        color: (P) => P.success,
        series: (updates) => updates.map((u) => u.entropy),
    },
};

function rollingRate(items, window) {
    return items.map((_, i) => {
        const slice = items.slice(Math.max(0, i - window + 1), i + 1);
        return (100 * slice.filter(Boolean).length) / Math.max(1, slice.length);
    });
}

function rollingMean(items, window) {
    return items.map((_, i) => {
        const slice = items.slice(Math.max(0, i - window + 1), i + 1);
        return slice.reduce((a, b) => a + b, 0) / Math.max(1, slice.length);
    });
}

const EPISODE_KEYS = ['reward', 'success_rate', 'collision_rate', 'near_collisions', 'episode_time'];
const UPDATE_KEYS = ['policy_loss', 'value_loss', 'entropy'];

export default function ChartsPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    // Recent data believing oldest-first:
    const episodes = useMemo(() => [...(rl.episodes || [])].reverse(), [rl.episodes]);
    const updates = useMemo(() => [...(rl.updates || [])].reverse(), [rl.updates]);

    const sections = [
        { title: 'Training outcomes', keys: EPISODE_KEYS, source: 'episodes', data: episodes },
        { title: 'Policy updates (PPO)', keys: UPDATE_KEYS, source: 'updates', data: updates },
    ];

    const hasEvals = rl.evaluations && rl.evaluations.length > 0;

    return (
        <div>
            <div style={{ ...S.panelHeader, marginBottom: 6 }}>
                <span style={S.sectionTitle}>Charts</span>
                <span style={{ ...S.dim, fontSize: 10 }}>
                    last {episodes.length} episodes · {updates.length} updates
                    {hasEvals ? ' · evaluations recorded' : ''}
                </span>
            </div>
            {episodes.length === 0 && updates.length === 0 ? (
                <div style={{ ...S.dim, fontSize: 11 }}>
                    No chart data yet. Press START and let the trainer complete a few episodes, or run STEP to collect states.
                </div>
            ) : (
                sections.map((sec) => (
                    <div key={sec.title} style={{ marginBottom: 14 }}>
                        <div style={{ ...S.label }}>{sec.title}</div>
                        {sec.keys.map((k) => {
                            const t = TRANSFORMERS[k];
                            return (
                                <div key={k} style={{ marginBottom: 8 }}>
                                    <MiniChart
                                        values={t.series(sec.data)}
                                        color={t.color(P)}
                                        label={t.label}
                                    />
                                </div>
                            );
                        })}
                    </div>
                ))
            )}
            {rl.snapshot && (
                <div style={{ ...S.dim, fontSize: 10, marginTop: 4 }}>
                    Charts update automatically as episodes finish — you can keep training while inspecting them.
                </div>
            )}
        </div>
    );
}