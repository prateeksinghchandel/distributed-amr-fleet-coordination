import React from 'react';

const sectionHeader = { fontSize: 11, color: '#888', marginBottom: 6, letterSpacing: 1 };

export default function AuctionPanel({ fleet }) {
    const recent = fleet.auctions.slice(0, 6);
    const liveBids = [...fleet.liveBids.entries()];

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
            <div style={sectionHeader}>P2P AUCTION</div>
            <div style={{ fontSize: 10, color: '#666', marginBottom: 8 }}>
                Auctions are run by the coordinator — this panel only mirrors the live bid traffic
                and completed results from Zenoh.
            </div>

            {liveBids.length > 0 && (
                <div style={{ fontSize: 10, color: '#ffd43b', marginBottom: 8 }}>
                    <div>Live bids:</div>
                    {liveBids.map(([taskId, bids]) => (
                        <div key={taskId} style={{ color: '#aaa' }}>
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
                <div style={{ fontSize: 11, color: '#666' }}>No completed auctions yet</div>
            )}
            {recent.map((a) => (
                <AuctionRow key={`${a.taskId}-${a.time}`} a={a} />
            ))}
        </div>
    );
}

function AuctionRow({ a }) {
    return (
        <div style={{
            padding: 6,
            marginBottom: 4,
            borderRadius: 3,
            border: '1px solid #0f3460',
            background: '#0a0a1a',
            fontSize: 10
        }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 'bold', color: '#e94560' }}>{a.taskId}</span>
                <span style={{ color: a.winner ? '#00ff88' : '#aaa' }}>
                    {a.winner ? `→ ${a.winner}` : (a.reason || 'no winner')}
                </span>
            </div>
            <div style={{ color: '#aaa', marginTop: 2, lineHeight: 1.5 }}>
                {a.bids.map((b) => (
                    <span key={b.robotId}>
                        <span style={{ color: b.robotId === a.winner ? '#00ff88' : 'inherit' }}>
                            {b.robotId}{b.bid === null ? `:${b.reason || 'n/a'}` : `:${b.bid}`}
                        </span>{' · '}
                    </span>
                ))}
            </div>
        </div>
    );
}