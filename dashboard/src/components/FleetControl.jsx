import React, { useEffect, useMemo, useState } from 'react';
import { useFleetManager } from '../hooks/useFleetManager.js';
import { CONNECTION_STATUS } from '../distributed/ConnectionManager.js';

const C = {
    RUNNING: '#00ff88',
    ONLINE: '#00ff88',
    STARTING: '#ffd43b',
    STOPPING: '#ff9d3b',
    STOPPED: '#8a8a9a',
    CRASHED: '#e94560',
    ACCENT: '#e94560',
    PANEL: '#16213e',
    PANEL_DARK: '#0a0a1a',
    BORDER: '#0f3460',
    TEXT: '#e0e0e0',
    MUTED: '#888',
};

const STATE_LABEL = {
    RUNNING: 'RUNNING',
    ONLINE: 'ONLINE',
    STARTING: 'STARTING',
    STOPPING: 'STOPPING',
    STOPPED: 'STOPPED',
    CRASHED: 'CRASHED',
};

const baseButton = {
    padding: '3px 10px',
    background: '#0f3460',
    color: '#e0e0e0',
    border: '1px solid #555577',
    borderRadius: 3,
    cursor: 'pointer',
    fontSize: 11,
    fontFamily: 'inherit',
};

const dotStyle = (color) => ({
    display: 'inline-block',
    width: 9,
    height: 9,
    borderRadius: '50%',
    background: color,
    marginRight: 6,
    flex: '0 0 auto',
});

function StatusDot({ state, label }) {
    const color = C[state] || C.STOPPED;
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', color, fontSize: 11, minWidth: 84 }}>
            <span style={dotStyle(color)} />
            {label || STATE_LABEL[state] || (state || 'UNKNOWN').toUpperCase()}
        </span>
    );
}

function ActionButton({ label, onClick, title, disabled, style }) {
    const active = !disabled && style !== 'danger';
    return (
        <button
            type="button"
            title={title}
            onClick={onClick}
            disabled={disabled}
            style={{
                ...baseButton,
                borderColor: active ? '#00ff88' : '#555577',
                color: active ? '#00ff88' : '#8a8a9a',
                opacity: disabled ? 0.5 : 1,
                ...(style === 'danger'
                    ? { borderColor: '#e94560', color: '#e94560' }
                    : {}),
            }}
        >
            {label}
        </button>
    );
}

function SectionTitle({ children }) {
    return (
        <div style={{
            fontSize: 11,
            letterSpacing: 1,
            color: '#e94560',
            fontWeight: 'bold',
            marginBottom: 6,
        }}>
            {children}
        </div>
    );
}

function ProcessRow({ title, proc, busy, onStart, onStop, onRestart }) {
    const running = proc && (proc.state === 'RUNNING' || proc.state === 'STARTING' || proc.state === 'STOPPING');
    return (
        <React.Fragment>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, minHeight: 26 }}>
                <span style={{ width: 148, fontSize: 12, color: '#e0e0e0' }}>{title}</span>
                {running ? (
                    <ActionButton label={'STOP'} onClick={onStop} disabled={busy} title="Gracefully stop" />
                ) : (
                    <ActionButton label={'START'} onClick={onStart} disabled={busy} title="Start" />
                )}
                <ActionButton
                    label="RESTART"
                    onClick={onRestart}
                    disabled={busy || !proc}
                    title={proc && !running ? 'Start' : 'Restart'}
                />
                <StatusDot state={proc ? proc.state : 'STOPPED'} />
                <span style={{ fontSize: 10, color: '#666', marginLeft: 4 }}>
                    {proc && proc.pid ? `pid ${proc.pid}` : ''}
                </span>
            </div>
            {proc && proc.lastError && (
                <div style={{ fontSize: 10, color: '#e94560', margin: '-2px 0 4px 148px', wordBreak: 'break-all' }}>
                    ⚠ {proc.lastError}
                </div>
            )}
        </React.Fragment>
    );
}

const logTabsFor = (amrs) => [
    { key: 'zenohd', label: 'Zenoh' },
    { key: 'bridge', label: 'Bridge' },
    { key: 'coordinator', label: 'Coordinator' },
    ...amrs.map((a) => ({ key: a.name || `amr:${a.id}`, label: a.id })),
];

export default function FleetControl({ fleet }) {
    const { status, error, managerAvailable, busy, lastCommandError, command, fetchLogs } = useFleetManager();
    const [open, setOpen] = useState(true);
    const [logSource, setLogSource] = useState(null);
    const [logData, setLogData] = useState({ lines: [] });
    const [createForm, setCreateForm] = useState({ id: '', x: '', y: '' });

    const amrs = useMemo(() => (status ? status.amrs : []), [status]);
    const ready = status ? status.ready : false;

    const bridgeState = status ? status.infrastructure.bridge.state : 'STOPPED';

    useEffect(() => {
        // As soon as the Fleet Manager reports the WS bridge RUNNING, snap the
        // Zenoh connection to connected instead of waiting on the reconnect backoff.
        if (bridgeState === 'RUNNING' && fleet && fleet.conn && fleet.conn.status !== CONNECTION_STATUS.CONNECTED) {
            fleet.conn.retryConnection();
        }
    }, [bridgeState, fleet]);

    const logTabs = useMemo(() => logTabsFor(amrs), [amrs]);

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

    const nextDefaultId = useMemo(() => {
        const used = new Set(amrs.map((a) => a.id));
        let n = 1;
        while (used.has(`AMR${n}`)) n += 1;
        return `AMR${n}`;
    }, [amrs]);

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
            <div style={{ borderBottom: '2px solid #0f3460', background: '#16213e', padding: '10px 16px' }}>
                <div style={{ fontSize: 13, color: '#e94560', fontWeight: 'bold', fontFamily: 'monospace' }}>
                    FLEET CONTROL — MANAGER UNAVAILABLE
                </div>
                <div style={{ fontSize: 11, color: '#8a8a9a', fontFamily: 'monospace', marginTop: 4 }}>
                    {error ? `Cannot reach Fleet Manager: ${error}` : 'Contacting Fleet Manager…'}
                    <br />
                    Start it with: PYTHONPATH=backend/.venv/bin/python -m fleet_manager (from project root)
                </div>
            </div>
        );
    }

    const isBusy = (path) => busy[`POST:${path}`] || busy[`DELETE:${path}`];
    const zenoh = status.infrastructure.zenoh;
    const bridge = status.infrastructure.bridge;
    const coord = status.backend.coordinator;

    const handleCreate = async (e) => {
        e.preventDefault();
        const id = (createForm.id || '').trim();
        if (!id) return;
        try {
            await command('/api/amrs', { method: 'POST', body: { id, x: parseFloat(createForm.x) || 0, y: parseFloat(createForm.y) || 0 } });
            setCreateForm({ id: '', x: '', y: '' });
        } catch {
            /* error surfaced in the header strip */
        }
    };

    const handleRemove = async (id) => {
        if (!window.confirm(`Remove ${id}? The AMR will be deleted from the configured fleet.`)) return;
        try {
            await command(`/api/amrs/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (logSource === id || logSource === `amr:${id}`) setLogSource(null);
        } catch {
            /* error surfaced in the header strip */
        }
    };

    return (
        <div style={{ borderBottom: '2px solid #0f3460', background: '#16213e', color: '#e0e0e0', fontFamily: 'monospace' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 16px', cursor: 'pointer' }} onClick={(e) => { if (e.target.closest('button')) return; setOpen((o) => !o); }}>
                <span style={{ fontSize: 11, letterSpacing: 1, color: '#e94560', fontWeight: 'bold' }}>▸ FLEET CONTROL</span>
                <span
                    style={{
                        fontSize: 11,
                        border: `1px solid ${ready ? '#00ff88' : '#ffd43b'}`,
                        color: ready ? '#00ff88' : '#ffd43b',
                        borderRadius: 3,
                        padding: '1px 8px',
                    }}
                >
                    {ready ? 'FLEET READY' : 'NOT READY'}
                </span>
                <span style={{ fontSize: 11, color: '#8a8a9a' }}>
                    {status.configuration.preset} · {status.configuration.tasks} task(s) · {amrs.length} AMR(s)
                </span>
                {lastCommandError && <span style={{ fontSize: 11, color: '#e94560' }}>⚠ {lastCommandError}</span>}
                <span style={{ flex: 1 }} />
                <button style={{ ...baseButton, borderColor: '#00ff88', color: '#00ff88' }} disabled={isBusy('/api/start-all')} onClick={() => command('/api/start-all')}>
                    {isBusy('/api/start-all') ? '…' : 'START ALL'}
                </button>
                <button style={{ ...baseButton, borderColor: '#e94560', color: '#e94560' }} disabled={isBusy('/api/stop-all')} onClick={() => command('/api/stop-all')}>
                    {isBusy('/api/stop-all') ? '…' : 'STOP ALL'}
                </button>
                <span style={{ fontSize: 10, color: '#555' }}>[{open ? '−' : '+'}]</span>
            </div>

            {open && (
                <div style={{ display: 'flex', gap: 0, padding: '4px 16px 12px', flexWrap: 'wrap' }}>
                    <div style={{ minWidth: 230, marginRight: 26 }}>
                        <SectionTitle>INFRASTRUCTURE</SectionTitle>
                        <ProcessRow
                            title="Zenoh Router"
                            proc={zenoh}
                            busy={isBusy('/api/zenoh/start') || isBusy('/api/zenoh/stop') || isBusy('/api/zenoh/restart')}
                            onStart={() => command('/api/zenoh/start')}
                            onStop={() => command('/api/zenoh/stop')}
                            onRestart={() => command('/api/zenoh/restart')}
                        />
                        <ProcessRow
                            title="WebSocket Bridge"
                            proc={bridge}
                            busy={isBusy('/api/bridge/start') || isBusy('/api/bridge/stop') || isBusy('/api/bridge/restart')}
                            onStart={() => command('/api/bridge/start')}
                            onStop={() => command('/api/bridge/stop')}
                            onRestart={() => command('/api/bridge/restart')}
                        />
                    </div>

                    <div style={{ minWidth: 230, marginRight: 26 }}>
                        <SectionTitle>BACKEND</SectionTitle>
                        <ProcessRow
                            title="Coordinator"
                            proc={coord}
                            busy={isBusy('/api/coordinator/start') || isBusy('/api/coordinator/stop') || isBusy('/api/coordinator/restart')}
                            onStart={() => command('/api/coordinator/start')}
                            onStop={() => command('/api/coordinator/stop')}
                            onRestart={() => command('/api/coordinator/restart')}
                        />
                    </div>

                    <div style={{ minWidth: 300, marginRight: 26 }}>
                        <SectionTitle>AMR FLEET</SectionTitle>
                        {amrs.length === 0 && (
                            <div style={{ fontSize: 11, color: '#8a8a9a', marginBottom: 6 }}>No AMRs configured.</div>
                        )}
                        {amrs.map((a) => (
                            <div key={a.id} style={{ display: 'flex', flexDirection: 'column' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, minHeight: 26 }}>
                                    <span style={{ width: 76, fontSize: 12, color: '#e0e0e0' }}>{a.id}</span>
                                    {(() => {
                                        const amrBusy = isBusy(`/api/amrs/${a.id}/start`) || isBusy(`/api/amrs/${a.id}/stop`) || isBusy(`/api/amrs/${a.id}/restart`);
                                        return (
                                            <React.Fragment>
                                                {(a.state === 'RUNNING' || a.state === 'STARTING' || a.state === 'STOPPING') ? (
                                                    <ActionButton label={'STOP'} onClick={() => command(`/api/amrs/${encodeURIComponent(a.id)}/stop`)} disabled={amrBusy} />
                                                ) : (
                                                    <ActionButton label={'START'} onClick={() => command(`/api/amrs/${encodeURIComponent(a.id)}/start`)} disabled={amrBusy} />
                                                )}
                                                <ActionButton label="RESTART" onClick={() => command(`/api/amrs/${encodeURIComponent(a.id)}/restart`)} disabled={amrBusy} />
                                                <StatusDot state={a.state} label={a.state === 'RUNNING' ? 'ONLINE' : undefined} />
                                                <ActionButton label="REMOVE" style="danger" onClick={() => handleRemove(a.id)} disabled={amrBusy} />
                                            </React.Fragment>
                                        );
                                    })()}
                                </div>
                                {a.lastError && (
                                    <div style={{ fontSize: 10, color: '#e94560', margin: '-2px 0 4px', wordBreak: 'break-all' }}>
                                        ⚠ {a.lastError}
                                    </div>
                                )}
                            </div>
                        ))}
                        <form onSubmit={handleCreate} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 11, color: '#8a8a9a' }}>+ CREATE AMR</span>
                            <input
                                value={createForm.id}
                                onChange={(e) => setCreateForm({ ...createForm, id: e.target.value })}
                                placeholder={nextDefaultId}
                                style={inputStyle}
                                size={6}
                            />
                            <input
                                value={createForm.x}
                                onChange={(e) => setCreateForm({ ...createForm, x: e.target.value })}
                                placeholder="x"
                                style={{ ...inputStyle, width: 44 }}
                                type="number"
                                step="0.5"
                            />
                            <input
                                value={createForm.y}
                                onChange={(e) => setCreateForm({ ...createForm, y: e.target.value })}
                                placeholder="y"
                                style={{ ...inputStyle, width: 44 }}
                                type="number"
                                step="0.5"
                            />
                            <button type="submit" style={{ ...baseButton, borderColor: '#00ffff', color: '#00ffff' }}>
                                Create
                            </button>
                        </form>
                    </div>

                    <div style={{ minWidth: 420, flex: 1 }}>
                        <SectionTitle>LOGS</SectionTitle>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
                            {logTabs.map((tab) => (
                                <button
                                    key={tab.key}
                                    type="button"
                                    onClick={() => {
                                        const next = logSource === tab.key ? null : tab.key;
                                        setLogSource(next);
                                        if (!next) setLogData({ lines: [] });
                                    }}
                                    style={{
                                        ...baseButton,
                                        borderColor: logSource === tab.key ? '#00ffff' : '#555577',
                                        color: logSource === tab.key ? '#00ffff' : '#e0e0e0',
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
                                            <React.Fragment>
                                                <StatusDot state={meta.state} label={meta.state === 'RUNNING' && logSource.startsWith('amr:') ? 'ONLINE' : undefined} />
                                                {meta.lastError && (
                                                    <span style={{ color: '#e94560', wordBreak: 'break-all' }}>⚠ {meta.lastError}</span>
                                                )}
                                            </React.Fragment>
                                        );
                                    })()}
                                </div>
                                <div style={{ background: '#0a0a1a', border: '1px solid #0f3460', borderRadius: 3, padding: 8, height: 140, overflowY: 'auto', fontSize: 10, color: '#c8d4e8', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                    {logData.error && <div style={{ color: '#e94560' }}>log error: {logData.error}</div>}
                                    {logData.lines.length === 0 && !logData.error && (
                                        <span style={{ color: '#8a8a9a' }}>
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
                            <div style={{ fontSize: 11, color: '#8a8a9a' }}>Select a process above to view its captured stdout/stderr.</div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

const inputStyle = {
    background: '#0a0a1a',
    border: '1px solid #0f3460',
    color: '#e0e0e0',
    borderRadius: 3,
    padding: '3px 6px',
    fontSize: 11,
    fontFamily: 'inherit',
};