import React from 'react';

const sectionHeader = { fontSize: 11, color: '#888', marginBottom: 6, letterSpacing: 1 };

export default function AuctionPanel({ simulation, onMutate }) {
    const recent = simulation.auctions.slice(-6).reverse();

    const toggle = (e) => {
        simulation.setAuctionEnabled(e.target.checked);
        onMutate();
    };

    return (
        <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
            <div style={sectionHeader}>P2P AUCTION</div>
            <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, cursor: 'pointer' }}>
                <input type="checkbox" checked={simulation.auctionEnabled} onChange={toggle} />
                Auto-assign via auction
            </label>
            <div style={{ fontSize: 10, color: '#aaa', marginBottom: 8 }}>
                Queue: {simulation.auctionQueue.length} · Waiting: {simulation.waitingTasks.size} · In flight: {simulation.auctionInFlightId || '—'} · Bus: {simulation.bus.pendingCount()}
            </div>
            <div style={{ fontSize: 10, color: '#666', marginBottom: 8 }}>
                Generator tasks are auctioned fleet-wide (lowest bid wins); manual tasks wait for the Assign button.
            </div>
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