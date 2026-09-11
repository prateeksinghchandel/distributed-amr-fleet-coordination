import React, { useRef, useEffect, useCallback } from 'react';
import { Renderer } from '../rendering/renderer.js';

export default function WarehouseCanvas({ world, mode, cameraResetToken, onWorldUpdate, onLog, onCameraChange, onMutate }) {
    const canvasRef = useRef(null);
    const rendererRef = useRef(null);
    const worldRef = useRef(world);
    const animFrameRef = useRef(null);
    const lastTimeRef = useRef(0);
    const mouseWorldRef = useRef({ x: null, y: null });
    const isDraggingRef = useRef(false);
    const dragStartRef = useRef({ x: 0, y: 0 });
    const camStartRef = useRef({ x: 0, y: 0 });
    const modeRef = useRef(mode);
    const cameraStateRef = useRef({ x: 0, y: 0, zoom: 1 });
    const fpsCounterRef = useRef({ count: 0, last: 0 });
    const obstacleDragRef = useRef(null);
    const obstacleMoveRef = useRef(null);
    const onWorldUpdateRef = useRef(onWorldUpdate);
    const onCameraChangeRef = useRef(onCameraChange);

    useEffect(() => {
        modeRef.current = mode;
    }, [mode]);

    useEffect(() => {
        worldRef.current = world;
    }, [world]);

    useEffect(() => {
        onWorldUpdateRef.current = onWorldUpdate;
    }, [onWorldUpdate]);

    useEffect(() => {
        onCameraChangeRef.current = onCameraChange;
    }, [onCameraChange]);

    useEffect(() => {
        const canvas = canvasRef.current;
        const renderer = new Renderer(canvas);
        renderer.resize();
        rendererRef.current = renderer;
        cameraStateRef.current = { ...renderer.camera };

        const loop = (timestamp) => {
            if (!lastTimeRef.current) lastTimeRef.current = timestamp;
            const dt = Math.min((timestamp - lastTimeRef.current) / 1000, 0.05);
            lastTimeRef.current = timestamp;

            worldRef.current.update(dt);
            onWorldUpdateRef.current(worldRef.current);

            renderer.render(worldRef.current, mouseWorldRef.current.x, mouseWorldRef.current.y, obstacleDragRef.current);

            cameraStateRef.current = { ...renderer.camera };

            fpsCounterRef.current.count++;
            const now = performance.now();
            if (now - fpsCounterRef.current.last >= 1000) {
                onCameraChangeRef.current({
                    ...cameraStateRef.current,
                    fps: fpsCounterRef.current.count,
                    canvasSize: `${canvas.width}×${canvas.height}`,
                    worldSize: `${worldRef.current.width}×${worldRef.current.height}`,
                    gridSpacing: renderer.camera.getGridSpacing(),
                    mouseWorld: mouseWorldRef.current
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

    const handleMouseDown = useCallback((e) => {
        if (e.button !== 0) return;
        const { x, y } = getCanvasCoords(e);
        const worldPos = screenToWorldLocal(x, y);

        if (modeRef.current === 'add-task') {
            const existing = worldRef.current.tasks.find(t =>
                Math.abs(t.x - worldPos.x) < 0.3 && Math.abs(t.y - worldPos.y) < 0.3
            );
            if (!existing) {
                worldRef.current.addTask(worldPos.x, worldPos.y);
                onWorldUpdate(worldRef.current);
                const task = worldRef.current.tasks[worldRef.current.tasks.length - 1];
                onLog(`Task ${task.id} added at (${worldPos.x.toFixed(2)}, ${worldPos.y.toFixed(2)})`);
            }
            return;
        }

        if (modeRef.current === 'move-robot') {
            const robot = worldRef.current.getSelectedRobot();
            if (robot) {
                robot.setTarget(worldPos.x, worldPos.y);
                onLog(`${robot.id} target set to (${worldPos.x.toFixed(2)}, ${worldPos.y.toFixed(2)})`);
            }
            return;
        }

        if (modeRef.current === 'add-obstacle') {
            obstacleDragRef.current = { startX: worldPos.x, startY: worldPos.y, endX: worldPos.x, endY: worldPos.y };
            return;
        }

        if (modeRef.current === 'move-obstacle') {
            const obstacle = worldRef.current.getObstacleAt(worldPos.x, worldPos.y);
            if (obstacle) {
                obstacleMoveRef.current = { id: obstacle.id, offsetX: worldPos.x - obstacle.x, offsetY: worldPos.y - obstacle.y };
                worldRef.current.selectedObstacleId = obstacle.id;
                onMutate();
                return;
            }
        }

        const obstacle = worldRef.current.getObstacleAt(worldPos.x, worldPos.y);
        if (obstacle) {
            worldRef.current.selectedObstacleId = obstacle.id;
            onLog(`Selected obstacle ${obstacle.id}`);
            return;
        }

        const robot = worldRef.current.getRobotAt(worldPos.x, worldPos.y);
        if (robot) {
            worldRef.current.selectedRobotId = robot.id;
            onLog(`Selected robot ${robot.id}`);
            return;
        }

        isDraggingRef.current = true;
        dragStartRef.current = { x: e.clientX, y: e.clientY };
        camStartRef.current = { x: rendererRef.current.camera.x, y: rendererRef.current.camera.y };
    }, [getCanvasCoords, screenToWorldLocal, onWorldUpdate, onLog, onMutate]);

    const handleMouseMove = useCallback((e) => {
        const { x, y } = getCanvasCoords(e);
        const worldPos = screenToWorldLocal(x, y);
        mouseWorldRef.current = worldPos;

        if (obstacleDragRef.current) {
            obstacleDragRef.current.endX = worldPos.x;
            obstacleDragRef.current.endY = worldPos.y;
            return;
        }

        if (obstacleMoveRef.current && rendererRef.current) {
            const obs = worldRef.current.getObstacle(obstacleMoveRef.current.id);
            if (obs) {
                obs.x = worldPos.x - obstacleMoveRef.current.offsetX;
                obs.y = worldPos.y - obstacleMoveRef.current.offsetY;
            }
            return;
        }

        if (isDraggingRef.current && rendererRef.current) {
            const dx = (e.clientX - dragStartRef.current.x) / rendererRef.current.camera.zoom;
            const dy = (e.clientY - dragStartRef.current.y) / rendererRef.current.camera.zoom;
            rendererRef.current.camera.x = camStartRef.current.x - dx;
            rendererRef.current.camera.y = camStartRef.current.y - dy;
        }
    }, [getCanvasCoords, screenToWorldLocal]);

    const handleMouseUp = useCallback((_e) => {
        if (obstacleDragRef.current) {
            const { startX, startY, endX, endY } = obstacleDragRef.current;
            const x = Math.min(startX, endX);
            const y = Math.min(startY, endY);
            const w = Math.abs(endX - startX);
            const h = Math.abs(endY - startY);
            if (w > 0.2 && h > 0.2) {
                const obs = worldRef.current.addObstacle(x, y, w, h);
                onWorldUpdate(worldRef.current);
                onLog(`Obstacle ${obs.id} added at (${x.toFixed(2)}, ${y.toFixed(2)}) ${w.toFixed(1)}×${h.toFixed(1)}m`);
            }
            obstacleDragRef.current = null;
        }
        if (obstacleMoveRef.current) {
            obstacleMoveRef.current = null;
        }
        isDraggingRef.current = false;
    }, [onWorldUpdate, onLog]);

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

    const cursor = mode === 'add-task' || mode === 'move-robot' || mode === 'add-obstacle' ? 'crosshair' : mode === 'move-obstacle' ? 'grab' : 'default';

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <canvas
                ref={canvasRef}
                style={{ width: '100%', height: '100%', display: 'block', cursor }}
            />
        </div>
    );
}
