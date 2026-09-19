import { useTheme } from '../theme/ThemeContext.jsx';
import { ui } from '../theme/ui.js';

export default function AuctionPanel({ fleet, onSelectTask, maxAuctions = 12 }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const recent = fleet.auctions.slice(0, maxAuctions);
    const liveBids = [...fleet.liveBids.entries()];
    const auctionMode = fleet.auctionMode || 'SERVER_AUCTION';

    return (
        <div style={{ padding: 12 }}>
            <div style={S.sectionTitle}>{auctionMode === 'P2P_AUCTION' ? 'P2P' : 'SERVER'} AUCTION</div>
            <div style={{ fontSize: 10, color: P.textDim, marginBottom: 8 }}>
                Auctions are run by the fleet coordinator — this panel only mirrors the live bid traffic
                and completed results from Zenoh.
            </div>

            {liveBids.length > 0 && (
                <div style={{ fontSize: 10, color: P.warning, marginBottom: 8 }}>
                    <div>Live bids:</div>
                    {liveBids.map(([taskId, bids]) => (
                        <div key={taskId} style={{ color: P.textMuted }}>
                            {taskId}: {[...bids.values()].map((b) => (
                                <span key={b.robotId}>
                                    {b.robotId}{b.bid === null ? `:${b.reason || 'n/a'}` : `:${b.bid.toFixed(2)}`}{' '}
                                </span>
                            ))}
                        </div>
                    ))}
                </div>
            )}

            {recent.length === 0 && (
                <div style={{ fontSize: 11, color: P.textDim }}>No completed auctions yet</div>
            )}
            {recent.map((a, idx) => (
                <AuctionRow key={`${a.taskId}-${a.time}-${idx}`} a={a} onSelectTask={onSelectTask} />
            ))}
        </div>
    );
}

function AuctionRow({ a, onSelectTask }) {
    const { palette: P } = useTheme();
    return (
        <div
            onClick={() => onSelectTask && onSelectTask(a.taskId)}
            style={{
                padding: 6,
                marginBottom: 4,
                borderRadius: 3,
                cursor: onSelectTask ? 'pointer' : 'default',
                border: `1px solid ${P.border}`,
                background: P.surfaceElevated,
                fontSize: 10,
            }}
        >
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 'bold', color: P.accent }}>{a.taskId}</span>
                <span style={{ color: a.winner ? P.success : P.textMuted }}>
                    {a.winner ? `→ ${a.winner}` : (a.reason || 'no winner')}
                </span>
            </div>
            <div style={{ color: P.textMuted, marginTop: 2, lineHeight: 1.5 }}>
                {a.bids.map((b) => (
                    <span key={b.robotId}>
                        <span style={{ color: b.robotId === a.winner ? P.success : 'inherit' }}>
                            {b.robotId}{b.bid === null ? `:${b.reason || 'n/a'}` : `:${b.bid}`}
                        </span>{' · '}
                    </span>
                ))}
            </div>
        </div>
    );
}