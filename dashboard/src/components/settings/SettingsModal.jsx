import { useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { useFleetManager } from '../../hooks/useFleetManager.js';
import InfrastructureSettings from './InfrastructureSettings.jsx';
import DeveloperTab from './DeveloperTab.jsx';
import FleetConfigEditor from './FleetConfigEditor.jsx';

const TABS = ['GENERAL', 'FLEET', 'WAREHOUSE', 'AUCTION', 'INFRASTRUCTURE', 'DEVELOPER'];

export default function SettingsModal({ fleet, debug, initialTab = 'GENERAL', onClose }) {
    const { palette: P, themeName, setThemeName, accentName, setAccentName, accents } = useTheme();
    const S = ui(P);
    const { lastCommandError, managerAvailable } = useFleetManager();
    const [tab, setTab] = useState(initialTab);
    const warehouse = fleet.warehouse;

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50 }}>
            <div style={{ position: 'absolute', inset: 0, background: P.overlay }} onClick={onClose} />
            <div
                style={{
                    position: 'absolute',
                    top: '6vh',
                    left: '50%',
                    transform: 'translateX(-50%)',
                    width: 'min(880px, 92vw)',
                    maxHeight: '88vh',
                    display: 'flex',
                    flexDirection: 'column',
                    background: P.surface,
                    border: `1px solid ${P.borderStrong}`,
                    borderRadius: 8,
                    boxShadow: `0 24px 60px ${P.overlay}`,
                    color: P.text,
                    fontFamily: 'monospace',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 18px', borderBottom: `1px solid ${P.border}` }}>
                    <span style={S.sectionTitle}>⚙ SETTINGS</span>
                    <span style={{ fontSize: 10, color: P.textDim, marginTop: 1 }}>
                        Fleet Manager: {managerAvailable ? 'AVAILABLE' : 'UNAVAILABLE'}
                    </span>
                    <span style={{ flex: 1 }} />
                    {lastCommandError && <span style={{ fontSize: 11, color: P.danger }}>⚠ {lastCommandError}</span>}
                    <button style={S.button()} onClick={onClose}>CLOSE ✕</button>
                </div>

                <div style={{ display: 'flex', gap: 0, padding: '10px 18px 0', borderBottom: `1px solid ${P.border}` }}>
                    {TABS.map((t) => (
                        <button key={t} style={{ ...S.button(), ...(tab === t ? { background: P.accent, color: P.accentContrast, fontWeight: 'bold', borderColor: P.accent } : {}), borderRadius: '4px 4px 0 0' }} onClick={() => setTab(t)}>
                            {t}
                        </button>
                    ))}
                </div>

                <div style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
                    {tab === 'GENERAL' && (
                        <div>
                            <div style={S.sectionTitle}>THEME</div>
                            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                                {['dark', 'light'].map((t) => (
                                    <button key={t} style={S.tab(themeName === t)} onClick={() => setThemeName(t)}>
                                        {t.toUpperCase()}
                                    </button>
                                ))}
                            </div>

                            <div style={{ ...S.sectionTitle, marginTop: 18 }}>ACCENT</div>
                            <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                                {Object.entries(accents).map(([name, hex]) => (
                                    <button
                                        key={name}
                                        title={name}
                                        onClick={() => setAccentName(name)}
                                        style={{
                                            width: 34,
                                            height: 34,
                                            borderRadius: '50%',
                                            background: hex,
                                            border: accentName === name ? `3px solid ${P.text}` : `2px solid ${P.borderStrong}`,
                                            cursor: 'pointer',
                                        }}
                                    />
                                ))}
                            </div>
                            <div style={{ fontSize: 11, color: P.textMuted, marginTop: 10 }}>
                                Theme and accent persist locally (browser) — they do not touch backend state.
                            </div>
                        </div>
                    )}

                    {tab === 'FLEET' && (
                        <div>
                            <div style={S.sectionTitle}>FLEET CONFIGURATION</div>
                            <div style={{ fontSize: 11, color: P.textDim, marginTop: 6, marginBottom: 12 }}>
                                Stored in the Fleet Manager settings. The coordinator must restart for changes to
                                take effect; a new preset regenerates the warehouse layout.
                            </div>
                            <FleetConfigEditor />
                        </div>
                    )}

                    {tab === 'WAREHOUSE' && (
                        <div>
                            <div style={S.sectionTitle}>WAREHOUSE LAYOUT — READ-ONLY</div>
                            <div style={{ fontSize: 11, color: P.textDim, marginTop: 6, marginBottom: 12 }}>
                                Live values mirrored from the coordinator via <span style={{ color: P.info }}>warehouse/state</span>.
                                Layout is generated by the backend from the active preset; the dashboard never edits it.
                            </div>
                            {!warehouse ? (
                                <div style={{ fontSize: 12, color: P.warning }}>No world state received yet — start the fleet.</div>
                            ) : (
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 6, fontSize: 12 }}>
                                    <span style={{ color: P.textMuted }}>Dimensions</span>
                                    <span>{warehouse.width} × {warehouse.height} m</span>
                                    <span style={{ color: P.textMuted }}>Roster (AMRs)</span>
                                    <span>{warehouse.roster.length}</span>
                                    <span style={{ color: P.textMuted }}>Shelves</span>
                                    <span>{warehouse.shelves.length}</span>
                                    <span style={{ color: P.textMuted }}>Charging pads</span>
                                    <span>{warehouse.chargingPads.length}</span>
                                    <span style={{ color: P.textMuted }}>Delivery docks</span>
                                    <span>{warehouse.deliveryDocks.length}</span>
                                </div>
                            )}
                            {warehouse && (
                                <>
                                    <div style={{ ...S.sectionTitle, marginTop: 16 }}>DELIVERY DOCKS</div>
                                    <div style={{ fontSize: 12, color: P.textMuted }}>
                                        {(warehouse.deliveryDocks.map((d) => d.id).join(', ')) || '—'}
                                    </div>
                                    <div style={{ ...S.sectionTitle, marginTop: 16 }}>CHARGING PADS</div>
                                    <div style={{ fontSize: 12, color: P.textMuted }}>
                                        {(warehouse.chargingPads.map((p) => p.id).join(', ')) || '—'}
                                    </div>
                                    <div style={{ ...S.sectionTitle, marginTop: 16 }}>SHELF RACKS</div>
                                    <div style={{ fontSize: 12, color: P.textMuted }}>
                                        {(warehouse.shelves.map((s) => s.id).join(', ')) || '—'}
                                    </div>
                                    <div style={{ fontSize: 10, color: P.textDim, marginTop: 12 }}>
                                        The browser never builds warehouses — this view is the coordinator's authoritative geometry.
                                    </div>
                                </>
                            )}
                        </div>
                    )}

                    {tab === 'AUCTION' && (
                        <div>
                            <div style={S.sectionTitle}>AUCTION ENGINE</div>
                            <div style={{ fontSize: 11, color: P.textMuted, marginTop: 8, lineHeight: 1.7 }}>
                                The dashboard never decides winners. It subscribes to bid traffic and mirrors the
                                coordinator's auction outcome (backend/common/auction.py is authoritative).
                            </div>
                            <div style={{ ...S.sectionTitle, marginTop: 18 }}>SERVER AUCTION</div>
                            <div style={{ fontSize: 12, color: P.text, marginTop: 6, lineHeight: 1.6 }}>
                                The coordinator collects bids from all robots, keeps its own bid, runs {`select_winner`} at the
                                deadline and announces the winner. The server is the single source of truth.
                            </div>
                            <div style={{ ...S.sectionTitle, marginTop: 18 }}>P2P AUCTION</div>
                            <div style={{ fontSize: 12, color: P.text, marginTop: 6, lineHeight: 1.6 }}>
                                The coordinator only announces the task. Every robot broadcasts its own bid and runs the same
                                {` select_winner`} locally; the winner self-commits on <span style={{ color: P.info }}>auction/commit</span>.
                                The server acts as a passive ledger. Retry attempts reuse the same auctionId attempt counter.
                            </div>
                            <div style={{ ...S.sectionTitle, marginTop: 18 }}>OPERATIONAL CONSTANTS</div>
                            <div style={{ fontSize: 12, color: P.textMuted, marginTop: 6 }}>
                                Deadline, commit window, retry budget and conflict rules are tuned on the backend
                                (<span style={{ color: P.info }}>backend/common/auction.py</span>).
                            </div>
                        </div>
                    )}

                    {tab === 'INFRASTRUCTURE' && <InfrastructureSettings fleet={fleet} />}

                    {tab === 'DEVELOPER' && <DeveloperTab fleet={fleet} debug={debug} />}
                </div>
            </div>
        </div>
    );
}