import { useCallback, useEffect, useRef, useState } from 'react';

const API_BASE = (import.meta.env && import.meta.env.VITE_FLEET_MANAGER_URL) || '/api';

/**
 * Hook for talking to the local Fleet Manager service.
 * The Fleet Manager (backend/fleet_manager) owns all OS process lifecycle;
 * the dashboard only issues commands and displays state.
 */
export function useFleetManager(options = {}) {
    const { pollInterval = 1500 } = options;
    const [status, setStatus] = useState(null);
    const [info, setInfo] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState({});
    const [lastCommandError, setLastCommandError] = useState(null);
    const mountedRef = useRef(true);

    const refresh = useCallback(async () => {
        try {
            const [statusRes, infoRes] = await Promise.all([
                fetch(`${API_BASE}/status`),
                fetch(`${API_BASE}/info`),
            ]);
            if (!statusRes.ok || !infoRes.ok) {
                throw new Error(`fleet manager responded ${statusRes.status}/${infoRes.status}`);
            }
            const [s, i] = await Promise.all([statusRes.json(), infoRes.json()]);
            if (mountedRef.current) {
                setStatus(s);
                setInfo(i);
                setError(null);
            }
        } catch (e) {
            if (mountedRef.current) {
                setError(e.message || String(e));
                setStatus(null);
                setInfo(null);
            }
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        refresh();
        const timer = setInterval(refresh, pollInterval);
        return () => {
            mountedRef.current = false;
            clearInterval(timer);
        };
    }, [refresh, pollInterval]);

    const command = useCallback(async (path, { method = 'POST', body } = {}) => {
        const key = `${method}:${path}`;
        setBusy((b) => ({ ...b, [key]: true }));
        setLastCommandError(null);
        try {
            const res = await fetch(`${API_BASE}${path}`, {
                method,
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const msg = data.error || `HTTP ${res.status}`;
                setLastCommandError(msg);
                throw new Error(msg);
            }
            await refresh();
            return data;
        } finally {
            setBusy((b) => {
                const next = { ...b };
                delete next[key];
                return next;
            });
        }
    }, [refresh]);

    const fetchLogs = useCallback(async (name, limit = 250) => {
        try {
            const res = await fetch(`${API_BASE}/logs/${encodeURIComponent(name)}?limit=${limit}`);
            if (!res.ok) return { lines: [], error: `HTTP ${res.status}` };
            return await res.json();
        } catch (e) {
            return { lines: [], error: e.message };
        }
    }, []);

    const managerAvailable = error === null && status !== null;

    return { status, info, error, busy, lastCommandError, managerAvailable, refresh, command, fetchLogs };
}