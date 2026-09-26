import React, { useState, useEffect, useRef, useCallback } from 'react';
import Header from './Header.jsx';
import FleetSidebar from './FleetSidebar.jsx';
import Inspector from './Inspector.jsx';
import EventLog from './EventLog.jsx';
import WarehouseCanvas from './WarehouseCanvas.jsx';
import SettingsModal from './settings/SettingsModal.jsx';
import StartupScreen from './StartupScreen.jsx';
import TaskDrawer from './drawers/TaskDrawer.jsx';
import AuctionDrawer from './drawers/AuctionDrawer.jsx';
import AmrDrawer from './drawers/AmrDrawer.jsx';
import { useFleetManager } from '../hooks/useFleetManager.js';
import { createDistributedFleetState } from '../distributed/DistributedFleetState.js';
import { useTheme } from '../theme/ThemeContext.jsx';
import { OPERATING_MODES } from './ModeSelector.jsx';

const fleet = createDistributedFleetState();
const STORAGE_MODE = 'amr_mode';

function loadMode() {
    try {
        const m = localStorage.getItem(STORAGE_MODE);
        if (m && OPERATING_MODES[m]) return m;
    } catch { /* private mode */ }
    return 'NORMAL';
}

export default function Dashboard({ onOpenRl, onOpenSelfplay }) {
    const { palette: P } = useTheme();
    const { status, managerAvailable } = useFleetManager();
    const [, setTick] = useState(0);
    const [debug, setDebug] = useState({});
    const [mode, setMode] = useState(loadMode);
    const [drawer, setDrawer] = useState(null); // 'tasks' | 'auctions' | 'amrs' | null
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [settingsTab, setSettingsTab] = useState('GENERAL');
    const [selection, setSelection] = useState(null); // {type:'robot'|'task', id}
    const lastWorldSyncRef = useRef(0);
    const lastSelectedRef = useRef(fleet.selectedRobotId);

    useEffect(() => {
        const unsubscribe = fleet.subscribe(() => {
            const now = performance.now();
            if (now - lastWorldSyncRef.current > 100) {
                lastWorldSyncRef.current = now;
                setTick((t) => t + 1);
            }
            if (fleet.selectedRobotId !== lastSelectedRef.current) {
                lastSelectedRef.current = fleet.selectedRobotId;
                setSelection({ type: 'robot', id: fleet.selectedRobotId });
            }
        });
        return () => unsubscribe();
    }, []);

    const handleModeChange = useCallback((m) => {
        setMode(m);
        try {
            localStorage.setItem(STORAGE_MODE, m);
        } catch { /* private mode */ }
    }, []);

    const fleetRunning = Boolean(status && status.ready && managerAvailable);

    if (!fleetRunning) {
        return <StartupScreen fleet={fleet} mode={mode} onModeChange={handleModeChange} onOpenRl={onOpenRl} onOpenSelfplay={onOpenSelfplay} />;
    }

    const openSettings = (tab = 'GENERAL') => {
        setSettingsTab(tab);
        setSettingsOpen(true);
    };

    const effectiveSelection = selection && selection.type === 'task'
        ? (fleet.tasksList.some((t) => t.id === selection.id) ? selection : { type: 'robot', id: fleet.selectedRobotId })
        : { type: 'robot', id: fleet.selectedRobotId };

    return (
        <div style={{
            height: '100vh',
            width: '100vw',
            display: 'flex',
            flexDirection: 'column',
            background: P.background,
            color: P.text,
            fontFamily: 'monospace',
        }}>
            <Header
                fleet={fleet}
                mode={mode}
                onModeChange={handleModeChange}
                onOpenSettings={() => openSettings()}
                onOpenInfrastructure={() => openSettings('INFRASTRUCTURE')}
                onOpenRl={onOpenRl}
                onOpenSelfplay={onOpenSelfplay}
            />

            <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                <FleetSidebar
                    fleet={fleet}
                    selectedRobotId={fleet.selectedRobotId}
                    onSelectRobot={(id) => { fleet.setSelectedRobot(id); setSelection({ type: 'robot', id }); }}
                    onOpenTasks={() => setDrawer('tasks')}
                    onOpenAuctions={() => setDrawer('auctions')}
                    onOpenAmrs={() => setDrawer('amrs')}
                />
                <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
                    <WarehouseCanvas
                        fleet={fleet}
                        onCameraChange={setDebug}
                        palette={P}
                    />
                </div>
                <Inspector
                    fleet={fleet}
                    selection={effectiveSelection}
                    mode={mode}
                    onSelectRobot={(id) => { fleet.setSelectedRobot(id); setSelection({ type: 'robot', id }); }}
                    onSelectTask={(id) => setSelection({ type: 'task', id })}
                    onCancelTask={(id) => fleet.cancelTask(id)}
                />
            </div>

            <EventLog fleet={fleet} height={190} />

            {drawer === 'tasks' && (
                <TaskDrawer
                    fleet={fleet}
                    onClose={() => setDrawer(null)}
                    onSelectTask={(id) => { setSelection({ type: 'task', id }); setDrawer(null); }}
                />
            )}
            {drawer === 'auctions' && (
                <AuctionDrawer
                    fleet={fleet}
                    onClose={() => setDrawer(null)}
                    onSelectTask={(id) => { setSelection({ type: 'task', id }); setDrawer(null); }}
                />
            )}
            {drawer === 'amrs' && (
                <AmrDrawer status={status} onClose={() => setDrawer(null)} />
            )}

            {settingsOpen && (
                <SettingsModal
                    fleet={fleet}
                    debug={debug}
                    initialTab={settingsTab}
                    onClose={() => setSettingsOpen(false)}
                />
            )}
        </div>
    );
}