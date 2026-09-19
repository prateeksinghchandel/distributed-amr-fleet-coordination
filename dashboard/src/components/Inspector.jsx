import { useTheme } from '../theme/ThemeContext.jsx';
import { ui, taskCounts, taskColor } from '../theme/ui.js';
import { useFleetManager } from '../hooks/useFleetManager.js';

export default function Inspector({ fleet, selection, mode, onSelectRobot, onCancelTask }) {
    const { palette: P } = useTheme();
    const S = ui(P);

    let body;
    if (selection && selection.type === 'robot') {
        const robot = fleet.robotsList.find((r) => r.id === selection.id);
        body = robot ? <RobotInspector robot={robot} /> : <FleetOverview fleet={fleet} mode={mode} onSelectRobot={onSelectRobot} />;
    } else if (selection && selection.type === 'task') {
        const task = fleet.tasksList.find((t) => t.id === selection.id);
        body = task ? <TaskInspector task={task} fleet={fleet} onCancelTask={onCancelTask} /> : <FleetOverview fleet={fleet} mode={mode} onSelectRobot={onSelectRobot} />;
    } else {
        body = <FleetOverview fleet={fleet} mode={mode} onSelectRobot={onSelectRobot} />;
    }

    return (
        <div
            style={{
                width: 300,
                minWidth: 300,
                background: P.surface,
                borderLeft: `1px solid ${P.border}`,
                color: P.text,
                overflowY: 'auto',
                padding: 12,
                fontFamily: 'monospace',
            }}
        >
            <div style={{ ...S.panelHeader, marginBottom: 12 }}>
                <span style={S.sectionTitle}>INSPECTOR</span>
            </div>
            {body}
        </div>
    );
}

function FleetOverview({ fleet, mode, onSelectRobot }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const counts = taskCounts(fleet.tasksList);
    const robots = fleet.robotsList;
    const online = robots.filter((r) => r.online).length;
    const moving = robots.filter((r) => r.online && r.status && r.status.startsWith('MOVING')).length;
    const idle = robots.filter((r) => r.online && (r.status === 'IDLE')).length;
    const charging = robots.filter((r) => r.online && r.status === 'CHARGING').length;
    const auctionMode = fleet.auctionMode || 'SERVER_AUCTION';
    const blocked = robots.filter((r) => r.online && r.blocked).length;

    return (
        <>
            <div style={S.sectionTitle}>FLEET OVERVIEW</div>
            <div style={{ fontSize: 12, marginTop: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 6 }}>
                <span style={{ color: P.textMuted }}>Robots</span><span>{robots.length}</span>
                <span style={{ color: P.textMuted }}>Online</span><span style={{ color: P.success }}>{online}</span>
                <span style={{ color: P.textMuted }}>Moving</span><span>{moving}</span>
                <span style={{ color: P.textMuted }}>Idle</span><span>{idle}</span>
                <span style={{ color: P.textMuted }}>Charging</span><span style={{ color: P.success }}>{charging}</span>
                <span style={{ color: P.textMuted }}>Blocked</span><span style={{ color: blocked ? P.danger : P.textMuted }}>{blocked}</span>
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 18 }}>TASKS</div>
            <div style={{ fontSize: 12, marginTop: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 6 }}>
                <span style={{ color: P.textMuted }}>Pending</span><span style={{ color: P.warning }}>{counts.pending}</span>
                <span style={{ color: P.textMuted }}>Active</span><span style={{ color: P.info }}>{counts.active}</span>
                <span style={{ color: P.textMuted }}>Completed</span><span style={{ color: P.success }}>{counts.completed}</span>
                <span style={{ color: P.textMuted }}>Failed</span><span style={{ color: counts.failed ? P.danger : P.textMuted }}>{counts.failed}</span>
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 18 }}>CONFIGURATION</div>
            <div style={{ fontSize: 12, marginTop: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 6 }}>
                <span style={{ color: P.textMuted }}>Auction</span><span>{auctionMode === 'P2P_AUCTION' ? 'P2P' : 'SERVER'}</span>
                <span style={{ color: P.textMuted }}>Mode</span><span>{mode}</span>
            </div>
            <div style={{ fontSize: 11, color: P.textDim, marginTop: 16, lineHeight: 1.6 }}>
                Select a robot or task to inspect. Recent robots:
            </div>
            <div style={{ marginTop: 6 }}>
                {fleet.robotsList.map((r) => (
                    <button key={r.id} style={{ ...S.button(), margin: '0 6px 6px 0' }} onClick={() => onSelectRobot(r.id)}>
                        {r.id}
                    </button>
                ))}
            </div>
        </>
    );
}

function RobotInspector({ robot }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const { command, busy } = useFleetManager();
    const stopBusy = busy[`POST:/api/amrs/${robot.id}/stop`] || busy[`POST:/api/amrs/${robot.id}/restart`];
    const heading = (Math.round(robot.heading * 180 / Math.PI) % 360 + 360) % 360;

    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={S.dot(robot.online ? robot.color : P.textDim)} />
                <span style={{ fontWeight: 'bold', fontSize: 16, color: robot.color }}>{robot.id}</span>
                <span style={{ fontSize: 11, color: robot.online ? P.success : P.danger }}>
                    {robot.online ? 'ONLINE' : 'OFFLINE'}
                </span>
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>BATTERY</div>
            <div style={{ fontSize: 20, fontWeight: 'bold', color: robot.battery < 20 ? P.danger : P.text }}>{Math.round(robot.battery)}%</div>

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>STATUS</div>
            <div style={{ fontSize: 13, color: robot.blocked ? P.danger : P.info }}>
                {robot.status}{robot.blocked ? ' ⚠ BLOCKED' : ''}
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>CURRENT TASK</div>
            <div style={{ fontSize: 13 }}>
                {robot.currentTaskId
                    ? <span style={{ color: P.info }}>{robot.currentTaskId}</span>
                    : <span style={{ color: P.textDim }}>—</span>}
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>POSITION</div>
            <div style={{ fontSize: 12, color: P.textMuted }}>
                X {robot.x.toFixed(2)} · Y {robot.y.toFixed(2)}
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>HEADING</div>
            <div style={{ fontSize: 12, color: P.textMuted }}>{heading}°</div>

            {robot.targetX != null && (
                <>
                    <div style={{ ...S.sectionTitle, marginTop: 16 }}>TARGET</div>
                    <div style={{ fontSize: 12, color: P.textMuted }}>
                        ({robot.targetX.toFixed(2)}, {robot.targetY.toFixed(2)})
                    </div>
                </>
            )}

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>ACTIONS</div>
            <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                <button style={S.button('danger')} disabled={stopBusy} onClick={() => command(`/api/amrs/${encodeURIComponent(robot.id)}/stop`)}>
                    STOP ROBOT
                </button>
                <button style={S.button()} disabled={stopBusy} onClick={() => command(`/api/amrs/${encodeURIComponent(robot.id)}/restart`)}>
                    RESTART
                </button>
            </div>
            <div style={{ fontSize: 10, color: P.textDim, marginTop: 6 }}>
                Process-level fleet-manager commands; telemetry, status and position are live backend values.
            </div>
        </>
    );
}

function TaskInspector({ task, fleet, onCancelTask }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const bids = fleet.auctions
        .filter((a) => a.taskId === task.id)
        .slice(0, 1)
        .map((a) => a.bids || []);
    const bidList = bids[0] || [];
    const winnerBid = fleet.auctions.find((a) => a.taskId === task.id);

    const cancelable = ['ASSIGNED', 'PICKING_UP', 'DELIVERING'].includes(task.status);

    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 'bold', fontSize: 16 }}>{task.id}</span>
                <span style={S.chip(taskColor(P, task.status))}>{task.status}</span>
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 16 }}>PICKUP</div>
            <div style={{ fontSize: 12, color: P.textMuted }}>({task.pickup.x.toFixed(2)}, {task.pickup.y.toFixed(2)})</div>

            <div style={{ ...S.sectionTitle, marginTop: 12 }}>DROPOFF</div>
            <div style={{ fontSize: 12, color: P.textMuted }}>({task.dropoff.x.toFixed(2)}, {task.dropoff.y.toFixed(2)})</div>

            <div style={{ ...S.sectionTitle, marginTop: 12 }}>ASSIGNED</div>
            <div style={{ fontSize: 12, color: task.assignedRobotId ? P.info : P.textDim }}>
                {task.assignedRobotId || 'Unassigned'}
            </div>

            <div style={{ ...S.sectionTitle, marginTop: 12 }}>AUCTION</div>
            <div style={{ fontSize: 12, color: P.textMuted }}>
                {fleet.auctionMode === 'P2P_AUCTION' ? 'P2P' : 'SERVER'} · {fleet.liveBids.has(task.id) ? 'BIDDING' : 'SETTLED'}
            </div>

            {bidList.length > 0 && (
                <>
                    <div style={{ ...S.sectionTitle, marginTop: 12 }}>BIDS (backend)</div>
                    <div style={{ fontSize: 12 }}>
                        {bidList.map((b) => (
                            <div key={b.robotId} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                                <span>{b.robotId}</span>
                                <span style={{ color: winnerBid && winnerBid.winner === b.robotId ? P.success : P.textMuted }}>
                                    {b.bid == null ? '—' : b.bid}{winnerBid && winnerBid.winner === b.robotId ? ' ← WINNER' : ''}
                                </span>
                            </div>
                        ))}
                    </div>
                </>
            )}

            {cancelable && (
                <div style={{ marginTop: 16 }}>
                    <button style={{ ...S.button('danger'), width: '100%' }} onClick={() => onCancelTask(task.id)}>
                        CANCEL TASK
                    </button>
                </div>
            )}
        </>
    );
}