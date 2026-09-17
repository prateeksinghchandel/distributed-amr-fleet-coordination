import React, { useState, useEffect, useRef, useCallback } from 'react';
import Header from './Header.jsx';
import Sidebar from './Sidebar.jsx';
import WarehouseCanvas from './WarehouseCanvas.jsx';
import FleetPanel from './FleetPanel.jsx';
import BottomPanel from './BottomPanel.jsx';
import FleetControl from './FleetControl.jsx';
import { createDistributedFleetState } from '../distributed/DistributedFleetState.js';

const fleet = createDistributedFleetState();

export default function Dashboard() {
    const [, setTick] = useState(0);
    const [logs, setLogs] = useState(() => fleet.logs.map((e) => `[${e.time}] ${e.message}`));
    const logsSeenRef = useRef(fleet.logs.length);
    const [debug, setDebug] = useState({});
    const [cameraResetToken, setCameraResetToken] = useState(0);
    const lastWorldSyncRef = useRef(0);

    useEffect(() => {
        const unsubscribe = fleet.subscribe(() => {
            const now = performance.now();
            if (now - lastWorldSyncRef.current > 100) {
                lastWorldSyncRef.current = now;
                setTick((t) => t + 1);
            }
            const newCount = fleet.logs.length;
            if (newCount !== logsSeenRef.current) {
                const extra = fleet.logs.slice(logsSeenRef.current);
                logsSeenRef.current = newCount;
                if (extra.length > 0) {
                    setLogs((prev) => [...prev.slice(-399), ...extra.map((e) => `[${e.time}] ${e.message}`)]);
                }
            }
        });

        return () => unsubscribe();
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
            <Header fleet={fleet} onResetCamera={handleResetCamera} />
            <FleetControl />
            <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                <Sidebar fleet={fleet} />
                <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
                    <WarehouseCanvas
                        fleet={fleet}
                        cameraResetToken={cameraResetToken}
                        onCameraChange={setDebug}
                    />
                </div>
                <FleetPanel fleet={fleet} />
            </div>
            <BottomPanel logs={logs} debug={debug} fleet={fleet} />
        </div>
    );
}