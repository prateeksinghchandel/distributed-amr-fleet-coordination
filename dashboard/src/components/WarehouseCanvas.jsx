import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Renderer } from '../rendering/renderer.js';
import { getGridSpacing } from '../rendering/coordinates.js';

const OBSTACLE_GRID = 0.5; // world-space snap for obstacle rectangles (m)
const MIN_OBSTACLE = 0.5;

const TOOLS = [
    { id: 'pan', label: 'PAN' },
    { id: 'obstacle', label: '+ OBSTACLE' },
    { id: 'remove', label: 'REMOVE' },
];

export default function WarehouseCanvas({ fleet, cameraResetToken, onCameraChange, palette }) {
    const canvasRef = useRef(null);
    const rendererRef = useRef(null);
    const fleetRef = useRef(fleet);
    const animFrameRef = useRef(null);
    const mouseWorldRef = useRef({ x: null, y: null });
    const isDraggingRef = useRef(false);
    const dragStartRef = useRef({ x: 0, y: 0 });
    const camStartRef = useRef({ x: 0, y: 0 });
    const cameraStateRef = useRef({ x: 0, y: 0, zoom: 1 });
    const fpsCounterRef = useRef({ count: 0, last: 0 });
    const onCameraChangeRef = useRef(onCameraChange);
    const paletteRef = useRef(palette);
    const toolRef = useRef('pan');
    const drawModeRef = useRef(false);
    const obstacleDragRef = useRef(null);

    const [tool, setTool] = useState('pan');

    useEffect(() => {
        fleetRef.current = fleet;
    }, [fleet]);

    useEffect(() => {
        toolRef.current = tool;
    }, [tool]);

    useEffect(() => {
        onCameraChangeRef.current = onCameraChange;
    }, [onCameraChange]);

    useEffect(() => {
        paletteRef.current = palette;
        if (rendererRef.current) {
            rendererRef.current.setPalette(palette);
        }
    }, [palette]);

    useEffect(() => {
        const canvas = canvasRef.current;
        const renderer = new Renderer(canvas);
        renderer.setPalette(paletteRef.current);
        renderer.resize();
        rendererRef.current = renderer;
        cameraStateRef.current = { ...renderer.camera };

        const loop = () => {
            const fleetRefCurrent = fleetRef.current;
            const scene = fleetRefCurrent.getScene();
            renderer.render(
                scene,
                mouseWorldRef.current.x,
                mouseWorldRef.current.y,
                obstacleDragRef.current
            );
            cameraStateRef.current = { ...renderer.camera };

            fpsCounterRef.current.count++;
            const now = performance.now();
            if (now - fpsCounterRef.current.last >= 1000) {
                onCameraChangeRef.current({
                    ...cameraStateRef.current,
                    fps: fpsCounterRef.current.count,
                    canvasSize: `${canvas.width}×${canvas.height}`,
                    gridSpacing: getGridSpacing(renderer.camera.zoom),
                    mouseWorld: mouseWorldRef.current,
                    fleetStats: {
                        robots: fleetRefCurrent.robotsList.length,
                        tasks: fleetRefCurrent.tasksList.length,
                        auctions: fleetRefCurrent.auctions.length,
                    }
                });
                fpsCounterRef.current.count = 0;
                fpsCounterRef.current.last = now;
            }

            animFrameRef.current = requestAnimationFrame(loop);
        };

        animFrameRef.current = requestAnimationFrame(loop);

        return () => {
            if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
        };
    }, []);

    useEffect(() => {
        if (rendererRef.current) {
            rendererRef.current.camera.reset();
        }
    }, [cameraResetToken]);

    useEffect(() => {
        const onKey = (e) => {
            if (e.key === 'Escape' && toolRef.current !== 'pan') {
                drawModeRef.current = false;
                obstacleDragRef.current = null;
                setTool('pan');
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, []);

    const getCanvasCoords = useCallback((e) => {
        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        return { x, y };
    }, []);

    const screenToWorldLocal = useCallback((screenX, screenY) => {
        if (!rendererRef.current) return { x: 0, y: 0 };
        return rendererRef.current.camera.screenToWorld(
            screenX, screenY,
            canvasRef.current.width, canvasRef.current.height
        );
    }, []);

    const warehouseBounds = useCallback(() => {
        const scene = fleetRef.current.getScene();
        if (!scene.warehouse || !scene.warehouse.width) return null;
        return { width: scene.warehouse.width, height: scene.warehouse.height };
    }, []);

    const snap = useCallback((v) => Math.round(v / OBSTACLE_GRID) * OBSTACLE_GRID, []);

    const commitObstacle = useCallback(() => {
        const drag = obstacleDragRef.current;
        obstacleDragRef.current = null;
        drawModeRef.current = false;
        if (!drag) return;
        const x = Math.min(drag.startX, drag.endX);
        const y = Math.min(drag.startY, drag.endY);
        const width = Math.abs(drag.endX - drag.startX);
        const height = Math.abs(drag.endY - drag.startY);
        if (width < MIN_OBSTACLE || height < MIN_OBSTACLE) return;
        fleetRef.current.addObstacle({ x, y, width, height });
    }, []);

    const tryRemoveObstacle = useCallback((wx, wy) => {
        const fleetState = fleetRef.current;
        const obstacle = fleetState.obstacleAt(wx, wy);
        if (obstacle) {
            fleetState.removeObstacleById(obstacle.id);
        } else if (fleetState.shelfAt(wx, wy)) {
            fleetState.addLog('Shelf racks are part of the preset — remove runtime obstacles only');
        }
    }, []);

    const handleMouseDown = useCallback((e) => {
        if (e.button !== 0) return;
        const { x, y } = getCanvasCoords(e);
        const worldPos = screenToWorldLocal(x, y);

        if (toolRef.current === 'obstacle') {
            drawModeRef.current = true;
            const bounds = warehouseBounds();
            const sx = bounds ? Math.min(snap(worldPos.x), bounds.width) : snap(worldPos.x);
            const sy = bounds ? Math.min(snap(worldPos.y), bounds.height) : snap(worldPos.y);
            obstacleDragRef.current = { startX: sx, startY: sy, endX: sx, endY: sy };
            return;
        }

        if (toolRef.current === 'remove') {
            tryRemoveObstacle(worldPos.x, worldPos.y);
            return;
        }

        const selected = fleetRef.current.selectRobotAt(worldPos.x, worldPos.y);
        if (selected) return;

        isDraggingRef.current = true;
        dragStartRef.current = { x: e.clientX, y: e.clientY };
        camStartRef.current = { x: rendererRef.current.camera.x, y: rendererRef.current.camera.y };
    }, [getCanvasCoords, screenToWorldLocal, warehouseBounds, snap, tryRemoveObstacle]);

    const handleMouseMove = useCallback((e) => {
        const { x, y } = getCanvasCoords(e);
        const worldPos = screenToWorldLocal(x, y);
        mouseWorldRef.current = worldPos;

        if (drawModeRef.current && obstacleDragRef.current) {
            const bounds = warehouseBounds();
            const ex = bounds ? Math.min(snap(worldPos.x), bounds.width) : snap(worldPos.x);
            const ey = bounds ? Math.min(snap(worldPos.y), bounds.height) : snap(worldPos.y);
            obstacleDragRef.current.endX = ex;
            obstacleDragRef.current.endY = ey;
            return;
        }

        if (isDraggingRef.current && rendererRef.current) {
            const dx = (e.clientX - dragStartRef.current.x) / rendererRef.current.camera.zoom;
            const dy = (e.clientY - dragStartRef.current.y) / rendererRef.current.camera.zoom;
            rendererRef.current.camera.x = camStartRef.current.x - dx;
            rendererRef.current.camera.y = camStartRef.current.y - dy;
        }
    }, [getCanvasCoords, screenToWorldLocal, warehouseBounds, snap]);

    const handleMouseUp = useCallback(() => {
        if (drawModeRef.current) {
            commitObstacle();
            return;
        }
        isDraggingRef.current = false;
    }, [commitObstacle]);

    const handleWheel = useCallback((e) => {
        e.preventDefault();
        if (!rendererRef.current) return;
        const rect = canvasRef.current.getBoundingClientRect();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        rendererRef.current.camera.zoomAtPoint(
            delta, e.clientX - rect.left, e.clientY - rect.top,
            canvasRef.current.width, canvasRef.current.height
        );
    }, []);

    useEffect(() => {
        const canvas = canvasRef.current;
        canvas.addEventListener('mousedown', handleMouseDown);
        canvas.addEventListener('mousemove', handleMouseMove);
        canvas.addEventListener('mouseup', handleMouseUp);
        canvas.addEventListener('mouseleave', handleMouseUp);
        canvas.addEventListener('wheel', handleWheel, { passive: false });

        return () => {
            canvas.removeEventListener('mousedown', handleMouseDown);
            canvas.removeEventListener('mousemove', handleMouseMove);
            canvas.removeEventListener('mouseup', handleMouseUp);
            canvas.removeEventListener('mouseleave', handleMouseUp);
            canvas.removeEventListener('wheel', handleWheel);
        };
    }, [handleMouseDown, handleMouseMove, handleMouseUp, handleWheel]);

    useEffect(() => {
        const canvas = canvasRef.current;
        const resizeObserver = new ResizeObserver(() => {
            if (rendererRef.current) rendererRef.current.resize();
        });
        resizeObserver.observe(canvas.parentElement);
        return () => resizeObserver.disconnect();
    }, []);

    const hint = tool === 'obstacle'
        ? 'Drag on the canvas to draw an obstacle (0.5m grid).'
        : tool === 'remove'
            ? 'Click a dashed obstacle to remove it. Shelves are part of the preset.'
            : 'Left-drag to pan. Wheel to zoom. Select a robot to inspect it.';

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <canvas
                ref={canvasRef}
                style={{
                    width: '100%',
                    height: '100%',
                    display: 'block',
                    cursor: tool === 'pan' ? 'default' : 'crosshair',
                }}
            />
            <div style={{
                position: 'absolute',
                top: 10,
                right: 10,
                zIndex: 5,
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                background: 'rgba(15, 23, 42, 0.88)',
                border: `1px solid ${palette ? palette.gridLine : '#334155'}`,
                borderRadius: 8,
                padding: 8,
                fontFamily: 'monospace',
                fontSize: 11,
            }}>
                {TOOLS.map((t) => {
                    const active = tool === t.id;
                    return (
                        <button
                            key={t.id}
                            onClick={() => {
                                drawModeRef.current = false;
                                obstacleDragRef.current = null;
                                setTool(t.id);
                            }}
                            style={{
                                background: active ? (palette ? palette.accent : '#38bdf8') : 'transparent',
                                color: active ? '#0f172a' : (palette ? palette.text : '#e2e8f0'),
                                border: `1px solid ${palette ? palette.accent : '#38bdf8'}`,
                                borderRadius: 4,
                                padding: '4px 10px',
                                cursor: 'pointer',
                                fontFamily: 'inherit',
                                fontSize: 11,
                                fontWeight: 'bold',
                            }}
                        >
                            {t.label}
                        </button>
                    );
                })}
                <div style={{ color: palette ? palette.textDim : '#94a3b8', maxWidth: 180, lineHeight: 1.4 }}>
                    {hint}
                </div>
            </div>
        </div>
    );
}