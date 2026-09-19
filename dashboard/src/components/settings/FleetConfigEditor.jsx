import { useEffect, useMemo, useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { useFleetManager } from '../../hooks/useFleetManager.js';

const FALLBACK_PRESETS = ['MICRO_FULFILLMENT', 'ECOMMERCE', 'DISTRIBUTION'];

/**
 * Shared editor for the Fleet Manager configuration (preset / startup tasks /
 * auction mode). Backend-authoritative — writes go to /api/coordinator/configure.
 * Used from Settings → FLEET and from the StartupScreen so the auction mode can
 * be chosen *before* the fleet is started.
 */
export default function FleetConfigEditor({ compact = false }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const { status, info, error, managerAvailable, command, busy, lastCommandError } = useFleetManager();
    const config = status ? status.configuration : { preset: '', tasks: 0, auctionMode: 'SERVER_AUCTION' };
    const presets = (info && info.presets) || FALLBACK_PRESETS;
    const [draft, setDraft] = useState(null);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (status && draft === null) {
            setDraft({ preset: config.preset, tasks: config.tasks, auctionMode: config.auctionMode });
        }
    }, [status, draft, config.preset, config.tasks, config.auctionMode]);

    const dirty = useMemo(() => {
        if (!status || !draft) return false;
        return draft.preset !== config.preset ||
            Number(draft.tasks) !== Number(config.tasks) ||
            draft.auctionMode !== config.auctionMode;
    }, [status, draft, config.preset, config.tasks, config.auctionMode]);

    const coordRunning = status && status.backend && status.backend.coordinator &&
        ['STARTING', 'RUNNING', 'STOPPING'].includes(status.backend.coordinator.state);

    const save = async () => {
        if (!draft) return;
        setSaved(false);
        try {
            await command('/api/coordinator/configure', {
                method: 'POST',
                body: { preset: draft.preset, tasks: Number(draft.tasks), auctionMode: draft.auctionMode },
            });
            setSaved(true);
        } catch {
            /* surfaced via lastCommandError */
        }
    };

    const configBusy = busy['POST:/api/coordinator/configure'];
    const restartBusy = busy['POST:/api/coordinator/restart'];

    return (
        <div style={{ fontSize: 12 }}>
            {!managerAvailable && (
                <div style={{ fontSize: 11, color: P.danger, marginBottom: 10 }}>
                    {error ? `Fleet Manager unreachable: ${error}` : 'Contacting Fleet Manager…'}
                </div>
            )}

            <div style={S.label}>PRESET</div>
            <select style={S.select} value={draft ? draft.preset : ''} disabled={!draft || !managerAvailable}
                onChange={(e) => { setDraft({ ...draft, preset: e.target.value }); setSaved(false); }}>
                {presets.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>

            <div style={{ ...S.label, marginTop: 8 }}>STARTUP TASKS</div>
            <input style={S.input} type="number" min="0" value={draft ? draft.tasks : ''} disabled={!draft}
                onChange={(e) => { setDraft({ ...draft, tasks: e.target.value }); setSaved(false); }} />

            <div style={{ ...S.label, marginTop: 8 }}>AUCTION MODE</div>
            <div style={{ display: 'flex', gap: 6 }}>
                {['SERVER_AUCTION', 'P2P_AUCTION'].map((m) => (
                    <button key={m} style={S.tab(draft && draft.auctionMode === m)}
                        onClick={() => { setDraft({ ...draft, auctionMode: m }); setSaved(false); }}>
                        {m === 'P2P_AUCTION' ? 'P2P (peer-to-peer bidding)' : 'SERVER (coordinator-led)'}
                    </button>
                ))}
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <button style={S.button('primary')} disabled={!managerAvailable || configBusy} onClick={save}>
                    {configBusy ? '…' : 'SAVE CONFIG'}
                </button>
                {dirty && <span style={{ fontSize: 11, color: P.warning }}>⚠ Unsaved changes</span>}
                {saved && !dirty && <span style={{ fontSize: 11, color: P.success }}>Saved ✓</span>}
                {lastCommandError && <span style={{ fontSize: 11, color: P.logErr }}>⚠ {lastCommandError}</span>}
            </div>

            {coordRunning && (
                <div style={{ fontSize: 11, color: P.warning, marginTop: 8 }}>
                    Coordinator is running — stop it (or the fleet) before saving a new configuration.
                </div>
            )}

            <div style={{ marginTop: 8, fontSize: 11, color: P.textMuted, lineHeight: 1.5 }}>
                Config changes apply when the coordinator restarts; a new preset regenerates the warehouse layout.
            </div>

            {dirty && !compact && (
                <div style={{ marginTop: 10, padding: 10, borderRadius: 5, border: `1px solid ${P.warning}`, fontSize: 11, color: P.textMuted }}>
                    ⚠ <b>Restart required</b> for the new configuration to take effect.
                    <div style={{ marginTop: 8 }}>
                        <button style={S.button('primary')} disabled={!managerAvailable || restartBusy} onClick={() => command('/api/coordinator/restart')}>
                            {restartBusy ? '…' : 'RESTART COORDINATOR'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}