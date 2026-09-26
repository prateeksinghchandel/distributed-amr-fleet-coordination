import { useState } from 'react';
import { ThemeProvider } from './theme/ThemeContext.jsx';
import Dashboard from './components/Dashboard.jsx';
import RlTrainingView from './rl/RlTrainingView.jsx';
import { useRlConnection } from './rl/useRlConnection.js';

const STORAGE_VIEW = 'amr_app_view';

function loadView() {
    try {
        const v = localStorage.getItem(STORAGE_VIEW) || '';
        if (v === 'rl' || v === 'rl-selfplay') return v;
    } catch {
        /* private mode */
    }
    return 'fleet';
}

function AppRouter() {
    const [view, setView] = useState(loadView);
    const rl = useRlConnection();

    const switchView = (next, tab) => {
        const key = next === 'rl' && tab === 'selfplay' ? 'rl-selfplay' : next;
        setView(key);
        try {
            localStorage.setItem(STORAGE_VIEW, key);
        } catch { /* private mode */ }
    };

    if (view === 'rl' || view === 'rl-selfplay') {
        return (
            <RlTrainingView
                rl={rl}
                initialTab={view === 'rl-selfplay' ? 'selfplay' : undefined}
                onBack={() => switchView('fleet')}
            />
        );
    }
    return (
        <Dashboard
            onOpenRl={() => switchView('rl')}
            onOpenSelfplay={() => switchView('rl', 'selfplay')}
        />
    );
}

export default function App() {
    return (
        <ThemeProvider>
            <AppRouter />
        </ThemeProvider>
    );
}