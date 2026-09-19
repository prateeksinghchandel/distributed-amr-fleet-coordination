import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';
import DebugPanel from '../DebugPanel.jsx';

export default function DeveloperTab({ fleet, debug }) {
    const { palette: P } = useTheme();
    const S = ui(P);
    return (
        <div>
            <div style={S.sectionTitle}></div>
            <div style={{ fontSize: 11, color: P.textDim, marginBottom: 8 }}>
                Read-only diagnostics. All numbers below are live state mirrored from the Python fleet.
            </div>
            <DebugPanel debug={debug} fleet={fleet} />
        </div>
    );
}