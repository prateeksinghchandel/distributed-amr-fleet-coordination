import React, { useEffect, useRef, useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { fmt, fmtInt } from '../format.js';

const DEFAULT_SEEDS = '42 43 44 45 46';

export default function EvaluationPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [controller, setController] = useState('rl');
    const [checkpoint, setCheckpoint] = useState('');
    const [seedsText, setSeedsText] = useState(DEFAULT_SEEDS);
    const state = rl.status ? rl.status.state : 'IDLE';
    const isEvaluating = state === 'EVALUATING';

    // Last recorded evaluation per controller.
    const evals = (rl.evaluations || []).filter((e) => e.summary);
    const rlEval = evals.find((e) => (e.controller || '').toLowerCase() === 'rl') || null;
    const algEval = evals.find((e) => (e.controller || '').toLowerCase() === 'algorithmic') || null;

    const seeds = seedsText.split(/[\s,]+/).map((s) => parseInt(s, 10)).filter((n) => Number.isFinite(n) && n >= 0);
    const numEpisodes = seeds.length || 5;

    const pendingCompareRef = useRef(false);
    const checkpointRef = useRef(checkpoint);
    const numEpisodesRef = useRef(numEpisodes);
    useEffect(() => {
        checkpointRef.current = checkpoint;
        numEpisodesRef.current = numEpisodes;
    }, [checkpoint, numEpisodes]);

    // Chain: algorithmic eval completes -> run the RL eval with the same seeds,
    // so the side-by-side table shows both controllers for every seed.
    useEffect(() => {
        if (!pendingCompareRef.current) return;
        const evalsNow = rl.evaluations || [];
        if (evalsNow.length === 0) return;
        const latest = evalsNow[0];
        if (latest.summary && String(latest.controller || '').toLowerCase() === 'algorithmic') {
            pendingCompareRef.current = false;
            rl.send('evaluate', {
                controller: 'rl',
                checkpoint: checkpointRef.current || undefined,
                num_episodes: numEpisodesRef.current,
            }).catch(() => {});
        }
    }, [rl]);

    const run = (ctrl, ckpt) => {
        rl.send('evaluate', {
            controller: ctrl,
            checkpoint: ckpt || undefined,
            num_episodes: numEpisodes,
        }).catch(() => {});
    };

    const runBoth = () => {
        // Algorithmic first, then RL: results are keyed by seed and aligned by
        // the UI, so a side-by-side table works even if the trainer's
        // last_evaluation is overwritten between runs.
        pendingCompareRef.current = true;
        run('algorithmic');
    };

    const summaryRows = (ev) => [
        ['Controller', ev.controller],
        ['Scenario', ev.scenario],
        ['Seeds', ev.seed ? ev.seed.join(', ') : '—'],
        ['Success rate', ev.summary.success_rate != null ? `${fmt(ev.summary.success_rate, 1)}%` : '—'],
        ['Average time', `${fmt(ev.summary.avg_time)}s`],
        ['Average distance', `${fmt(ev.summary.avg_distance)}m`],
        ['Collisions', fmtInt(ev.summary.total_collisions)],
    ];

    const resultCell = (ev, seed, field) => {
        if (!ev) return null;
        const res = (ev.results || []).find((r) => r.seed === seed);
        if (!res) return null;
        let val;
        let color;
        if (field === 'success') { val = res.success ? '✓' : '✗'; color = res.success ? P.success : P.danger; }
        else if (field === 'time') { val = `${fmt(res.time)}s`; }
        else if (field === 'distance') { val = `${fmt(res.distance)}m`; }
        else if (field === 'collisions') { val = fmtInt(res.collisions); color = res.collisions > 0 ? P.danger : P.success; }
        return (
            <td style={{ color: color || P.text, padding: '2px 6px', textAlign: 'center', fontSize: 11 }}>
                {val}
            </td>
        );
    };

    return (
        <div>
            <div style={{ ...S.panelHeader, marginBottom: 6 }}>
                <span style={S.sectionTitle}>Evaluation</span>
                {isEvaluating && (
                    <span style={{ ...S.chip(P.info), animation: 'pulse 1.4s infinite' }}>EVALUATING…</span>
                )}
            </div>

            <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
                    <span style={S.label}>Controller</span>
                    <div style={{ display: 'flex', gap: 4 }}>
                        {['rl', 'algorithmic'].map((c) => (
                            <button
                                key={c}
                                onClick={() => setController(c)}
                                disabled={isEvaluating}
                                style={{ ...S.tab(controller === c) }}
                            >
                                {c === 'rl' ? 'RL policy' : 'Algorithmic'}
                            </button>
                        ))}
                    </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', minWidth: 150 }}>
                    <span style={S.label}>Checkpoint (RL only)</span>
                    <select
                        value={checkpoint}
                        onChange={(e) => setCheckpoint(e.target.value)}
                        disabled={isEvaluating || controller !== 'rl'}
                        style={S.select}
                    >
                        <option value="">current weights</option>
                        {rl.checkpoints.map((c) => (
                            <option key={c.name} value={c.name}>{c.name}</option>
                        ))}
                    </select>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', minWidth: 170 }}>
                    <span style={S.label}>Seeds (space separated)</span>
                    <input
                        value={seedsText}
                        onChange={(e) => setSeedsText(e.target.value)}
                        disabled={isEvaluating}
                        style={S.input}
                    />
                </div>
                <button
                    onClick={() => run(controller, controller === 'rl' ? checkpoint : undefined)}
                    disabled={isEvaluating || seeds.length === 0}
                    style={{ ...S.button('primary') }}
                >
                    RUN {controller.toUpperCase()}
                </button>
                <button
                    onClick={runBoth}
                    disabled={isEvaluating || seeds.length === 0}
                    style={S.button('default')}
                    title="Runs the algorithmic baseline, then the RL policy, side by side"
                >
                    COMPARE
                </button>
            </div>

            <div style={{ fontSize: 10, color: P.textMuted, marginBottom: 10 }}>
                Deterministic per-seed runs over the current (or selected) scenario — identical seeds reproduce identical warehouses.
                Evaluation pauses training temporarily; model weights are untouched unless a checkpoint is explicitly loaded.
            </div>

            {!algEval && !rlEval ? (
                <div style={{ ...S.dim, fontSize: 11 }}>No evaluations yet.</div>
            ) : (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    {[['Algorithmic', algEval], ['RL', rlEval]].map(([label, ev]) => (
                        <div key={label} style={{
                            flex: '1 1 240px',
                            background: P.surfaceElevated,
                            border: `1px solid ${P.border}`,
                            borderRadius: 4,
                            padding: 8,
                        }}>
                            <div style={{ fontSize: 10, letterSpacing: 1, fontWeight: 'bold', color: P.accent, marginBottom: 4 }}>
                                {label} {ev && ev.controller !== label.toLowerCase() ? '(no run yet)' : ''}
                            </div>
                            {ev ? (
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                                    <tbody>
                                        {summaryRows(ev).map(([k, v]) => (
                                            <tr key={k}>
                                                <td style={{ color: P.textMuted, padding: '1px 4px 1px 0' }}>{k}</td>
                                                <td style={{ color: P.text, textAlign: 'right', fontWeight: 'bold', padding: '1px 0' }}>{v}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            ) : (
                                <div style={{ ...S.dim, fontSize: 11 }}>No result.</div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {(algEval || rlEval) && (
                <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10, fontSize: 10 }}>
                    <thead>
                        <tr style={{ color: P.textMuted, textAlign: 'center' }}>
                            <th style={{ textAlign: 'left', padding: '3px 6px' }}>seed</th>
                            <th style={{ padding: '3px 6px' }}>alg ✓</th>
                            <th style={{ padding: '3px 6px' }}>alg time</th>
                            <th style={{ padding: '3px 6px' }}>alg dist</th>
                            <th style={{ padding: '3px 6px' }}>alg col</th>
                            <th style={{ padding: '3px 6px' }}>rl ✓</th>
                            <th style={{ padding: '3px 6px' }}>rl time</th>
                            <th style={{ padding: '3px 6px' }}>rl dist</th>
                            <th style={{ padding: '3px 6px' }}>rl col</th>
                        </tr>
                    </thead>
                    <tbody>
                        {seeds.map((s) => (
                            <tr key={s} style={{ borderTop: `1px solid ${P.border}` }}>
                                <td style={{ padding: '2px 6px', color: P.text }}>{s}</td>
                                {['success', 'time', 'distance', 'collisions'].map((f) => (
                                    <React.Fragment key={'a' + f}>{resultCell(algEval, s, f)}</React.Fragment>
                                ))}
                                {['success', 'time', 'distance', 'collisions'].map((f) => (
                                    <React.Fragment key={'r' + f}>{resultCell(rlEval, s, f)}</React.Fragment>
                                ))}
                            </tr>
                        ))}
                        {seeds.length === 0 && (
                            <tr><td colSpan={9} style={{ ...S.dim, padding: 6 }}>Enter at least one seed.</td></tr>
                        )}
                    </tbody>
                </table>
            )}
        </div>
    );
}