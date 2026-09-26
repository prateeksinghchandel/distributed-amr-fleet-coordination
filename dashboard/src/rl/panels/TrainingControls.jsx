import React, { useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { withAlpha } from '../../theme/palette.js';

const SPEEDS = [0.25, 0.5, 1, 2, 5, 10];
const STATE_COLOR = {
    IDLE: null,
    TRAINING: '#00ff88',
    PAUSED: '#ffd43b',
    EVALUATING: '#00c8ff',
    STOPPING: '#ffb703',
    ERROR: '#ff5c74',
};

export default function TrainingControls({ rl, onAction }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [stepCount, setStepCount] = useState('1');
    const status = rl.status || {};
    const state = status.state || 'IDLE';
    const busy = rl.busy;
    const stateColor = STATE_COLOR[state] || P.textMuted;
    const isTraining = state === 'TRAINING';
    const isEvaluating = state === 'EVALUATING';

    const send = (command, args) => {
        if (busy) return;
        if (onAction) onAction(command, args);
        rl.send(command, args).catch(() => {});
    };

    const buttonsDisabled = busy || isEvaluating;

    const btnRow = (items) =>
        items.map((b) => (
            <button
                key={b.label}
                onClick={b.onClick}
                disabled={b.disabled || buttonsDisabled}
                style={{ ...S.button(b.variant), flex: 1, justifyContent: 'center', fontSize: 11, opacity: (b.disabled || buttonsDisabled) ? 0.45 : 1 }}
            >
                {b.label}
            </button>
        ));

    const speedNow = status.speed === 0 ? 'max' : status.speed;

    return (
        <div>
            <div style={{ ...S.panelHeader }}>
                <span style={S.sectionTitle}>Training Controls</span>
                <span
                    style={{
                        ...S.chip(stateColor),
                        animation: isTraining ? 'pulse 1.4s infinite' : undefined,
                    }}
                >
                    {state}{status.detail ? ` · ${status.detail.toUpperCase()}` : ''}
                </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 10 }}>
                {btnRow([
                    { label: isTraining ? 'PAUSE' : 'START', variant: 'primary', onClick: () => (isTraining ? send('pause') : send('start')) },
                    { label: 'RESUME', variant: 'default', onClick: () => send('resume'), disabled: state === 'TRAINING' },
                    { label: 'STEP', onClick: () => send('step', { count: parseInt(stepCount, 10) || 1 }) },
                    { label: 'STEP EPISODE', variant: 'ghost', onClick: () => send('step_episode') },
                ])}
            </div>

            <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                <input
                    type="number"
                    min="1"
                    max="10000"
                    value={stepCount}
                    onChange={(e) => setStepCount(e.target.value)}
                    style={{ ...S.input, width: 52, textAlign: 'center' }}
                    title="Number of steps for the STEP button"
                />
                <button style={{ ...S.button('default'), flex: 1 }} onClick={() => send('reset_episode')} disabled={buttonsDisabled}>
                    RESET EPISODE
                </button>
                <button style={{ ...S.button('danger'), flex: 1 }} onClick={() => send('reset_training')} disabled={buttonsDisabled}>
                    RESET TRAINING
                </button>
            </div>

            <div style={{ ...S.label, marginTop: 6 }}>Simulation speed</div>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
                {SPEEDS.map((s) => (
                    <button
                        key={s}
                        onClick={() => send('set_speed', { speed: s })}
                        style={{
                            ...S.button('default'),
                            padding: '2px 8px',
                            fontSize: 10,
                            color: speedNow === s ? P.accentContrast : P.text,
                            background: speedNow === s ? P.accent : P.surface,
                            borderColor: speedNow === s ? P.accent : P.borderStrong,
                        }}
                    >
                        {s}x
                    </button>
                ))}
                <button
                    onClick={() => send('set_speed', { speed: 0 })}
                    style={{
                        ...S.button('default'),
                        padding: '2px 8px',
                        fontSize: 10,
                        color: speedNow === 'max' ? P.accentContrast : P.text,
                        background: speedNow === 'max' ? P.accent : P.surface,
                        borderColor: speedNow === 'max' ? P.accent : P.borderStrong,
                    }}
                >
                    MAX
                </button>
                <span style={{ ...S.dim, fontSize: 11, alignSelf: 'center' }}>
                    {status.steps_per_second === 'max' ? 'as fast as possible, sparser snapshots' : `≈ ${status.steps_per_second} steps/s`}
                </span>
            </div>

            {status.error && (
                <div style={{
                    background: withAlpha(P.danger, 0.15),
                    border: `1px solid ${P.danger}`,
                    borderRadius: 4,
                    padding: 6,
                    fontSize: 11,
                    color: P.danger,
                    marginTop: 6,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                }}>
                    {status.error}
                </div>
            )}

            {isEvaluating && (
                <div style={{
                    background: withAlpha(P.info, 0.15),
                    border: `1px solid ${P.info}`,
                    borderRadius: 4,
                    padding: 6,
                    fontSize: 11,
                    color: P.info,
                    marginTop: 6,
                }}>
                    Evaluation in progress — run a seed over the checkpoint and algorithm. The trained model is not touched by evaluations.
                </div>
            )}
        </div>
    );
}