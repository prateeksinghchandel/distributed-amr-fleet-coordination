import React, { useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { fmtInt } from '../format.js';

export default function SelfplayPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [poolEvery, setPoolEvery] = useState('3000');
    const [episodes, setEpisodes] = useState('3');
    const [launching, setLaunching] = useState(false);

    const connected = rl.connected;
    const launcherAlive = Boolean(rl.launcher && rl.launcher.alive);
    const serverManaged = launcherAlive;

    const league = (rl.status && rl.status.league) || rl.league || {};
    const enabled = Boolean(rl.status && rl.status.league);
    const running = Boolean(league.running);
    const latest = rl.leagueHistory && rl.leagueHistory[0]
        ? rl.leagueHistory[0]
        : null;
    const error = league.error || null;

    const parsedEvery = Math.max(1, parseInt(poolEvery, 10) || 3000);
    const parsedEps = Math.max(1, parseInt(episodes, 10) || 3);

    const start = () => {
        rl.send('league_start', {
            pool_every: parsedEvery,
            vs_pool_episodes: parsedEps,
            report_every: 5.0,
        }).catch(() => {});
    };

    const toggleLaunch = () => {
        if (launcherAlive) {
            rl.stopServer().catch(() => {});
            return;
        }
        setLaunching(true);
        rl.launchServer({
            league: true,
            pool_size: 4,
            pool_every: parsedEvery,
        })
            .catch(() => {})
            .finally(() => setLaunching(false));
    };

    const serverLabelColor = connected
        ? P.success
        : (launcherAlive ? P.accent : P.textMuted);

    const serverLabel = connected
        ? 'TRAINING SERVER: CONNECTED'
        : (launcherAlive ? 'TRAINING SERVER: STARTING…' : 'TRAINING SERVER: OFF');

    return (
        <div>
            <div style={{ ...S.panelHeader, marginBottom: 6 }}>
                <span style={S.sectionTitle}>Self-play</span>
                {connected ? (running ? (
                    <span style={{ ...S.chip(P.success) }}>LEAGUE RUNNING</span>
                ) : (
                    <span style={{ ...S.chip(P.textMuted) }}>SCHEDULE STOPPED</span>
                )) : null}
                {connected && (
                    <span style={{ ...S.dim, fontSize: 10 }}>
                        gen {league.generations ?? 0}
                    </span>
                )}
            </div>

            {error && (
                <div style={{ ...S.chip(P.danger), fontSize: 10, marginBottom: 6 }}>
                    {error}
                </div>
            )}

            {/* Server life-cycle strip */}
            <div style={{
                display: 'flex',
                gap: 8,
                alignItems: 'center',
                flexWrap: 'wrap',
                marginBottom: 8,
                padding: '6px 8px',
                border: `1px solid ${P.border}`,
                borderRadius: 4,
                background: P.surfaceElevated,
            }}>
                <span style={{ ...S.dot(serverLabelColor) }} />
                <span style={{ fontWeight: 'bold', fontSize: 10, letterSpacing: 0.5, color: serverLabelColor }}>
                    {serverLabel}
                </span>
                {connected && serverManaged && (
                    <button
                        onClick={toggleLaunch}
                        style={{ ...S.button('default'), fontSize: 10, color: P.danger, borderColor: P.danger }}
                        title="Stop the launcher-spawned training server"
                    >
                        STOP SERVER
                    </button>
                )}
                {!connected && launcherAlive && (
                    <span style={{ ...S.chip(P.accent), fontSize: 10 }}>waiting for server…</span>
                )}
                {!connected && !launcherAlive && (
                    <button
                        onClick={toggleLaunch}
                        disabled={launching}
                        style={S.button('primary')}
                        title="Start the Python training server with --league (champion-vs-pool)"
                    >
                        {launching ? 'LAUNCHING…' : 'LAUNCH SERVER · --league'}
                    </button>
                )}
                {!connected && !launcherAlive && rl.launcher && rl.launcher.logTail && (
                    <span style={{ ...S.dim, fontSize: 9, maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {rl.launcher.logTail.split('\n').at(-1)}
                    </span>
                )}
                {!connected && !launcherAlive && !(rl.launcher && rl.launcher.logTail) && (
                    <span style={{ ...S.dim, fontSize: 9 }}>
                        launches via the Vite dev server (_studio/*) · manual alternative: npm run rl-server:league
                    </span>
                )}
            </div>

            {connected && (<>
            <div style={{ display: 'flex', gap: 6, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 8 }}>
                {!enabled ? (
                    <div style={{ ...S.dim, fontSize: 11, lineHeight: 1.6 }}>
                        This server is running without <span style={{ color: P.accent }}>--league</span>.
                        Stop it and relaunch via the button above (or{' '}
                        <span style={{ color: P.success }}>npm run rl-server:league</span>) to arm the
                        champion-vs-pool schedule.
                    </div>
                ) : (
                    <>
                        <div style={{ display: 'flex', flexDirection: 'column', minWidth: 90 }}>
                            <span style={S.label}>Pool every (steps)</span>
                            <input
                                value={poolEvery}
                                onChange={(e) => setPoolEvery(e.target.value)}
                                disabled={running}
                                style={S.input}
                            />
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', minWidth: 90 }}>
                            <span style={S.label}>vs episodes</span>
                            <input
                                value={episodes}
                                onChange={(e) => setEpisodes(e.target.value)}
                                disabled={running}
                                style={S.input}
                            />
                        </div>
                        {running ? (
                            <>
                                <button
                                    onClick={() => rl.send('league_promote').catch(() => {})}
                                    style={S.button('default')}
                                    title="Freeze current weights into the pool now"
                                >
                                    PROMOTE NOW
                                </button>
                                <button
                                    onClick={() => rl.send('league_vs_pool', { episodes: parsedEps }).catch(() => {})}
                                    style={S.button('default')}
                                    title="Run a champion-vs-pool match now"
                                >
                                    RUN VS
                                </button>
                                <button
                                    onClick={() => rl.send('league_stop').catch(() => {})}
                                    style={{ ...S.button('default'), color: P.danger, borderColor: P.danger }}
                                >
                                    STOP
                                </button>
                            </>
                        ) : (
                            <button
                                onClick={start}
                                style={S.button('primary')}
                                title="Start the promotion schedule"
                            >
                                START LEAGUE
                            </button>
                        )}
                    </>
                )}
            </div>

            <div style={{ fontSize: 10, color: P.textMuted, marginBottom: 10, lineHeight: 1.6 }}>
                Every {league.scheduled_pool_every ?? parsedEvery} sim steps the current weights are frozen into
                a pool of former champions (oldest evicted); training scenes then spawn those frozen peers to
                dodge. A deterministic 2-robot match against each pool member is run per promotion.
            </div>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <div style={{
                    flex: '1 1 260px',
                    background: P.surfaceElevated,
                    border: `1px solid ${P.border}`,
                    borderRadius: 4,
                    padding: 8,
                }}>
                    <div style={{ fontSize: 10, letterSpacing: 1, fontWeight: 'bold', color: P.accent, marginBottom: 4 }}>
                        OPPONENT POOL ({league.pool ? league.pool.length : 0})
                    </div>
                    {(league.pool || []).length === 0 ? (
                        <div style={{ ...S.dim, fontSize: 11 }}>Empty — the first promotion seeds it.</div>
                    ) : (
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                            <tbody>
                                {(league.pool || []).map((m) => (
                                    <tr key={`${m.name}-${m.generation ?? 'seed'}`} style={{ borderTop: `1px solid ${P.border}` }}>
                                        <td style={{ padding: '2px 6px', fontWeight: 'bold', color: P.text }}>{m.name}</td>
                                        <td style={{ padding: '2px 6px', color: P.textMuted, textAlign: 'right' }}>
                                            {m.source === 'seed' ? 'imported' : `gen ${m.generation}`}
                                            {m.steps != null ? ` · @${fmtInt(m.steps)}` : ''}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                <div style={{
                    flex: '1 1 260px',
                    background: P.surfaceElevated,
                    border: `1px solid ${P.border}`,
                    borderRadius: 4,
                    padding: 8,
                }}>
                    <div style={{ fontSize: 10, letterSpacing: 1, fontWeight: 'bold', color: P.accent, marginBottom: 4 }}>
                        LAST VS-POOL MATCH
                    </div>
                    {!latest ? (
                        <div style={{ ...S.dim, fontSize: 11 }}>No match yet.</div>
                    ) : (
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                            <tbody>
                                {(latest.opponents || []).map((r) => (
                                    <tr key={r.opponent} style={{ borderTop: `1px solid ${P.border}` }}>
                                        <td style={{ padding: '2px 6px', color: P.text }}>{r.opponent}</td>
                                        <td style={{ padding: '2px 6px', textAlign: 'right', fontWeight: 'bold',
                                            color: r.success_rate >= 50 ? P.success : P.danger }}>
                                            {r.success_rate}%
                                        </td>
                                    </tr>
                                ))}
                                <tr>
                                    <td style={{ padding: '2px 6px', color: P.textMuted }}>
                                        avg · {latest.summary ? latest.summary.opponents : 0} opponents
                                    </td>
                                    <td style={{ padding: '2px 6px', textAlign: 'right', fontWeight: 'bold', color: P.text }}>
                                        {latest.summary ? latest.summary.avg_success_rate : '—'}%
                                    </td>
                                </tr>
                                <tr>
                                    <td style={{ padding: '2px 6px', color: P.textMuted }}>generation</td>
                                    <td style={{ padding: '2px 6px', textAlign: 'right', color: P.text }}>
                                        {latest.generation}
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    )}
                </div>
            </div>

            {(league.history && league.history.length > 0) && (
                <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10, fontSize: 10 }}>
                    <thead>
                        <tr style={{ color: P.textMuted, textAlign: 'right' }}>
                            <th style={{ textAlign: 'left', padding: '3px 6px' }}>gen</th>
                            <th style={{ textAlign: 'right', padding: '3px 6px' }}>steps</th>
                            <th style={{ textAlign: 'right', padding: '3px 6px' }}>opponent avg %</th>
                        </tr>
                    </thead>
                    <tbody>
                        {league.history.slice().reverse().map((h) => (
                            <tr key={`${h.generation}-${h.steps}`} style={{ borderTop: `1px solid ${P.border}` }}>
                                <td style={{ padding: '2px 6px', color: P.text }}>{h.generation}</td>
                                <td style={{ padding: '2px 6px', textAlign: 'right', color: P.textMuted }}>{fmtInt(h.steps)}</td>
                                <td style={{ padding: '2px 6px', textAlign: 'right', fontWeight: 'bold',
                                    color: h.avg_success_rate >= 50 ? P.success : P.danger }}>
                                    {h.avg_success_rate}%
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
            </>)}
        </div>
    );
}