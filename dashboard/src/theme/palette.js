/**
 * palette.js — the single source for every visual color in the dashboard.
 *
 * Components must NEVER hardcode hex values. They read the resolved palette
 * from `useTheme()` (ThemeContext). Adding a new theme only requires a new
 * entry here — no component edits.
 */

export const ACCENT_COLORS = {
    crimson: '#e94560',
    electric: '#00c8ff',
    violet: '#9d4edd',
    amber: '#ffb703',
    emerald: '#00b37e',
};

export const PALETTES = {
    dark: {
        name: 'dark',
        background: '#0a0a1a',
        surface: '#16213e',
        surfaceElevated: '#1d2b52',
        surfaceActive: '#0f3460',
        border: '#0f3460',
        borderStrong: '#2a3a66',
        text: '#e8ecf8',
        textMuted: '#8a8a9a',
        textDim: '#5a6a88',
        success: '#00ff88',
        warning: '#ffd43b',
        danger: '#ff5c74',
        info: '#00c8ff',
        canvasBg: '#101022',
        gridLine: '#23233f',
        gridLabel: '#4a4a6a',
        selectionOutline: '#ffffff',
        overlay: 'rgba(5, 5, 15, 0.82)',
        scrollTrack: '#16213e',
        scrollThumb: '#0f3460',
        logWarn: '#ffb703',
        logErr: '#ff7b8c',
        shelfFill: '#2c251f',
        shelfStroke: '#ffa94d',
        shelfSlot: 'rgba(255, 169, 77, 0.35)',
        obstacleFill: '#555577',
        obstacleStroke: '#8a8ab0',
        obstacleText: '#aaaacc',
    },
    light: {
        name: 'light',
        background: '#e9edf6',
        surface: '#ffffff',
        surfaceElevated: '#f4f7fd',
        surfaceActive: '#e2e9f8',
        border: '#c8d2e5',
        borderStrong: '#9fb0cf',
        text: '#192338',
        textMuted: '#5c6a86',
        textDim: '#8a94ac',
        success: '#12a35e',
        warning: '#d99a00',
        danger: '#d63447',
        info: '#0077c8',
        canvasBg: '#dde4f0',
        gridLine: '#c3cee3',
        gridLabel: '#7d8aa6',
        selectionOutline: '#192338',
        overlay: 'rgba(10, 12, 25, 0.45)',
        scrollTrack: '#dfe6f2',
        scrollThumb: '#aebad4',
        logWarn: '#b57600',
        logErr: '#c22f43',
        shelfFill: '#3a3526',
        shelfStroke: '#c7853a',
        shelfSlot: 'rgba(199, 133, 58, 0.35)',
        obstacleFill: '#8a8fa8',
        obstacleStroke: '#d63447',
        obstacleText: '#5a6478',
    },
};

/**
 * Return a full semantic palette for one theme + accent.
 * `accent` may be one of ACCENT_COLORS keys or an arbitrary hex.
 */
export function paletteFor(themeName, accent) {
    const base = PALETTES[themeName] || PALETTES.dark;
    const accentColor = ACCENT_COLORS[accent] || accent || '#e94560';
    return {
        ...base,
        accent: accentColor,
        accentContrast: '#ffffff',
        accentSoft: withAlpha(accentColor, 0.15),
        accentStrong: withAlpha(accentColor, 0.45),
        accentBorder: withAlpha(accentColor, 0.65),
    };
}

export function withAlpha(hex, alpha) {
    const clean = String(hex || '').replace('#', '');
    if (clean.length !== 6) return hex;
    const r = parseInt(clean.slice(0, 2), 16);
    const g = parseInt(clean.slice(2, 4), 16);
    const b = parseInt(clean.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}