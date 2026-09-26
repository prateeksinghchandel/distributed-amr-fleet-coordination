import { useRef, useState } from 'react';
import { useTheme } from '../theme/ThemeContext.jsx';
import { ui } from '../theme/ui.js';
import { useFleetManager } from '../hooks/useFleetManager.js';
import ModeSelector from './ModeSelector.jsx';
import FleetConfigEditor from './settings/FleetConfigEditor.jsx';

export default function StartupScreen({ fleet, mode, onModeChange, onOpenRl, onOpenSelfplay }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const { status, error, managerAvailable, command, busy, lastCommandError } = useFleetManager();
    const config = status ? status.configuration : { preset: '—', tasks: 0, auctionMode: 'SERVER_AUCTION' };
    const amrs = status ? status.amrs : [];
    const startAllBusy = busy['POST:/api/start-all'];
    const [startOutcome, setStartOutcome] = useState(null);
    const outcomeKey = useRef(0);

    const startFleet = async () => {
        setStartOutcome(null);
        try {
            const data = await command('/api/start-all');
            const results = data && data.results;
            const failed = results
                ? Object.entries(results).filter(([, r]) => r && r.ok === false)
                : [];
            if (failed.length > 0) {
                const key = 'start-fail-' + (++outcomeKey.current);
                setStartOutcome({ type: 'error', key, failed });
                fleet.addLog(`[SYSTEM] START ALL: ${failed.length}/${results ? Object.keys(results).length : '?'} process(es) failed`);
                for (const [name, r] of failed) {
                    fleet.addLog(`[SYSTEM] start failed ${name}: ${r.error || 'unknown error'}`);
                }
            } else {
                const key = 'start-ok-' + (++outcomeKey.current);
                setStartOutcome({ type: 'ok', key });
                fleet.addLog('[SYSTEM] START ALL: stack started — awaiting fleet readiness');
            }
        } catch (e) {
            const key = 'start-err-' + (++outcomeKey.current);
            setStartOutcome({ type: 'command-error', key, message: lastCommandError || e.message || String(e) });
        }
    };

    return (
        <div
            style={{
                height: '100vh',
                width: '100vw',
                background: P.background,
                color: P.text,
                fontFamily: 'monospace',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                overflowY: 'auto',
            }}
        >
            <div style={{ ...S.card, width: 'min(640px, 92vw)', padding: '24px 30px', margin: '24px 0' }}>
                <div style={{ fontSize: 22, fontWeight: 'bold', color: P.accent, letterSpacing: 1, textAlign: 'center' }}>
                    AMR FLEET MANAGER
                </div>
                <div style={{ fontSize: 11, color: P.textMuted, marginTop: 6, lineHeight: 1.7, textAlign: 'center' }}>
                    {managerAvailable
                        ? 'Fleet control room — warehouse robots, task bidding and fleet processes.'
                        : 'Fleet Manager not reachable yet.'}
                </div>

                <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 8, fontSize: 12 }}>
                    <span style={{ color: P.textMuted }}>Preset</span><span>{config.preset}</span>
                    <span style={{ color: P.textMuted }}>Startup tasks</span><span>{config.tasks}</span>
                    <span style={{ color: P.textMuted }}>Auction mode</span>
                    <span style={{ color: config.auctionMode === 'P2P_AUCTION' ? P.success : P.info }}>
                        {config.auctionMode === 'P2P_AUCTION' ? 'P2P' : 'SERVER'}
                    </span>
                    <span style={{ color: P.textMuted }}>AMRs</span><span>{amrs.length}</span>
                </div>

                <div style={{ display: 'flex', justifyContent: 'center', marginTop: 18 }}>
                    {managerAvailable ? (
                        <button
                            style={{ ...S.button('primary'), padding: '10px 28px', fontSize: 14 }}
                            disabled={startAllBusy}
                            onClick={startFleet}
                        >
                            {startAllBusy ? 'STARTING…' : '▶ START FLEET'}
                        </button>
                    ) : (
                        <span style={{ fontSize: 11, color: P.danger }}>{error ? error : 'Connecting to Fleet Manager…'}</span>
                    )}
                </div>
                {!managerAvailable && (
                    <div style={{ fontSize: 10, color: P.textDim, marginTop: 8, textAlign: 'center' }}>
                        Start it with: PYTHONPATH=backend .venv/bin/python -m fleet_manager (from project root)
                    </div>
                )}

                {lastCommandError && (
                    <div style={{ fontSize: 11, color: P.logErr, marginTop: 8, textAlign: 'center' }}>
                        ⚠ {lastCommandError}
                    </div>
                )}

                {startOutcome && startOutcome.type === 'error' && (
                    <div style={{ marginTop: 12, padding: 10, borderRadius: 5, border: `1px solid ${P.logErr}`, background: P.surfaceActive }}>
                        <div style={{ fontSize: 11, fontWeight: 'bold', color: P.logErr }}>⚠ START ALL — some processes failed:</div>
                        {startOutcome.failed.map(([name, r]) => (
                            <div key={`${startOutcome.key}-${name}`} style={{ fontSize: 11, color: P.text, marginTop: 4 }}>
                                • <b>{name}</b> — {r.error || r.state || 'unknown error'}
                            </div>
                        ))}
                        <div style={{ fontSize: 10, color: P.textDim, marginTop: 6 }}>
                            Check Infrastructure logs (SETTINGS → INFRASTRUCTURE) or the fleet-manager console for details.
                        </div>
                    </div>
                )}
                {startOutcome && startOutcome.type === 'ok' && (
                    <div style={{ marginTop: 12, fontSize: 11, color: P.success, textAlign: 'center' }}>
                        Stack started — the control room opens when all processes report ready.
                    </div>
                )}
                {startOutcome && startOutcome.type === 'command-error' && (
                    <div style={{ marginTop: 12, fontSize: 11, color: P.logErr, textAlign: 'center' }}>
                        ⚠ Command failed: {startOutcome.message}
                    </div>
                )}

                <div style={{ ...S.sectionTitle, marginTop: 20 }}>CONFIGURE BEFORE START</div>
                <div style={{ marginTop: 8 }}>
                    <FleetConfigEditor compact />
                </div>

                <div style={{ display: 'flex', justifyContent: 'center', marginTop: 20, flexDirection: 'column', gap: 6, alignItems: 'center' }}>
                    <ModeSelector mode={mode} onModeChange={onModeChange} />
                    <button
                        style={{ ...S.button(), fontSize: 11 }}
                        onClick={onOpenRl}
                        title="Independent visual RL training studio (works without the fleet stack)"
                    >
                        ⚙ RL TRAINING STUDIO
                    </button>
                    <button
                        style={{ ...S.button(), fontSize: 11 }}
                        onClick={onOpenSelfplay}
                        title="Self-play studio: champion vs frozen opponent pool (auto-launches the --league server)"
                    >
                        ⟲ SELF-PLAY STUDIO
                    </button>
                </div>
            </div>
        </div>
    );
}