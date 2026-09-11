import React, { useState, useCallback, useRef } from 'react';
import WarehouseCanvas from './WarehouseCanvas.jsx';
import Sidebar from './Sidebar.jsx';
import { createWorld } from '../simulation/World.js';

const worldInstance = createWorld();

export default function Dashboard() {
    const [, setTick] = useState(0);
    const lastWorldSyncRef = useRef(0);
    const [mode, setMode] = useState('default');
    const [logs, setLogs] = useState([]);
    const [debugInfo, setDebugInfo] = useState({});
    const [cameraResetToken, setCameraResetToken] = useState(0);

    const world = worldInstance;

    const addLog = useCallback((msg) => {
        setLogs(prev => [...prev.slice(-99), `[${new Date().toLocaleTimeString()}] ${msg}`]);
    }, []);

    const handleWorldUpdate = useCallback((_w) => {
        const now = performance.now();
        if (now - lastWorldSyncRef.current > 100) {
            lastWorldSyncRef.current = now;
            setTick(t => t + 1);
        }
    }, []);

    const handleCameraChange = useCallback((info) => {
        setDebugInfo(info);
    }, []);

    const handleResetCamera = useCallback(() => {
        setCameraResetToken(t => t + 1);
        addLog('Camera reset');
    }, [addLog]);

    const handleAddRobot = useCallback(() => {
        const id = `R${world.robots.length + 1}`;
        const x = 5 + Math.random() * 40;
        const y = 3 + Math.random() * 24;
        world.addRobot(id, x, y);
        setTick(t => t + 1);
        addLog(`Robot ${id} added at (${x.toFixed(2)}, ${y.toFixed(2)})`);
    }, [addLog, world]);

    const handleLog = useCallback((msg) => {
        addLog(msg);
    }, [addLog]);

    const handleMutate = useCallback(() => {
        setTick(t => t + 1);
    }, []);

    return (
        <div style={{ display: 'flex', width: '100vw', height: '100vh', background: '#0a0a1a', color: '#e0e0e0', fontFamily: 'monospace' }}>
            <Sidebar
                world={world}
                mode={mode}
                setMode={setMode}
                onResetCamera={handleResetCamera}
                onAddRobot={handleAddRobot}
                onLog={handleLog}
                debug={debugInfo}
                onMutate={handleMutate}
            />
            <div style={{ flex: 1, position: 'relative' }}>
                <WarehouseCanvas
                    world={world}
                    mode={mode}
                    cameraResetToken={cameraResetToken}
                    onWorldUpdate={handleWorldUpdate}
                    onLog={handleLog}
                    onCameraChange={handleCameraChange}
                    onMutate={handleMutate}
                />
            </div>
            <div style={{
                width: 280, minWidth: 280, background: '#16213e', borderLeft: '2px solid #0f3460',
                padding: 12, overflowY: 'auto', fontSize: 11
            }}>
                <div style={{ color: '#e94560', fontWeight: 'bold', marginBottom: 8 }}>EVENT LOG</div>
                {logs.slice(-99).reverse().map((log, i) => (
                    <div key={i} style={{ padding: '2px 0', color: '#aaa', borderBottom: '1px solid #222', wordBreak: 'break-all' }}>{log}</div>
                ))}
            </div>
        </div>
    );
}