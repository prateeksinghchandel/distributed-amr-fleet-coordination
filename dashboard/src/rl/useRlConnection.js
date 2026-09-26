/**
 * useRlConnection — WebSocket + REST client for the Python RL training server.
 *
 * The Python backend is the source of truth for training state. This hook
 * streams that state into React:
 *
 *   * a WebSocket to `/rl/ws` receives the server's event stream
 *     (status / metrics / snapshot / episode / update / evaluation / step /
 *      log / error). Snapshots are produced by the trainer at its own pace.
 *   * REST endpoints (`/rl/status`, `/rl/scenarios`, `/rl/checkpoints`,
 *     `/rl/snapshot`, `/rl/command`) are used for command acks and refreshes.
 *
 * Commands are sent over REST POST so each one returns a deterministic ack.
 * The WS reconnects with exponential backoff if the server restarts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const API_BASE = '/rl';

const leagueCommands = new Set([
    'league_start', 'league_stop', 'league_promote', 'league_vs_pool',
]);

const wsUrl = () => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${window.location.host}${API_BASE}/ws`;
};

async function apiFetch(path, options) {
    const res = await fetch(`${API_BASE}${path}`, options);
    let body = null;
    try {
        body = await res.json();
    } catch {
        /* non-JSON error body */
    }
    if (!res.ok) {
        throw new Error((body && (body.error || body.message)) ||
            `request failed (${res.status})`);
    }
    return body;
}

const pushCap = (list, item, cap) => {
    const next = [item, ...list];
    if (next.length > cap) next.length = cap;
    return next;
};

export function useRlConnection({ pollIntervalMs = 3000 } = {}) {
    const [connected, setConnected] = useState(false);
    const [error, setError] = useState(null);
    const [status, setStatus] = useState(null);
    const [metrics, setMetrics] = useState(null);
    const [snapshot, setSnapshot] = useState(null);
    const [lastStep, setLastStep] = useState(null);
    const [episodes, setEpisodes] = useState([]);
    const [updates, setUpdates] = useState([]);
    const [evaluations, setEvaluations] = useState([]);
    const [logs, setLogs] = useState([]);
    const [checkpoints, setCheckpoints] = useState([]);
    const [scenarios, setScenarios] = useState(null);
    const [lastAck, setLastAck] = useState(null);
    const [busy, setBusy] = useState(false);
    const [league, setLeague] = useState(null);
    const [leagueHistory, setLeagueHistory] = useState([]);
    const [launcher, setLauncher] = useState(null); // {alive, pid, logTail, args} | null

    const wsRef = useRef(null);
    const aliveRef = useRef(true);
    const retryRef = useRef(0);
    const reconnTimerRef = useRef(null);
    const pollTimerRef = useRef(null);

    useEffect(() => {
        aliveRef.current = true;
        return () => {
            aliveRef.current = false;
            if (wsRef.current) wsRef.current.close();
            if (reconnTimerRef.current) clearTimeout(reconnTimerRef.current);
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        };
    }, []);

    const refreshScenarios = useCallback(async () => {
        try {
            setScenarios(await apiFetch('/scenarios'));
        } catch { /* server starting up */ }
    }, []);

    const refreshCheckpoints = useCallback(async () => {
        try {
            const body = await apiFetch('/checkpoints');
            setCheckpoints(body.checkpoints || []);
        } catch { /* non-fatal */ }
    }, []);

    const refreshStatus = useCallback(async (opts = {}) => {
        try {
            const body = await apiFetch('/status');
            setStatus(body);
            if (opts.snapshot !== false) {
                try {
                    setSnapshot(await apiFetch('/snapshot'));
                } catch { /* no snapshot yet */ }
            }
            if (body && body.type === 'metrics') setMetrics(body);
        } catch {
            if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
                setConnected(false);
            }
        }
    }, []);

    const refreshAll = useCallback(async () => {
        await Promise.all([refreshScenarios(), refreshCheckpoints(), refreshStatus()]);
    }, [refreshScenarios, refreshCheckpoints, refreshStatus]);

    // The launcher middleware lives under /_studio/* on the Vite dev/preview
    // server (NOT the /rl proxy that forwards to the Python server).
    const launcherFetch = useCallback(async (path, options) => {
        const res = await fetch(path, options);
        let body = null;
        try {
            body = await res.json();
        } catch {
            body = null;
        }
        if (!res.ok) {
            throw new Error((body && body.error) ||
                `launcher request failed (${res.status})`);
        }
        return body;
    }, []);

    const refreshLauncher = useCallback(async () => {
        try {
            setLauncher(await launcherFetch('/_studio/rl-launcher'));
        } catch { /* dev server launched without the launcher plugin */ }
    }, [launcherFetch]);

    const launchServer = useCallback(async (args = {}) => {
        const body = await launcherFetch('/_studio/rl-launch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(args),
        });
        setLauncher((prev) => ({ ...prev, ...body, args }));
        return body;
    }, [launcherFetch]);

    const stopServer = useCallback(async () => {
        const body = await launcherFetch('/_studio/rl-stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        setLauncher((prev) => ({ ...prev, ...body }));
        return body;
    }, [launcherFetch]);

    useEffect(() => {
        let cancelled = false;
        const connect = () => {
            if (!aliveRef.current || cancelled) return;
            let ws;
            try {
                ws = new WebSocket(wsUrl());
            } catch {
                scheduleReconnect();
                return;
            }
            wsRef.current = ws;

            ws.onopen = () => {
                retryRef.current = 0;
                setConnected(true);
                refreshAll();
                refreshLauncher();
                setPollTimer();
            };

            ws.onmessage = (ev) => {
                if (cancelled) return;
                let msg;
                try {
                    msg = JSON.parse(ev.data);
                } catch {
                    return;
                }
                handleMessage(msg);
            };

            ws.onclose = () => {
                if (cancelled) return;
                setConnected(false);
                scheduleReconnect();
            };

            ws.onerror = () => {
                ws.close();
            };
        };

        const scheduleReconnect = () => {
            if (!aliveRef.current || cancelled) return;
            const delay = Math.min(8000, 600 * 2 ** retryRef.current);
            retryRef.current += 1;
            reconnTimerRef.current = setTimeout(connect, delay);
        };

        const setPollTimer = () => {
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
            pollTimerRef.current = setInterval(
                () => refreshStatus(), pollIntervalMs);
        };

        const handleMessage = (msg) => {
            switch (msg.type) {
                case 'status':
                    setStatus(msg);
                    break;
                case 'metrics':
                    setMetrics(msg);
                    break;
                case 'snapshot':
                    setSnapshot(msg);
                    if (msg.status) setStatus(msg.status);
                    break;
                case 'step':
                    setLastStep(msg);
                    break;
                case 'episode':
                    setEpisodes((prev) => pushCap(prev, msg, 200));
                    break;
                case 'update':
                    setUpdates((prev) => pushCap(prev, msg, 120));
                    break;
                case 'evaluation':
                    setEvaluations((prev) => pushCap(prev, msg, 20));
                    break;
                case 'checkpoint':
                    refreshCheckpoints();
                    break;
                case 'league':
                    setLeague(msg);
                    if (msg.event === 'vs_pool') {
                        setLeagueHistory((prev) => pushCap(prev, msg, 50));
                    }
                    break;
                case 'log':
                    setLogs((prev) => pushCap(prev, msg, 300));
                    break;
                case 'error':
                    setError(msg.message || 'training server error');
                    break;
                case 'ack':
                    setLastAck(msg);
                    if (msg.ok && msg.command) refreshStatus({ snapshot: true });
                    break;
                default:
                    break;
            }
        };

        connect();
        return () => {
            cancelled = true;
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        };
    }, [refreshAll, refreshStatus, refreshCheckpoints, refreshLauncher, pollIntervalMs]);

    const send = useCallback(async (command, args = {}) => {
        setBusy(true);
        try {
            const body = await apiFetch('/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command, args }),
            });
            setLastAck(body);
            // Pull fresh status/snapshot immediately after a control op.
            refreshStatus({ snapshot: true });
            if (body.ok && ['save_checkpoint', 'delete_checkpoint', 'load_checkpoint'].includes(command)) {
                refreshCheckpoints();
            }
            if (body.ok && command in leagueCommands) {
                setLeague(body.result || null);
            }
            return body;
        } catch (err) {
            setError(err.message);
            throw err;
        } finally {
            setBusy(false);
        }
    }, [refreshStatus, refreshCheckpoints]);

    const clearError = useCallback(() => setError(null), []);

    return useMemo(() => ({
        connected,
        status,
        metrics,
        snapshot,
        lastStep,
        episodes,
        updates,
        evaluations,
        logs,
        checkpoints,
        scenarios,
        league,
        leagueHistory,
        lastAck,
        busy,
        error,
        clearError,
        launcher,
        launchServer,
        stopServer,
        refreshLauncher,
        send,
        refreshStatus,
        refreshCheckpoints,
        refreshScenarios,
    }), [
        connected, status, metrics, snapshot, lastStep, episodes, updates,
        evaluations, logs, checkpoints, scenarios, lastAck, busy, error,
        league, leagueHistory, launcher,
        clearError, send, launchServer, stopServer, refreshLauncher,
        refreshStatus, refreshCheckpoints, refreshScenarios,
    ]);
}