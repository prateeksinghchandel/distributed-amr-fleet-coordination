import React, { useRef, useEffect, useCallback } from 'react';
import { Renderer } from '../rendering/renderer.js';
import { getGridSpacing } from '../rendering/coordinates.js';

export default function WarehouseCanvas({ fleet, cameraResetToken, onCameraChange }) {
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

    useEffect(() => {
        fleetRef.current = fleet;
    }, [fleet]);

    useEffect(() => {
        onCameraChangeRef.current = onCameraChange;
    }, [onCameraChange]);

    useEffect(() => {
        const canvas = canvasRef.current;
        const renderer = new Renderer(canvas);
        renderer.resize();
        rendererRef.current = renderer;
        cameraStateRef.current = { ...renderer.camera };

        const loop = () => {
            const fleetRefCurrent = fleetRef.current;
            const scene = fleetRefCurrent.getScene();
            renderer.render(scene, mouseWorldRef.current.x, mouseWorldRef.current.y, null);
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

        const selected = fleetRef.current.selectRobotAt(worldPos.x, worldPos.y);
        if (selected) return;

        isDraggingRef.current = true;
        dragStartRef.current = { x: e.clientX, y: e.clientY };
        camStartRef.current = { x: rendererRef.current.camera.x, y: rendererRef.current.camera.y };
    }, [getCanvasCoords, screenToWorldLocal]);

    const handleMouseMove = useCallback((e) => {
        const { x, y } = getCanvasCoords(e);
        const worldPos = screenToWorldLocal(x, y);
        mouseWorldRef.current = worldPos;

        if (isDraggingRef.current && rendererRef.current) {
            const dx = (e.clientX - dragStartRef.current.x) / rendererRef.current.camera.zoom;
            const dy = (e.clientY - dragStartRef.current.y) / rendererRef.current.camera.zoom;
            rendererRef.current.camera.x = camStartRef.current.x - dx;
            rendererRef.current.camera.y = camStartRef.current.y - dy;
        }
    }, [getCanvasCoords, screenToWorldLocal]);

    const handleMouseUp = useCallback(() => {
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

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <canvas
                ref={canvasRef}
                style={{ width: '100%', height: '100%', display: 'block', cursor: 'default' }}
            />
        </div>
    );
}