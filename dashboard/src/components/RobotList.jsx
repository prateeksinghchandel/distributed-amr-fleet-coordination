import { useTheme } from '../theme/ThemeContext.jsx';
import { ui, robotStatusColor } from '../theme/ui.js';

export default function RobotList({ fleet, selectedRobotId, onSelect }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    const robots = [...fleet.robotsList]
        .sort((a, b) => (a.online === b.online ? a.id.localeCompare(b.id) : a.online ? -1 : 1));

    return (
        <div>
            <div style={S.panelHeader}>
                <span style={S.sectionTitle}>FLEET</span>
                <span style={{ fontSize: 11, color: P.textMuted }}>
                    {robots.filter((r) => r.online).length}/{robots.length} ONLINE
                </span>
            </div>
            {robots.length === 0 && (
                <div style={{ fontSize: 12, color: P.textDim, padding: '4px 0 10px' }}>
                    Waiting for telemetry…
                </div>
            )}
            {robots.map((r) => {
                const isSel = selectedRobotId === r.id;
                const statusColor = robotStatusColor(P, r);
                return (
                    <div
                        key={r.id}
                        onClick={() => onSelect(r.id)}
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                            padding: '6px 8px',
                            marginBottom: 4,
                            borderRadius: 4,
                            cursor: 'pointer',
                            background: isSel ? P.surfaceActive : P.surface,
                            border: `1px solid ${isSel ? P.accent : P.border}`,
                        }}
                    >
                        <span style={S.dot(r.online ? r.color : P.textDim)} />
                        <span style={{ fontWeight: 'bold', fontSize: 12, width: 56, color: P.text }}>{r.id}</span>
                        <span style={{ fontSize: 11, color: P.textMuted, width: 34 }}>{Math.round(r.battery)}%</span>
                        <span style={{ fontSize: 11, color: statusColor, flex: 1, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {r.blocked ? '⚠ BLOCKED' : r.status}
                        </span>
                    </div>
                );
            })}
        </div>
    );
}