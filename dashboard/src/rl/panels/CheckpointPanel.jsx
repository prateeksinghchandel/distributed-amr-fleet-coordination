import React, { useState } from 'react';
import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import { fmtBytes, fmtInt, fmt } from '../format.js';

export default function CheckpointPanel({ rl }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const [name, setName] = useState('');
    const [busyName, setBusyName] = useState(null);
    const agent = rl.status && rl.status.agent;
    const dir = rl.status && rl.status.checkpoint_dir;
    const autosaveEvery = rl.status && rl.status.autosave_every;
    const lastAutosave = rl.status && rl.status.last_autosave;

    const act = async (command, args, label) => {
        setBusyName(label);
        try {
            await rl.send(command, args);
        } catch { /* error surfaces via hook */ }
        setBusyName(null);
    };

    return (
        <div>
            <div style={{ ...S.panelHeader, marginBottom: 6 }}>
                <span style={S.sectionTitle}>Checkpoints</span>
                <span style={{ ...S.dim, fontSize: 9 }}>{dir}</span>
            </div>

            <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="checkpoint name"
                    style={{ ...S.input, flex: 1 }}
                />
                <button
                    onClick={() => act('save_checkpoint', { name }, 'save')}
                    disabled={busyName === 'save'}
                    style={{ ...S.button('primary') }}
                >
                    SAVE
                </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginBottom: 6, fontSize: 11 }}>
                <div style={{ color: P.textMuted }}>
                    model
                    {agent ? ` · ${agent.updates} updates · ${fmtInt(agent.params)} params` : ''}
                </div>
                <div style={{ color: P.textMuted, textAlign: 'right' }}>
                    {agent ? `${agent.obs_dim}→${agent.action_dim} · ${agent.device} · log_std ${fmt(agent.log_std)}` : ''}
                </div>
            </div>

            <div style={{ fontSize: 10, marginBottom: 8, lineHeight: 1.5 }}>
                <span style={{ color: lastAutosave ? P.success : P.textDim }}>
                    {lastAutosave
                        ? `auto-saved ${lastAutosave.name} @ ${fmtInt(lastAutosave.steps)} steps (${new Date(lastAutosave.time * 1000).toLocaleTimeString()})`
                        : 'no auto-save yet'}
                </span>
                <span style={{ color: P.textDim }}>
                    {' · '}every {autosaveEvery > 0 ? `${fmtInt(autosaveEvery)} sim steps` : 'off (server started with --save-every 0)'}
                </span>
            </div>

            <div style={{ maxHeight: 260, overflowY: 'auto' }}>
                {rl.checkpoints.length === 0 ? (
                    <div style={{ ...S.dim, fontSize: 11 }}>No checkpoints yet.</div>
                ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                        <tbody>
                            {rl.checkpoints.map((c) => {
                                const label = 'ckpt:' + c.name;
                                return (
                                    <tr key={c.name} style={{ borderBottom: `1px solid ${P.border}` }}>
                                        <td style={{ padding: '4px 2px', color: P.text }}>
                                            {c.name}
                                            {c.name === 'autosave.pt' && (
                                                <span style={{ ...S.chip(P.success), fontSize: 8, marginLeft: 5, padding: '0 4px' }}>AUTO</span>
                                            )}
                                        </td>
                                        <td style={{ padding: '4px 2px', color: P.textMuted, fontSize: 10 }}>
                                            {fmtBytes(c.bytes)} · {new Date(c.timestamp * 1000).toLocaleTimeString()}
                                        </td>
                                        <td style={{ padding: '3px 0', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            <button
                                                onClick={() => act('load_checkpoint', { name: c.name }, label)}
                                                disabled={rl.busy}
                                                style={{ ...S.button('default'), padding: '1px 7px', fontSize: 10, marginRight: 4 }}
                                            >
                                                LOAD
                                            </button>
                                            <button
                                                onClick={() => act('delete_checkpoint', { name: c.name }, label)}
                                                disabled={rl.busy}
                                                style={{ ...S.button('danger'), padding: '1px 7px', fontSize: 10 }}
                                            >
                                                DEL
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

            {agent && agent.log_std != null && (
                <div style={{ fontSize: 10, color: P.textDim, marginTop: 8, lineHeight: 1.5 }}>
                    Manual saves never overwrite (names get _1, _2 suffixes). Auto-save overwrites the
                    single autosave.pt while training so the model is always recoverable on this server.
                </div>
            )}
        </div>
    );
}