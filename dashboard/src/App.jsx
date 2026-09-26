import { useState } from 'react';
import { ThemeProvider } from './theme/ThemeContext.jsx';
import Dashboard from './components/Dashboard.jsx';
import RlTrainingView from './rl/RlTrainingView.jsx';
import { useRlConnection } from './rl/useRlConnection.js';

const STORAGE_VIEW = 'amr_app_view';

function loadView() {
    try {
        return localStorage.getItem(STORAGE_VIEW) === 'rl' ? 'rl' : 'fleet';
    } catch {
        return 'fleet';
    }
}

function AppRouter() {
    const [view, setView] = useState(loadView);
    const rl = useRlConnection();

    const switchView = (next) => {
        setView(next);
        try {
            localStorage.setItem(STORAGE_VIEW, next);
        } catch { /* private mode */ }
    };

    if (view === 'rl') {
        return <RlTrainingView rl={rl} onBack={() => switchView('fleet')} />;
    }
    return <Dashboard onOpenRl={() => switchView('rl')} />;
}

export default function App() {
    return (
        <ThemeProvider>
            <AppRouter />
        </ThemeProvider>
    );
}