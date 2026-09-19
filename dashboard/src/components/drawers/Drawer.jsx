import { useTheme } from '../../theme/ThemeContext.jsx';
import { ui } from '../../theme/ui.js';

export default function Drawer({ title, subtitle, onClose, width = 480, children }) {
    const { palette: P } = useTheme();
    const S = ui(P);

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40 }}>
            <div
                style={{ position: 'absolute', inset: 0, background: P.overlay }}
                onClick={onClose}
            />
            <div
                style={{
                    position: 'absolute',
                    top: 0,
                    right: 0,
                    bottom: 0,
                    width,
                    maxWidth: '90vw',
                    background: P.surface,
                    borderLeft: `1px solid ${P.borderStrong}`,
                    display: 'flex',
                    flexDirection: 'column',
                    color: P.text,
                    fontFamily: 'monospace',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: `1px solid ${P.border}` }}>
                    <span style={S.sectionTitle}>{title}</span>
                    {subtitle && <span style={{ fontSize: 11, color: P.textMuted }}>{subtitle}</span>}
                    <span style={{ flex: 1 }} />
                    <button style={S.button()} onClick={onClose}>CLOSE ✕</button>
                </div>
                <div style={{ flex: 1, overflowY: 'auto' }}>{children}</div>
            </div>
        </div>
    );
}