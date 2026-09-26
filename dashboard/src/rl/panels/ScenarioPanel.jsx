import React, { useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { fmtInt } from '../format.js';

const PRESET_LABELS = {
    simple: 'Simple',
    obstacle_avoidance: 'Obstacle Avoidance',
    two_robot: 'Two Robot',
    intersection: 'Intersection',
    narrow_corridor: 'Narrow Corridor',
    chokepoint: 'Chokepoint',
    dense_traffic: 'Dense Traffic',
    random: 'Random',
};

const OBSTACLE_MODES = ['none', 'random', 'corridor', 'quadrant', 'racked'];
const SPAWN_MODES = ['edges', 'corners', 'opposite_edges', 'random'];
const GOAL_MODES = ['opposite', 'center', 'random'];

export default function ScenarioPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [preset, setPreset] = useState('simple');
    const [level, setLevel] = useState(null);
    const [showOverrides, setShowOverrides] = useState(false);
    const [overrides, setOverrides] = useState({});
    const [pending, setPending] = useState(false);

    const scenario = (rl.status && rl.status.scenario) || {};
    const curriculum = (rl.scenarios && rl.scenarios.curriculum) || {};
    const autoSaving = (rl.status && rl.status.autosave_every) > 0;

    const applyPreset = async (name) => {
        setPreset(name);
        setPending(true);
        try {
            await rl.send('change_scenario', { scenario: name, overrides: normalizedOverrides() });
        } catch { /* error surfaced by hook */ }
        setPending(false);
    };

    const applyLevel = async (lv) => {
        setLevel(lv);
        setPending(true);
        try {
            await rl.send('change_scenario', { level: lv, overrides: normalizedOverrides() });
        } catch { /* error surfaced by hook */ }
        setPending(false);
    };

    const applyOverrides = async () => {
        setPending(true);
        try {
            await rl.send('change_scenario', {
                scenario: preset,
                level: level || undefined,
                overrides: normalizedOverrides(),
            });
        } catch { /* error surfaced by hook */ }
        setPending(false);
    };

    const normalizedOverrides = () => {
        const o = {};
        for (const [k, v] of Object.entries(overrides)) {
            const sv = String(v).trim();
            if (sv === '') continue;
            if (k === 'dynamic_obstacles') o[k] = sv === 'true';
            else if (k === 'n_robots' || k === 'n_rl' || k === 'max_steps' || k === 'seed') o[k] = parseInt(sv, 10);
            else if (k === 'width' || k === 'height' || k === 'obstacle_density') o[k] = parseFloat(sv);
            else o[k] = sv;
        }
        return o;
    };

    const set = (k, v) => setOverrides((prev) => ({ ...prev, [k]: v }));

    const numberRows = [
        ['width', 'Width (m)'],
        ['height', 'Height (m)'],
        ['n_robots', 'Robots'],
        ['n_rl', 'RL agents'],
        ['obstacle_density', 'Obstacle density'],
        ['max_steps', 'Max steps'],
        ['seed', 'Seed (resets scene)'],
    ];

    const selectRows = [
        ['obstacle_mode', OBSTACLE_MODES],
        ['spawn_mode', SPAWN_MODES],
        ['goal_mode', GOAL_MODES],
    ];

    return (
        <div>
            <div style={S.panelHeader}>
                <span style={S.sectionTitle}>Scenario</span>
                {scenario.name && (
                    <span style={{ ...S.chip(P.info), fontSize: 10 }}>
                        {scenario.name} · d{scenario.difficulty} · n={scenario.n_robots}
                    </span>
                )}
            </div>

            <div style={{ fontSize: 10, color: P.textDim, lineHeight: 1.5, marginBottom: 8 }}>
                Learned weights persist across scenario switches — only the shared rollout buffer resets
                (full {rl.status && fmtInt(rl.status.rollout_steps)}-step rollouts count toward PPO updates),
                and auto-save {autoSaving ? 'keeps writing autosave.pt while training' : 'is off (server started with --save-every 0)'}.
            </div>

            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                {Object.entries(PRESET_LABELS).map(([key, label]) => (
                    <button
                        key={key}
                        onClick={() => { setLevel(undefined); applyPreset(key); }}
                        disabled={pending}
                        style={{
                            ...S.button('default'),
                            padding: '2px 8px',
                            fontSize: 10,
                            background: preset === key && !showOverrides ? P.accent : P.surface,
                            color: preset === key && !showOverrides ? P.accentContrast : P.text,
                            borderColor: preset === key && !showOverrides ? P.accent : P.borderStrong,
                            opacity: pending ? 0.5 : 1,
                        }}
                    >
                        {label}
                    </button>
                ))}
            </div>

            <div style={{ ...S.label }}>Curriculum level</div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
                {Object.keys(curriculum).map((lv) => (
                    <button
                        key={lv}
                        onClick={() => { setPreset(undefined); applyLevel(parseInt(lv, 10)); }}
                        disabled={pending}
                        style={{
                            ...S.button('default'),
                            flex: 1,
                            padding: '2px 0',
                            fontSize: 10,
                            background: level === parseInt(lv, 10) ? P.accent : P.surface,
                            color: level === parseInt(lv, 10) ? P.accentContrast : P.text,
                            borderColor: level === parseInt(lv, 10) ? P.accent : P.borderStrong,
                            opacity: pending ? 0.5 : 1,
                        }}
                    >
                        {lv}
                    </button>
                ))}
            </div>

            <button
                onClick={() => setShowOverrides((v) => !v)}
                style={{ ...S.button('ghost'), width: '100%', fontSize: 11, justifyContent: 'center' }}
            >
                {showOverrides ? 'HIDE OVERRIDES ▾' : 'OVERRIDES ▸'}
            </button>

            {showOverrides && (
                <div style={{ marginTop: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                        {numberRows.map(([key, label]) => (
                            <label key={key} style={{ display: 'flex', flexDirection: 'column' }}>
                                <span style={S.label}>{label}</span>
                                <input
                                    type="number"
                                    step="any"
                                    placeholder={scenario[key] ?? ''}
                                    value={overrides[key] ?? ''}
                                    onChange={(e) => set(key, e.target.value)}
                                    style={S.input}
                                />
                            </label>
                        ))}
                        <label style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={S.label}>Dynamic obstacles</span>
                            <select
                                value={overrides.dynamic_obstacles ?? (scenario.dynamic_obstacles ? 'true' : 'false')}
                                onChange={(e) => set('dynamic_obstacles', e.target.value)}
                                style={S.select}
                            >
                                <option value="true">yes</option>
                                <option value="false">no</option>
                            </select>
                        </label>
                    </div>
                    {selectRows.map(([key, options]) => (
                        <div key={key} style={{ marginTop: 6 }}>
                            <span style={S.label}>{key}</span>
                            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                {options.map((o) => (
                                    <button
                                        key={o}
                                        onClick={() => set(key, o)}
                                        style={{
                                            ...S.button('default'),
                                            padding: '1px 8px',
                                            fontSize: 10,
                                            background: (overrides[key] ?? scenario[key]) === o ? P.accent : P.surface,
                                            color: (overrides[key] ?? scenario[key]) === o ? P.accentContrast : P.text,
                                        }}
                                    >
                                        {o}
                                    </button>
                                ))}
                            </div>
                        </div>
                    ))}
                    <button
                        onClick={applyOverrides}
                        disabled={pending}
                        style={{ ...S.button('primary'), width: '100%', marginTop: 8, justifyContent: 'center' }}
                    >
                        {pending ? 'BUILDING SCENE…' : 'APPLY OVERRIDES + REBUILD'}
                    </button>
                </div>
            )}
        </div>
    );
}