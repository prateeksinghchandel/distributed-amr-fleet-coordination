import { useTheme } from '../theme/ThemeContext.jsx';
import { ui, taskCounts } from '../theme/ui.js';
import RobotList from './RobotList.jsx';

export default function FleetSidebar({ fleet, selectedRobotId, onSelectRobot, onOpenTasks, onOpenAuctions, onOpenAmrs }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const counts = taskCounts(fleet.tasksList);
    const activeCount = fleet.liveBids.size;
    const auctionMode = fleet.auctionMode || 'SERVER_AUCTION';

    return (
        <div
            style={{
                width: 300,
                minWidth: 300,
                background: P.surface,
                borderRight: `1px solid ${P.border}`,
                color: P.text,
                display: 'flex',
                flexDirection: 'column',
                overflowY: 'auto',
                padding: 12,
                gap: 14,
                fontFamily: 'monospace',
            }}
        >
            <RobotList fleet={fleet} selectedRobotId={selectedRobotId} onSelect={onSelectRobot} />

            <div>
                <button
                    style={{ ...S.button('primary'), width: '100%' }}
                    onClick={onOpenAmrs}
                >
                    + ADD / MANAGE AMR
                </button>
                <div style={{ fontSize: 10, color: P.textDim, marginTop: 4 }}>
                    Register a new AMR process, or start/stop/restart/remove existing ones.
                </div>
            </div>

            <div>
                <div style={S.panelHeader}>
                    <span style={S.sectionTitle}>TASKS</span>
                </div>
                <div style={S.card}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                        <span style={{ color: P.textMuted }}>PENDING</span>
                        <span style={{ color: P.warning, fontWeight: 'bold' }}>{counts.pending}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                        <span style={{ color: P.textMuted }}>ACTIVE</span>
                        <span style={{ color: P.info, fontWeight: 'bold' }}>{counts.active}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                        <span style={{ color: P.textMuted }}>COMPLETED</span>
                        <span style={{ color: P.success, fontWeight: 'bold' }}>{counts.completed}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                        <span style={{ color: P.textMuted }}>FAILED</span>
                        <span style={{ color: counts.failed ? P.danger : P.textMuted, fontWeight: 'bold' }}>{counts.failed}</span>
                    </div>
                    <button style={{ ...S.button(), width: '100%', marginTop: 8 }} onClick={onOpenTasks}>
                        VIEW TASKS
                    </button>
                </div>
            </div>

            <div>
                <div style={S.panelHeader}>
                    <span style={S.sectionTitle}>AUCTION</span>
                </div>
                <div style={S.card}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, padding: '2px 0' }}>
                        <span style={S.dot(auctionMode === 'P2P_AUCTION' ? P.success : P.info)} />
                        <span style={{ fontWeight: 'bold' }}>{auctionMode === 'P2P_AUCTION' ? 'P2P' : 'SERVER'}</span>
                        <span style={{ color: P.textMuted }}>- {activeCount} ACTIVE</span>
                    </div>
                    <button style={{ ...S.button(), width: '100%', marginTop: 8 }} onClick={onOpenAuctions}>
                        VIEW AUCTIONS
                    </button>
                </div>
            </div>
        </div>
    );
}