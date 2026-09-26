import React, { useRef, useEffect } from 'react';
import { Camera } from '../rendering/camera.js';
import { drawRlSnapshot } from './rlRenderer.js';

/**
 * RlWarehouseCanvas — renders the Python Gymnasium snapshot on a canvas with
 * pan/zoom. Rendering runs on requestAnimationFrame and always reads the
 * latest snapshot from a ref, so visualization is decoupled from the backend's
 * snapshot rate.
 */
export default function RlWarehouseCanvas({ snapshot, palette, showLidar }) {
    const canvasRef = useRef(null);
    const ctxRef = useRef(null);
    const cameraRef = useRef(null);
    const snapRef = useRef(snapshot);
    const paletteRef = useRef(palette);
    const showLidarRef = useRef(showLidar !== false);
    const animRef = useRef(null);
    const draggingRef = useRef(false);
    const dragStartRef = useRef({ x: 0, y: 0 });
    const camStartRef = useRef({ x: 0, y: 0 });

    useEffect(() => {
        snapRef.current = snapshot;
    }, [snapshot]);

    useEffect(() => {
        paletteRef.current = palette;
    }, [palette]);

    useEffect(() => {
        showLidarRef.current = showLidar !== false;
    }, [showLidar]);

    useEffect(() => {
        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        const camera = new Camera();
        ctxRef.current = ctx;
        cameraRef.current = camera;

        const resize = () => {
            const container = canvas.parentElement;
            if (!container) return;
            canvas.width = container.clientWidth;
            canvas.height = container.clientHeight;
        };
        resize();

        const observer = new ResizeObserver(resize);
        observer.observe(canvas.parentElement);

        const loop = () => {
            const snap = showLidarRef.current ? snapRef.current
                : snapRef.current && snapRef.current.lidar
                    ? { ...snapRef.current, lidar: null }
                    : snapRef.current;
            drawRlSnapshot(ctxRef.current, snap, cameraRef.current,
                           canvas.width, canvas.height, paletteRef.current);
            animRef.current = requestAnimationFrame(loop);
        };
        animRef.current = requestAnimationFrame(loop);

        return () => {
            cancelAnimationFrame(animRef.current);
            observer.disconnect();
        };
    }, []);

    const localToScreen = (e) => {
        const rect = canvasRef.current.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const handleMouseDown = (e) => {
        if (e.button !== 0) return;
        draggingRef.current = true;
        dragStartRef.current = localToScreen(e);
        camStartRef.current = { x: cameraRef.current.x, y: cameraRef.current.y };
    };

    const handleMouseMove = (e) => {
        if (!draggingRef.current) return;
        const c = cameraRef.current;
        const p = localToScreen(e);
        c.x = camStartRef.current.x - (p.x - dragStartRef.current.x) / c.zoom;
        c.y = camStartRef.current.y - (p.y - dragStartRef.current.y) / c.zoom;
    };

    const handleMouseUp = () => {
        draggingRef.current = false;
    };

    const handleWheel = (e) => {
        e.preventDefault();
        const canvas = canvasRef.current;
        const p = localToScreen(e);
        cameraRef.current.zoomAtPoint(
            e.deltaY > 0 ? -0.12 : 0.12,
            p.x,
            p.y,
            canvas.width,
            canvas.height,
        );
    };

    const resetView = () => {
        if (cameraRef.current) cameraRef.current.reset();
    };

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <canvas
                ref={canvasRef}
                style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab' }}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
                onWheel={handleWheel}
            />
            <button
                onClick={resetView}
                style={{
                    position: 'absolute',
                    right: 10,
                    bottom: 10,
                    zIndex: 5,
                    padding: '4px 10px',
                    background: 'rgba(15,23,42,0.85)',
                    color: palette ? palette.text : '#e2e8f0',
                    border: `1px solid ${palette ? palette.accent : '#38bdf8'}`,
                    borderRadius: 4,
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                    fontSize: 11,
                }}
            >
                RESET VIEW
            </button>
        </div>
    );
}