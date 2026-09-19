import { useTheme } from '../theme/ThemeContext.jsx';
import { ui } from '../theme/ui.js';

export const OPERATING_MODES = {
    NORMAL: { label: 'NORMAL', hint: 'Algorithmic planning + collision avoidance' },
    AI_ROBOT: { label: 'AI ROBOT', hint: 'Neural-network robot controller (extension point)' },
    AI_PLANNER: { label: 'AI PLANNER', hint: 'AI global path planning / rerouting (extension point)' },
};

export default function ModeSelector({ mode, onModeChange }) {
    const { palette: P } = useTheme();
    const S = ui(P);

    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ display: 'flex', gap: 4 }}>
                {Object.entries(OPERATING_MODES).map(([key, m]) => (
                    <button
                        key={key}
                        title={m.hint}
                        onClick={() => onModeChange(key)}
                        style={S.tab(mode === key)}
                    >
                        {m.label}
                    </button>
                ))}
            </div>
            {mode !== 'NORMAL' && (
                <span style={S.chip(P.success)} title="The independent collision-safety layer is always active and cannot be disabled by AI control.">
                    AI CONTROL ON · SAFETY ACTIVE
                </span>
            )}
        </div>
    );
}