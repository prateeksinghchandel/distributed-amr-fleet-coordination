import { useEffect, useRef, useState } from 'react';
import { useTheme } from '../theme/ThemeContext.jsx';
import { ui, taskCounts } from '../theme/ui.js';
import { CONNECTION_STATUS } from '../distributed/ConnectionManager.js';
import { useFleetManager } from '../hooks/useFleetManager.js';
import ModeSelector from './ModeSelector.jsx';

const CONN_LABEL = {
    [CONNECTION_STATUS.CONNECTED]: { color: (P) => P.success, label: 'CONNECTED' },
    [CONNECTION_STATUS.CONNECTING]: { color: (P) => P.warning, label: 'CONNECTING' },
    [CONNECTION_STATUS.ERROR]: { color: (P) => P.danger, label: 'ERROR' },
    [CONNECTION_STATUS.DISCONNECTED]: { color: (P) => P.textDim, label: 'DISCONNECTED' },
};

export default function Header({ fleet, mode, onModeChange, onOpenSettings, onOpenInfrastructure, onOpenRl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const { busy, command, managerAvailable, lastCommandError: fmLastError } = useFleetManager();
    const conn = fleet.connection;
    const connStyle = CONN_LABEL[conn.status] || CONN_LABEL[CONNECTION_STATUS.DISCONNECTED];
    const color = connStyle.color(P);
    const counts = taskCounts(fleet.tasksList);
    const online = fleet.robotsList.filter((r) => r.online).length;
    const startAllBusy = busy['POST:/api/start-all'];
    const stopAllBusy = busy['POST:/api/stop-all'];
    const [actionMsg, setActionMsg] = useState(null);
    const actionMsgTimer = useRef(null);

    useEffect(() => {
        return () => {
            if (actionMsgTimer.current) clearTimeout(actionMsgTimer.current);
        };
    }, []);

    const flashAction = (text, color) => {
        setActionMsg({ text, color });
        if (actionMsgTimer.current) clearTimeout(actionMsgTimer.current);
        actionMsgTimer.current = setTimeout(() => setActionMsg(null), 5000);
    };

    const runFleetAction = async (path, successMsg) => {
        let data;
        try {
            flashAction('SENDING ' + path + ' …', P.warning);
            data = await command(path);
        } catch {
            const msg = fmLastError || 'unknown error';
            fleet.addLog(`[SYSTEM] ${path} failed: ${msg}`);
            flashAction('⚠ ' + msg, P.logErr);
            return;
        }
        const results = data && data.results;
        const failed = results ? Object.entries(results).filter(([, r]) => r && r.ok === false) : [];
        if (failed.length > 0) {
            const names = failed.map(([n, r]) => `${n} (${r.error || r.state})`).join('; ');
            fleet.addLog(`[SYSTEM] ${path} — ${failed.length}/${Object.keys(results).length} process(es) FAILED`);
            for (const [name, r] of failed) fleet.addLog(`[SYSTEM] ${name}: ${r.error || r.state || 'unknown error'}`);
            flashAction(`⚠ ${failed.length}/${Object.keys(results).length} FAILED: ${names}`, P.logErr);
        } else {
            fleet.addLog(`[SYSTEM] ${successMsg}`);
            flashAction('✓ ' + successMsg, P.success);
        }
    };

    return (
        <div
            style={{
                display: 'flex',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: '8px 14px',
                padding: '8px 18px',
                background: P.surface,
                borderBottom: `2px solid ${P.accent}`,
                color: P.text,
            }}
        >
            <div style={{ fontWeight: 'bold', fontSize: 15, color: P.accent, letterSpacing: 0.5 }}>
                AMR FLEET MANAGER
            </div>

            <span
                title={conn.detail || ''}
                style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    fontSize: 11,
                    color,
                    border: `1px solid ${color}`,
                    borderRadius: 4,
                    padding: '3px 9px',
                }}
            >
                <span style={S.dot(color)} />
                {conn.status === CONNECTION_STATUS.ERROR || conn.status === CONNECTION_STATUS.DISCONNECTED
                    ? `ZENOH ${connStyle.label}`
                    : connStyle.label}
            </span>

            <div style={{ fontSize: 12, color: P.textMuted }}>
                {online}/{fleet.robotsList.length} ONLINE
            </div>
            <div style={{ fontSize: 12, color: P.info }}>{counts.active} ACTIVE TASKS</div>

            <div style={{ flex: 1 }} />

            <ModeSelector mode={mode} onModeChange={onModeChange} />

            {managerAvailable && (
                <>
                    <button style={S.button('primary')} disabled={startAllBusy} onClick={() => runFleetAction('/api/start-all', 'START ALL: all processes started')}>
                        {startAllBusy ? '…' : 'START ALL'}
                    </button>
                    <button style={S.button('danger')} disabled={stopAllBusy} onClick={() => runFleetAction('/api/stop-all', 'STOP ALL: all processes stopped')}>
                        {stopAllBusy ? '…' : 'STOP ALL'}
                    </button>
                </>
            )}

            {actionMsg && (
                <span
                    title="Last fleet-manager command result"
                    style={{
                        fontSize: 11,
                        color: actionMsg.color,
                        border: `1px solid ${actionMsg.color}`,
                        borderRadius: 4,
                        padding: '3px 9px',
                        maxWidth: 320,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                    }}
                >
                    {actionMsg.text}
                </span>
            )}

            {!fleet.isConnected && (
                <button style={S.button()} onClick={() => fleet.retryConnection()}>Reconnect</button>
            )}
            <button style={S.button()} title="Visual RL training environment (collision-avoidance policy)" onClick={onOpenRl}>
                RL TRAINING
            </button>
            <button style={S.button()} title="Infrastructure processes & logs" onClick={onOpenInfrastructure}>
                INFRA
            </button>
            <button style={S.button()} onClick={onOpenSettings}>⚙ SETTINGS</button>
        </div>
    );
}