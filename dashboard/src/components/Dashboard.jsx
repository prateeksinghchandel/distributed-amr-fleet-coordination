import React, { useState, useEffect, useRef, useCallback } from 'react';
import Header from './Header.jsx';
import Sidebar from './Sidebar.jsx';
import WarehouseCanvas from './WarehouseCanvas.jsx';
import FleetPanel from './FleetPanel.jsx';
import BottomPanel from './BottomPanel.jsx';
import { createSimulation } from '../simulation/Simulation.js';

const simulation = createSimulation();

export default function Dashboard() {
    const [, setTick] = useState(0);
    const lastWorldSyncRef = useRef(0);
    const simEventsSeenRef = useRef(simulation.events.length);
    const [logs, setLogs] = useState(() =>
        simulation.events.map((e) => `[${e.time}] ${e.message}`)
    );
    const [mode, setMode] = useState('default');
    const [debug, setDebug] = useState({});
    const [cameraResetToken, setCameraResetToken] = useState(0);
    const [resetVersion, setResetVersion] = useState(0);

    useEffect(() => {
        simulation.onEvent = (entry) => {
            setLogs((prev) => [...prev.slice(-399), `[${entry.time}] ${entry.message}`]);
        };
        const pending = simulation.events.slice(simEventsSeenRef.current);
        simEventsSeenRef.current = simulation.events.length;
        if (pending.length > 0) {
            setLogs((prev) => [...prev, ...pending.map((e) => `[${e.time}] ${e.message}`)]);
        }
        return () => {
            simulation.onEvent = null;
        };
    }, []);

    const handleWorldUpdate = useCallback((_sim) => {
        const now = performance.now();
        if (now - lastWorldSyncRef.current > 100) {
            lastWorldSyncRef.current = now;
            setTick((t) => t + 1);
        }
    }, []);

    const handleMutate = useCallback(() => {
        setTick((t) => t + 1);
    }, []);

    const handleReset = useCallback(() => {
        simulation.reset();
        setResetVersion((v) => v + 1);
        setTick((t) => t + 1);
    }, []);

    const handleResetCamera = useCallback(() => {
        setCameraResetToken((t) => t + 1);
    }, []);

    return (
        <div style={{
            height: '100vh',
            width: '100vw',
            display: 'flex',
            flexDirection: 'column',
            background: '#0a0a1a',
            color: '#e0e0e0',
            fontFamily: 'monospace'
        }}>
            <Header simulation={simulation} onMutate={handleMutate} onReset={handleReset} onResetCamera={handleResetCamera} />
            <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                <Sidebar
                    key={resetVersion}
                    simulation={simulation}
                    mode={mode}
                    setMode={setMode}
                    onMutate={handleMutate}
                />
                <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
                    <WarehouseCanvas
                        simulation={simulation}
                        mode={mode}
                        cameraResetToken={cameraResetToken}
                        onWorldUpdate={handleWorldUpdate}
                        onCameraChange={setDebug}
                        onMutate={handleMutate}
                    />
                </div>
                <FleetPanel simulation={simulation} onMutate={handleMutate} />
            </div>
            <BottomPanel logs={logs} debug={debug} simulation={simulation} />
        </div>
    );
}