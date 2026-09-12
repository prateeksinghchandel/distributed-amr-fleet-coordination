import React, { useRef, useEffect, useCallback } from 'react';
import { Renderer } from '../rendering/renderer.js';

export default function WarehouseCanvas({ simulation, mode, cameraResetToken, onWorldUpdate, onCameraChange, onMutate }) {
    const canvasRef = useRef(null);
    const rendererRef = useRef(null);
    const simRef = useRef(simulation);
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
    const onMutateRef = useRef(onMutate);

    useEffect(() => {
        modeRef.current = mode;
    }, [mode]);

    useEffect(() => {
        simRef.current = simulation;
    }, [simulation]);

    useEffect(() => {
        onWorldUpdateRef.current = onWorldUpdate;
    }, [onWorldUpdate]);

    useEffect(() => {
        onCameraChangeRef.current = onCameraChange;
    }, [onCameraChange]);

    useEffect(() => {
        onMutateRef.current = onMutate;
    }, [onMutate]);

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

            const sim = simRef.current;
            if (sim.running) {
                sim.step(dt * sim.speed);
            }

            renderer.render(sim, mouseWorldRef.current.x, mouseWorldRef.current.y, obstacleDragRef.current);
            onWorldUpdateRef.current(sim);

            cameraStateRef.current = { ...renderer.camera };

            fpsCounterRef.current.count++;
            const now = performance.now();
            if (now - fpsCounterRef.current.last >= 1000) {
                onCameraChangeRef.current({
                    ...cameraStateRef.current,
                    fps: fpsCounterRef.current.count,
                    canvasSize: `${canvas.width}×${canvas.height}`,
                    worldSize: `${sim.width}×${sim.height}`,
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
        const sim = simRef.current;

        if (modeRef.current === 'move-robot') {
            const robot = sim.getSelectedRobot();
            if (robot) {
                sim.moveRobotTo(robot.id, worldPos.x, worldPos.y);
                onMutateRef.current();
            }
            return;
        }

        if (modeRef.current === 'add-obstacle') {
            obstacleDragRef.current = { startX: worldPos.x, startY: worldPos.y, endX: worldPos.x, endY: worldPos.y };
            return;
        }

        if (modeRef.current === 'move-obstacle') {
            const obstacle = sim.getObstacleAt(worldPos.x, worldPos.y);
            if (obstacle) {
                obstacleMoveRef.current = { id: obstacle.id, offsetX: worldPos.x - obstacle.x, offsetY: worldPos.y - obstacle.y };
                sim.selectedObstacleId = obstacle.id;
                onMutateRef.current();
                return;
            }
        }

        const obstacle = sim.getObstacleAt(worldPos.x, worldPos.y);
        if (obstacle) {
            sim.selectedObstacleId = obstacle.id;
            onMutateRef.current();
            return;
        }

        const robot = sim.getRobotAt(worldPos.x, worldPos.y);
        if (robot) {
            sim.setSelectedRobot(robot.id);
            onMutateRef.current();
            return;
        }

        isDraggingRef.current = true;
        dragStartRef.current = { x: e.clientX, y: e.clientY };
        camStartRef.current = { x: rendererRef.current.camera.x, y: rendererRef.current.camera.y };
    }, [getCanvasCoords, screenToWorldLocal]);

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
            const sim = simRef.current;
            const obs = sim.getObstacle(obstacleMoveRef.current.id);
            if (obs) {
                sim.moveObstacle(obs.id, worldPos.x - obstacleMoveRef.current.offsetX, worldPos.y - obstacleMoveRef.current.offsetY);
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
                simRef.current.addObstacle(x, y, w, h);
                onMutateRef.current();
            }
            obstacleDragRef.current = null;
        }
        if (obstacleMoveRef.current) {
            obstacleMoveRef.current = null;
        }
        isDraggingRef.current = false;
    }, []);

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

    const cursor = mode === 'move-robot' || mode === 'add-obstacle' ? 'crosshair' : mode === 'move-obstacle' ? 'grab' : 'default';

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <canvas
                ref={canvasRef}
                style={{ width: '100%', height: '100%', display: 'block', cursor }}
            />
        </div>
    );
}