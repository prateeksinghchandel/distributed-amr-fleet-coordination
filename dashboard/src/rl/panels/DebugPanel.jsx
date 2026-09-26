import React from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { withAlpha } from '../../theme/palette.js';
import { fmt } from '../format.js';

const LIDAR_RAYS = 36;
const MAX_PEERS = 6;
const PEER_OBS_RANGE = 6.0;
const LIDAR_RANGE = 8.0;
const STEER_LIMIT = 0.9;
const MAX_ANGULAR = 1.8;
const DT = 0.1;
const REVERSE_SCALE = 0.3;

export default function DebugPanel({ rl }) {
    const { palette: P } = useTheme();
    const snap = rl.snapshot;
    const obs = snap && snap.observation;
    const action = snap && snap.action;
    const safety = snap && snap.safety;

    if (!snap || !obs) {
        return (
            <div style={{ fontSize: 11, color: P.textMuted, padding: '4px 0' }}>
                No observation yet — press START or STEP. The display environment mirrors step for step what training sees.
            </div>
        );
    }

    const rlRobot = (snap.robots || []).find((r) => r.id === snap.rl_agent);
    const lidarRange = (snap.lidar && snap.lidar.range) || LIDAR_RANGE;

    const throttle = action[0];
    const steer = action[1];
    const MAX_SPEED = 1.5; // RobotSpec.max_speed in the env
    const linear = throttle >= 0 ? throttle * MAX_SPEED : throttle * REVERSE_SCALE * MAX_SPEED;
    const desiredDtheta = steer * STEER_LIMIT;
    const headingRate = Math.abs(desiredDtheta) > MAX_ANGULAR * DT ? MAX_ANGULAR : Math.abs(desiredDtheta) / DT;

    const rows = (data) =>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <tbody>
                {data.map(([label, value, color]) => (
                    <tr key={label}>
                        <td style={{ color: P.textMuted, padding: '1px 4px 1px 0', whiteSpace: 'nowrap' }}>{label}</td>
                        <td style={{ color: color || P.text, padding: '1px 0', textAlign: 'right', fontWeight: 'bold' }}>{value}</td>
                    </tr>
                ))}
            </tbody>
        </table>;

    // Valid peer slots from observation [44:68].
    const peerSlots = [];
    for (let i = 0; i < MAX_PEERS; i++) {
        const base = 44 + i * 4;
        const dx = obs[base], dy = obs[base + 1], vx = obs[base + 2], vy = obs[base + 3];
        const empty = Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6;
        peerSlots.push({ dx, dy, vx, vy, empty });
    }

    const nearestLidar = action && obs[41] != null ? obs[41] * lidarRange : null;
    const goalDistObs = obs[36] != null ? obs[36] * lidarRange : null;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontWeight: 'bold', fontSize: 11, color: P.accent, letterSpacing: 1, textTransform: 'uppercase' }}>
                    Agent sees
                </span>
                <span style={{ fontSize: 10, color: P.textMuted }}>
                    step {snap.step} · t={fmt(snap.time)}s
                </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                    <div style={{ fontSize: 10, color: P.textMuted, marginBottom: 2 }}>Action → motion</div>
                    {rows([
                        ['throttle', fmt(throttle)],
                        ['linear v', `${fmt(linear)} m/s`],
                        ['steer', fmt(steer)],
                        ['heading rate', `${fmt(headingRate, 1)} rad/s`],
                    ])}
                    <div style={{ fontSize: 10, color: P.textMuted, marginTop: 6, marginBottom: 2 }}>Safety layer</div>
                    {rows([
                        ['override', safety.overridden ? 'ACTIVE' : (rl.status && rl.status.safety === 'off' ? 'off (disabled)' : 'passive')],
                        ['guard', safety.guard || 'none'],
                        ['reason', safety.reason || '—'],
                    ].map(([l, v]) => [l, v, l === 'override' && String(v).includes('ACTIVE') ? P.warning : undefined]))}
                </div>

                <div>
                    <div style={{ fontSize: 10, color: P.textMuted, marginBottom: 2 }}>Observation summary</div>
                    {rows([
                        ['goal dist', `${fmt(goalDistObs)} m`, goalDistObs < 1 ? P.success : undefined],
                        ['nearest lidar', `${fmt(nearestLidar)} m`, nearestLidar < 0.7 ? P.danger : nearestLidar < 1.5 ? P.warning : P.success],
                        ['ego vx', `${fmt(rlRobot ? rlRobot.vx : 0)} m/s`],
                        ['ego vy', `${fmt(rlRobot ? rlRobot.vy : 0)} m/s`],
                        ['nav state', rlRobot ? (rlRobot.nav_state || '—') : '—'],
                        ['nav reason', rlRobot ? (rlRobot.nav_reason || '—') : '—'],
                    ])}
                </div>
            </div>

            <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 10, color: P.textMuted, marginBottom: 2 }}>
                    Lidar ({LIDAR_RAYS} rays, range {fmt(lidarRange)} m)
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(18, 1fr)', gap: 1 }}>
                    {(snap.lidar && snap.lidar.rays
                        ? snap.lidar.rays
                        : Array.from({ length: LIDAR_RAYS }, (_, i) => ({ distance: obs[i] * lidarRange }))
                    ).map((ray, i) => {
                        const d = ray.distance;
                        const frac = Math.max(0, Math.min(1, d / lidarRange));
                        const height = 4 + 20 * frac;
                        const isMin = d === Math.min(...(snap.lidar ? snap.lidar.rays.map((r) => r.distance) : [Infinity]));
                        return (
                            <div key={i} title={`ray ${i}: ${fmt(d)} m`} style={{
                                display: 'flex',
                                alignItems: 'flex-end',
                                justifyContent: 'center',
                                height: 24,
                            }}>
                                <div style={{
                                    width: 5,
                                    height,
                                    background: d < 0.7 ? P.danger : d < 1.5 ? P.warning : withAlpha(P.success, 0.5 + 0.5 * frac),
                                    outline: isMin ? `1px solid ${P.text}` : 'none',
                                    borderRadius: 1,
                                }} />
                            </div>
                        );
                    })}
                </div>
                {(rlRobot) && (
                    <div style={{ fontSize: 10, color: P.textMuted, marginTop: 2 }}>
                        lidar origin follows RL agent heading; shortest ray highlighted. Obstacles behind unreachable rays read as full range.
                    </div>
                )}
            </div>

            <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 10, color: P.textMuted, marginBottom: 2 }}>
                    Nearby robots ({MAX_PEERS} slots, range {PEER_OBS_RANGE} m) — normalized observation [44:68]
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                    <tbody>
                        {peerSlots.map((peer, i) => (
                            <tr key={i} style={{ borderBottom: `1px solid ${P.border}`, }}>
                                <td style={{ color: P.textMuted, padding: '1px 0' }}>slot {i + 1}</td>
                                {peer.empty ? (
                                    <td colSpan={4} style={{ color: P.textDim, padding: '1px 0', textAlign: 'right' }}>
                                        — empty —
                                    </td>
                                ) : (
                                    <>
                                        <td style={{ padding: '1px 0', textAlign: 'right', color: P.info }}>dx {fmt(peer.dx * PEER_OBS_RANGE)}m</td>
                                        <td style={{ padding: '1px 0', textAlign: 'right', color: P.info }}>dy {fmt(peer.dy * PEER_OBS_RANGE)}m</td>
                                        <td style={{ padding: '1px 0', textAlign: 'right', color: P.text }}>vx {fmt(peer.vx)}</td>
                                        <td style={{ padding: '1px 0', textAlign: 'right', color: P.text }}>vy {fmt(peer.vy)}</td>
                                    </>
                                )}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                <span style={{ color: P.textMuted }}>Observation vector ({obs.length} floats)</span>
                <span style={{ color: P.textMuted, fontSize: 9 }}>{obs.map((v) => v.toFixed(2)).join(' ')}</span>
            </div>
        </div>
    );
}