import React, { useState } from 'react';
import { useTheme } from '../theme/ThemeContext.jsx';
import { ui } from '../theme/ui.js';
import { withAlpha } from '../theme/palette.js';
import RlWarehouseCanvas from './RlWarehouseCanvas.jsx';
import TrainingControls from './panels/TrainingControls.jsx';
import ScenarioPanel from './panels/ScenarioPanel.jsx';
import MetricsPanel from './panels/MetricsPanel.jsx';
import ChartsPanel from './panels/ChartsPanel.jsx';
import DebugPanel from './panels/DebugPanel.jsx';
import EvaluationPanel from './panels/EvaluationPanel.jsx';
import CheckpointPanel from './panels/CheckpointPanel.jsx';
import LogPanel from './panels/LogPanel.jsx';

const BOTTOM_TABS = [
    ['charts', 'Charts'],
    ['debug', 'Agent Debug'],
    ['evaluation', 'Evaluation'],
    ['checkpoints', 'Checkpoints'],
    ['logs', 'Log'],
];

export default function RlTrainingView({ rl, onBack }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [tab, setTab] = useState('charts');
    const [showLidar, setShowLidar] = useState(true);

    const status = rl.status || {};
    const state = status.state;
    const scenario = status.scenario || {};
    const connectionColor = rl.connected ? P.success : P.danger;

    return (
        <div style={{
            height: '100vh',
            width: '100vw',
            display: 'flex',
            flexDirection: 'column',
            background: P.background,
            color: P.text,
            fontFamily: 'monospace',
        }}>
            {/* Header */}
            <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '8px 14px',
                borderBottom: `1px solid ${P.border}`,
                background: P.surface,
            }}>
                <button
                    onClick={onBack}
                    style={{ ...S.button('ghost'), fontSize: 11 }}
                    title="Back to fleet manager"
                >
                    ◀ LIVE FLEET
                </button>
                <span style={{ fontWeight: 'bold', letterSpacing: 1, color: P.accent }}>
                    RL TRAINING · VISUAL
                </span>

                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ ...S.dot(rl.connected ? P.success : P.danger) }} />
                    <span style={{ ...S.chip(connectionColor), fontSize: 9 }}>
                        {rl.connected ? 'SERVER CONNECTED' : 'NO SERVER'}
                    </span>
                </div>

                <span style={{ fontSize: 11, color: P.textMuted }}>
                    {scenario.name || '…'}{scenario.difficulty ? ` · difficulty ${scenario.difficulty}` : ''} · n={scenario.n_robots ?? '…'}
                </span>

                <span style={{ ...S.chip(stateColor(P, state)), fontSize: 10 }}>
                    {state || 'IDLE'}{status.detail ? ` · ${String(status.detail).toUpperCase()}` : ''}
                </span>

                <span style={{ ...S.dim, fontSize: 10 }}>
                    {status.total_steps != null ? `${status.total_steps.toLocaleString()} steps` : ''}
                    {status.episodes != null ? ` · ${status.episodes} episodes` : ''}
                </span>

                <div style={{ flex: 1 }} />

                <button
                    onClick={() => setShowLidar((v) => !v)}
                    style={{
                        ...S.button('default'),
                        fontSize: 10,
                        padding: '2px 8px',
                        color: showLidar ? P.accent : P.textMuted,
                        borderColor: showLidar ? P.accent : P.borderStrong,
                    }}
                >
                    LIDAR {showLidar ? 'ON' : 'OFF'}
                </button>
            </div>

            {!rl.connected && !status.state ? (
                <ServerDown P={P} S={S} />
            ) : (
                <>
                    {/* Main split */}
                    <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                        <div style={{ flex: 1, minWidth: 0, position: 'relative', borderRight: `1px solid ${P.border}` }}>
                            <RlWarehouseCanvas snapshot={rl.snapshot} palette={P} showLidar={showLidar} />
                            <div style={{
                                position: 'absolute',
                                top: 8,
                                left: 10,
                                zIndex: 5,
                                fontSize: 10,
                                color: P.textMuted,
                                background: withAlpha(P.surface, 0.7),
                                border: `1px solid ${P.border}`,
                                borderRadius: 4,
                                padding: '3px 8px',
                            }}>
                                RL robot highlighted · global path dashed · velocity trail = trajectory ·
                                drag to pan · wheel to zoom
                            </div>
                        </div>

                        <div style={{
                            width: 340,
                            flex: '0 0 auto',
                            overflowY: 'auto',
                            padding: 10,
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 12,
                            background: P.background,
                        }}>
                            <div style={S.card}>
                                <TrainingControls rl={rl} />
                            </div>
                            <div style={S.card}>
                                <ScenarioPanel rl={rl} />
                            </div>
                            <div style={S.card}>
                                <MetricsPanel rl={rl} />
                            </div>
                        </div>
                    </div>

                    {/* Bottom tabs */}
                    <div style={{
                        borderTop: `1px solid ${P.border}`,
                        background: P.surface,
                        height: 300,
                        display: 'flex',
                        flexDirection: 'column',
                    }}>
                        <div style={{ display: 'flex', gap: 4, padding: '6px 10px 0', }}>
                            {BOTTOM_TABS.map(([key, label]) => (
                                <button
                                    key={key}
                                    onClick={() => setTab(key)}
                                    style={{ ...S.tab(tab === key), flex: '0 1 auto', padding: '4px 12px' }}
                                >
                                    {label}
                                </button>
                            ))}
                            <div style={{ flex: 1 }} />
                            {rl.error && (
                                <span style={{ ...S.chip(P.danger), fontSize: 9, alignSelf: 'center', cursor: 'pointer' }}
                                    onClick={rl.clearError}
                                    title="dismiss"
                                >
                                    ERROR: {rl.error}
                                </span>
                            )}
                            {rl.lastAck && rl.lastAck.error && (
                                <span style={{ ...S.chip(P.warning), fontSize: 9, alignSelf: 'center' }}>
                                    last cmd: {rl.lastAck.error}
                                </span>
                            )}
                        </div>
                        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px' }}>
                            {tab === 'charts' && <ChartsPanel rl={rl} />}
                            {tab === 'debug' && <DebugPanel rl={rl} />}
                            {tab === 'evaluation' && <EvaluationPanel rl={rl} />}
                            {tab === 'checkpoints' && <CheckpointPanel rl={rl} />}
                            {tab === 'logs' && <LogPanel rl={rl} />}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}

function stateColor(P, state) {
    const colors = {
        IDLE: P.textMuted,
        TRAINING: P.success,
        PAUSED: P.warning,
        EVALUATING: P.info,
        STOPPING: P.warning,
        ERROR: P.danger,
    };
    return colors[state] || P.textMuted;
}

function ServerDown({ P, S }) {
    return (
        <div style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
        }}>
            <div style={{ ...S.card, maxWidth: 560, padding: 20 }}>
                <div style={{ ...S.sectionTitle, marginBottom: 10 }}>RL training server not reachable</div>
                <div style={{ fontSize: 12, lineHeight: 1.7, color: P.text }}>
                    The visual training UI talks to the Python backend (aiohttp on port 8370).
                    Start it from the dashboard directory:
                </div>
                <div style={{
                    background: P.canvasBg,
                    border: `1px solid ${P.border}`,
                    borderRadius: 4,
                    padding: '8px 12px',
                    margin: '10px 0',
                    fontSize: 12,
                    color: P.success,
                }}>
                    npm run rl-server
                </div>
                <div style={{ fontSize: 11, color: P.textMuted, lineHeight: 1.6 }}>
                    The proxy forwards <b>/rl</b> to <b>http://127.0.0.1:8370</b> in dev. Reconnecting
                    automatically — the page will populate as soon as the server answers.
                </div>
            </div>
        </div>
    );
}