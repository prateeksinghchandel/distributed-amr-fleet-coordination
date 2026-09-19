import { useEffect, useMemo, useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { useFleetManager } from '../../hooks/useFleetManager.js';
import { CONNECTION_STATUS } from '../../distributed/ConnectionManager.js';

const STATE_COLOR = (P) => ({
    RUNNING: P.success,
    ONLINE: P.success,
    STARTING: P.warning,
    STOPPING: P.warning,
    STOPPED: P.textDim,
    CRASHED: P.danger,
});

function StatusDot({ P, state, label }) {
    const S = ui(P);
    const color = STATE_COLOR(P)[state] || P.textDim;
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color, fontSize: 11, minWidth: 88 }}>
            <span style={S.dot(color)} />
            {label || state || 'STOPPED'}
        </span>
    );
}

export default function InfrastructureSettings({ fleet }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const { status, error, managerAvailable, lastCommandError, command, fetchLogs, busy } = useFleetManager();
    const [logSource, setLogSource] = useState(null);
    const [logData, setLogData] = useState({ lines: [] });

    const amrs = useMemo(() => (status ? status.amrs : []), [status]);
    const bridgeState = status ? status.infrastructure.bridge.state : 'STOPPED';

    useEffect(() => {
        if (bridgeState === 'RUNNING' && fleet && fleet.conn && fleet.conn.status !== CONNECTION_STATUS.CONNECTED) {
            fleet.conn.retryConnection();
        }
    }, [bridgeState, fleet]);

    const logTabs = useMemo(() => [
        { key: 'zenohd', label: 'Zenoh' },
        { key: 'bridge', label: 'Bridge' },
        { key: 'coordinator', label: 'Coordinator' },
        ...amrs.map((a) => ({ key: a.name || `amr:${a.id}`, label: a.id })),
    ], [amrs]);

    const processMeta = useMemo(() => {
        if (!status) return {};
        const map = {
            zenohd: status.infrastructure.zenoh,
            bridge: status.infrastructure.bridge,
            coordinator: status.backend.coordinator,
        };
        for (const a of status.amrs) map[a.name || `amr:${a.id}`] = a;
        return map;
    }, [status]);

    const isBusy = (path) => busy[`POST:${path}`] || busy[`DELETE:${path}`];

    useEffect(() => {
        if (!logSource) return;
        let cancelled = false;
        const tick = async () => {
            const data = await fetchLogs(logSource, 250);
            if (!cancelled) setLogData(data);
        };
        tick();
        const timer = setInterval(tick, 1500);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [logSource, fetchLogs]);

    if (!managerAvailable) {
        return (
            <div>
                <div style={{ fontSize: 13, color: P.danger, fontWeight: 'bold' }}>FLEET MANAGER UNAVAILABLE</div>
                <div style={{ fontSize: 11, color: P.textMuted, marginTop: 6 }}>
                    {error ? `Cannot reach Fleet Manager: ${error}` : 'Contacting Fleet Manager…'}
                    <br />
                    Start it with: PYTHONPATH=backend .venv/bin/python -m fleet_manager (from project root)
                </div>
            </div>
        );
    }

    const zenoh = status.infrastructure.zenoh;
    const bridge = status.infrastructure.bridge;
    const coord = status.backend.coordinator;
    const ready = Boolean(status.ready);

    const processRow = (title, proc, base) => {
        const running = proc && (proc.state === 'RUNNING' || proc.state === 'STARTING' || proc.state === 'STOPPING');
        const busyRow = isBusy(`${base}/start`) || isBusy(`${base}/stop`) || isBusy(`${base}/restart`);
        return (
            <div style={{ marginBottom: 4 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 26 }}>
                    <span style={{ width: 118, fontSize: 12 }}>{title}</span>
                    {running ? (
                        <button style={S.button('danger')} disabled={busyRow} onClick={() => command(`${base}/stop`)}>STOP</button>
                    ) : (
                        <button style={S.button('primary')} disabled={busyRow} onClick={() => command(`${base}/start`)}>START</button>
                    )}
                    <button style={S.button()} disabled={busyRow || !proc} onClick={() => command(`${base}/restart`)}>RESTART</button>
                    <StatusDot P={P} state={proc ? proc.state : 'STOPPED'} />
                    {proc && proc.pid && <span style={{ fontSize: 10, color: P.textDim }}>pid {proc.pid}</span>}
                </div>
                {proc && proc.lastError && (
                    <div style={{ fontSize: 10, color: P.logErr, marginLeft: 118, wordBreak: 'break-all' }}>⚠ {proc.lastError}</div>
                )}
            </div>
        );
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 12, border: `1px solid ${ready ? P.success : P.warning}`, color: ready ? P.success : P.warning, borderRadius: 3, padding: '2px 10px' }}>
                    {ready ? 'FLEET READY' : 'NOT READY'}
                </span>
                <span style={{ fontSize: 11, color: P.textMuted }}>
                    {status.configuration.preset} · {status.configuration.tasks} task(s) · {status.amrs.length} AMR(s)
                </span>
                {lastCommandError && <span style={{ fontSize: 11, color: P.danger }}>⚠ {lastCommandError}</span>}
            </div>

            <div>
                <div style={S.sectionTitle}>INFRASTRUCTURE</div>
                {processRow('Zenoh Router', zenoh, '/api/zenoh')}
                {processRow('WebSocket Bridge', bridge, '/api/bridge')}
            </div>

            <div>
                <div style={S.sectionTitle}>BACKEND</div>
                {processRow('Coordinator', coord, '/api/coordinator')}
            </div>

            <div>
                <div style={S.sectionTitle}>AMR FLEET</div>
                {amrs.length === 0 && <div style={{ fontSize: 11, color: P.textMuted, marginBottom: 6 }}>No AMRs configured.</div>}
                {amrs.map((a) => {
                    const rowBusy = isBusy(`/api/amrs/${a.id}/start`) || isBusy(`/api/amrs/${a.id}/stop`) || isBusy(`/api/amrs/${a.id}/restart`);
                    const running = a.state === 'RUNNING' || a.state === 'STARTING' || a.state === 'STOPPING';
                    return (
                        <div key={a.id} style={{ marginBottom: 4 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 26 }}>
                                <span style={{ width: 64, fontSize: 12 }}>{a.id}</span>
                                {running ? (
                                    <button style={S.button('danger')} disabled={rowBusy} onClick={() => command(`/api/amrs/${encodeURIComponent(a.id)}/stop`)}>STOP</button>
                                ) : (
                                    <button style={S.button('primary')} disabled={rowBusy} onClick={() => command(`/api/amrs/${encodeURIComponent(a.id)}/start`)}>START</button>
                                )}
                                <button style={S.button()} disabled={rowBusy} onClick={() => command(`/api/amrs/${encodeURIComponent(a.id)}/restart`)}>RESTART</button>
                                <StatusDot P={P} state={a.state} label={a.state === 'RUNNING' ? 'ONLINE' : undefined} />
                                <button style={S.button('danger')} disabled={rowBusy} onClick={() => {
                                    if (window.confirm(`Remove ${a.id}?`)) {
                                        command(`/api/amrs/${encodeURIComponent(a.id)}`, { method: 'DELETE' });
                                        if (logSource === a.name || logSource === `amr:${a.id}`) setLogSource(null);
                                    }
                                }}>REMOVE</button>
                            </div>
                            {a.lastError && <div style={{ fontSize: 10, color: P.logErr, marginLeft: 64, wordBreak: 'break-all' }}>⚠ {a.lastError}</div>}
                        </div>
                    );
                })}
            </div>

            <div>
                <div style={S.sectionTitle}>LOGS</div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
                    {logTabs.map((tab) => (
                        <button
                            key={tab.key}
                            style={{ ...S.button(), borderColor: logSource === tab.key ? P.accent : P.borderStrong, color: logSource === tab.key ? P.accent : P.text }}
                            onClick={() => {
                                const next = logSource === tab.key ? null : tab.key;
                                setLogSource(next);
                                if (!next) setLogData({ lines: [] });
                            }}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
                {logSource ? (
                    <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, marginBottom: 4, minHeight: 16 }}>
                            {(() => {
                                const meta = processMeta[logSource];
                                if (!meta) return null;
                                return (
                                    <>
                                        <StatusDot P={P} state={meta.state} label={meta.state === 'RUNNING' && logSource.startsWith('amr:') ? 'ONLINE' : undefined} />
                                        {meta.lastError && <span style={{ color: P.logErr, wordBreak: 'break-all' }}>⚠ {meta.lastError}</span>}
                                    </>
                                );
                            })()}
                        </div>
                        <div style={{ background: P.surfaceElevated, border: `1px solid ${P.border}`, borderRadius: 3, padding: 8, height: 150, overflowY: 'auto', fontSize: 10, color: P.text, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                            {logData.error && <div style={{ color: P.logErr }}>log error: {logData.error}</div>}
                            {logData.lines.length === 0 && !logData.error && (
                                <span style={{ color: P.textDim }}>
                                    {(() => {
                                        const state = processMeta[logSource] ? processMeta[logSource].state : null;
                                        if (state === 'STOPPED') return 'process not started — capture begins when you press START' + (logSource === 'zenohd' ? ' (with START ALL the whole stack comes up)' : '');
                                        if (state === 'CRASHED') return 'process crashed before producing output';
                                        return 'no log output yet';
                                    })()}
                                </span>
                            )}
                            {logData.lines.join('\n')}
                        </div>
                    </div>
                ) : (
                    <div style={{ fontSize: 11, color: P.textDim }}>Select a process above to view its captured stdout/stderr.</div>
                )}
            </div>
        </div>
    );
}