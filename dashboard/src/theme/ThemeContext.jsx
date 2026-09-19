import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { ACCENT_COLORS, paletteFor } from './palette.js';

const ThemeContext = createContext(null);

const STORAGE_THEME = 'amr_theme';
const STORAGE_ACCENT = 'amr_accent';

export function ThemeProvider({ children }) {
    const [themeName, setThemeName] = useState(() => {
        try {
            return localStorage.getItem(STORAGE_THEME) || 'dark';
        } catch {
            return 'dark';
        }
    });
    const [accentName, setAccentName] = useState(() => {
        try {
            return localStorage.getItem(STORAGE_ACCENT) || 'crimson';
        } catch {
            return 'crimson';
        }
    });

    useEffect(() => {
        try {
            localStorage.setItem(STORAGE_THEME, themeName);
        } catch {
            /* private mode */
        }
    }, [themeName]);

    useEffect(() => {
        try {
            localStorage.setItem(STORAGE_ACCENT, accentName);
        } catch {
            /* private mode */
        }
    }, [accentName]);

    const palette = useMemo(() => paletteFor(themeName, accentName), [themeName, accentName]);

    return (
        <div data-theme={themeName} style={{ width: '100%', height: '100%' }}>
            <ThemeContext.Provider value={{ themeName, accentName, setThemeName, setAccentName, palette, accents: ACCENT_COLORS }}>
                {children}
            </ThemeContext.Provider>
        </div>
    );
}

export function useTheme() {
    const value = useContext(ThemeContext);
    if (!value) throw new Error('useTheme must be used inside <ThemeProvider>');
    return value;
}