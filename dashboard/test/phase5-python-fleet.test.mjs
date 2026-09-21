/**
 * phase5-python-fleet.test.mjs — Phase 5 distributed verification.
 *
 * Spins up the REAL stack and verifies the dashboard wire path end-to-end:
 *
 *   infra  : zenohd core router (TCP) + zenoh-bridge-remote-api (WS bridge
 *            linked to the router). Reused if already running.
 *   fleet  : Python coordinator (server.server_node) + Python robot nodes,
 *            all connected to the router over TCP.
 *   client : a browser-side ConnectionManager (real @eclipse-zenoh/zenoh-ts,
 *            no mocks) connected to the WS bridge — exactly the dashboard.
 *
 * Verified:
 *   1. world/state (warehouse + roster) reaches the dashboard.
 *   2. telemetry from every roster AMR is received and decoded from the
 *      Python `{origin,type,payload}` envelope.
 *   3. the coordinator auto-dispatchs a task via auction → dashboard sees
 *      tasks/new, auction/results, tasks/assigned and a completed task.
 *   4. a dashboard CONTROL_TASK_CREATE command reaches the Python
 *      coordinator and produces a SECOND task on the bus (command path).
 *
 * Requires: tools/zenoh/{zenohd,zenoh-bridge-remote-api} and the backend
 * venv with eclipse_zenoh installed.
 */

import { spawn } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ConnectionManager, CONNECTION_STATUS } from '../src/distributed/ConnectionManager.js';
import { TOPICS } from '../src/simulation/messages/topics.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND = path.resolve(ROOT, '..', 'backend');
const TOOLS = path.join(ROOT, 'tools', 'zenoh');

function findVenvPython() {
    if (process.env.PYTHON_BIN && fs.existsSync(process.env.PYTHON_BIN)) return process.env.PYTHON_BIN;
    const candidates = [
        path.join(BACKEND, '..', '.venv', 'Scripts', 'python.exe'),
        path.join(BACKEND, '..', '.venv', 'bin', 'python'),
        path.join(BACKEND, '..', 'venv', 'Scripts', 'python.exe'),
        path.join(BACKEND, '..', 'venv', 'bin', 'python'),
    ];
    for (const c of candidates) {
        if (fs.existsSync(c)) return c;
    }
    return process.platform === 'win32' ? 'python' : 'python3';
}
const VENV_PYTHON = findVenvPython();

const WS_PORT = Number.parseInt(process.env.ZENOH_WS_PORT || '10000', 10);
const TCP_PORT = Number.parseInt(process.env.ZENOH_TCP_PORT || '7447', 10);
const WS_URL = `ws/127.0.0.1:${WS_PORT}`;      // dashboard / zenoh-ts
const TCP_URL = `tcp/127.0.0.1:${TCP_PORT}`;    // Python fleet

const PASS = [];
const errors = [];
let infra = [];
const children = [];

const check = (name, pass) => {
    if (pass) PASS.push(`PASS ${name}`);
    else errors.push(`FAIL ${name}`);
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function resolveTool(name) {
    const envKey = name.toUpperCase().replaceAll('-', '_') + '_BIN';
    if (process.env[envKey] && fs.existsSync(process.env[envKey])) return process.env[envKey];
    const exts = process.platform === 'win32' ? ['.exe', ''] : [''];
    for (const ext of exts) {
        const local = path.join(TOOLS, name + ext);
        if (fs.existsSync(local)) return local;
    }
    const pathDirs = (process.env.PATH || '').split(path.delimiter);
    for (const dir of pathDirs) {
        for (const ext of exts) {
            const cand = path.join(dir, name + ext);
            if (dir && fs.existsSync(cand)) return cand;
        }
    }
    if (name === 'zenoh-bridge-remote-api' && fs.existsSync(path.join(TOOLS, 'zenoh_plugin_remote_api.dll'))) {
        const router = resolveTool('zenohd');
        if (router) return router;
    }
    return null;
}

function portOpen(port, timeoutMs = 1500) {
    return new Promise((resolve) => {
        const sock = net.connect({ port, host: '127.0.0.1' });
        const t = setTimeout(() => { sock.destroy(); tryLocalhost(); }, timeoutMs);
        sock.on('connect', () => { clearTimeout(t); sock.end(); resolve(true); });
        sock.on('error', () => { clearTimeout(t); tryLocalhost(); });
        function tryLocalhost() {
            const s2 = net.connect({ port, host: 'localhost' });
            const t2 = setTimeout(() => { s2.destroy(); resolve(false); }, timeoutMs);
            s2.on('connect', () => { clearTimeout(t2); s2.end(); resolve(true); });
            s2.on('error', () => { clearTimeout(t2); resolve(false); });
        }
    });
}

async function pollUntil(fn, timeoutMs, intervalMs = 200) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        const value = await fn();
        if (value) return value;
        await sleep(intervalMs);
    }
    return await fn();
}

function spawnPython(args) {
    const child = spawn(VENV_PYTHON, args, {
        cwd: BACKEND,
        env: { ...process.env, PYTHONPATH: BACKEND },
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    const out = [];
    child.stdout.on('data', (d) => out.push(d.toString()));
    child.stderr.on('data', (d) => out.push(`[STDERR] ${d.toString()}`));
    children.push(child);
    child.getLog = () => out.join('');
    return child;
}

function startDaemon(bin, args, logFile) {
    const child = spawn(bin, args, { stdio: ['ignore', 'ignore', fs.openSync(logFile, 'a')] });
    child.unref();
    return child;
}

const bridge = resolveTool('zenoh-bridge-remote-api');
const router = resolveTool('zenohd');
if (!bridge || !router) {
    console.log('SKIPPED: missing zenohd or zenoh-bridge-remote-api under tools/zenoh/.');
    process.exit(0);
}
if (!fs.existsSync(VENV_PYTHON)) {
    console.log(`SKIPPED: backend venv not found at ${VENV_PYTHON}`);
    process.exit(0);
}

// --- Infrastructure (start what is not already running) ------------------------
fs.mkdirSync(path.join(ROOT, 'logs'), { recursive: true });
const routerUp = await portOpen(TCP_PORT, 800);
if (!routerUp) {
    const c = startDaemon(router, ['--no-multicast-scouting', '--listen', `tcp/127.0.0.1:${TCP_PORT}`], path.join(ROOT, 'logs', 'zenohd.log'));
    infra.push(c);
    await pollUntil(() => portOpen(TCP_PORT, 400), 10000, 300);
} else {
    check('zenohd router already running (reused)', true);
}
if (!(await portOpen(TCP_PORT, 400))) {
    console.error('FAILURES: zenohd router did not come up on tcp ' + TCP_PORT);
    process.exit(1);
}

const bridgeUp = await portOpen(WS_PORT, 800);
if (!bridgeUp) {
    const bridgeArgs = (bridge === router)
        ? ['--no-multicast-scouting', '--listen', 'tcp/127.0.0.1:7448', '--connect', `tcp/127.0.0.1:${TCP_PORT}`, '--plugin-search-dir', TOOLS, '-P', 'remote_api', '--cfg', `plugins/remote_api/websocket_port:${WS_PORT}`]
        : ['--no-multicast-scouting', '--connect', `tcp/127.0.0.1:${TCP_PORT}`, '--ws-port', String(WS_PORT)];
    const c = startDaemon(bridge, bridgeArgs, path.join(ROOT, 'logs', 'zenoh-bridge.log'));
    infra.push(c);
    await pollUntil(() => portOpen(WS_PORT, 400), 10000, 300);
} else {
    check('zenoh bridge already running (reused)', true);
}
if (!(await portOpen(WS_PORT, 400))) {
    console.error('FAILURES: zenoh bridge did not come up on ws ' + WS_PORT);
    process.exit(1);
}
check('fleet infrastructure started (router + bridge)', true);

// --- Python fleet ----------------------------------------------------------------
const server = spawnPython(['-m', 'server.server_node', '--preset', 'MICRO_FULFILLMENT', '--tasks', '1', '--url', TCP_URL]);
await pollUntil(() => /World state published/.test(server.getLog()), 10000, 250);
check('Python coordinator connected to router and published world/state', /Connected to Zenoh bridge/.test(server.getLog()));

const serverLog = server.getLog();
const rosterMatch = serverLog.match(/Roster: \[(.*)\]/);
const roster = rosterMatch
    ? rosterMatch[1].split(', ').map((s) => s.replace(/['"]/g, '')).filter(Boolean)
    : [];
check('coordinator exposes a roster', roster.length >= 1);
const robots = roster.map((id) => spawnPython(['robot/robot_node.py', '--id', id, '--url', TCP_URL]));

// --- Dashboard connection (real zenoh-ts through ConnectionManager) ----------------
const conn = new ConnectionManager({ url: WS_URL });
const seen = { taskNew: new Set(), assigned: 0, results: 0, telemetry: new Map(), completed: 0, roster: null, dims: null, world: 0 };

conn.subscribe(TOPICS.WORLD_STATE, ({ payload }) => {
    seen.world += 1;
    seen.roster = payload.roster || null;
    seen.dims = { w: payload.width, h: payload.height };
});
conn.subscribe(TOPICS.ROBOT_TELEMETRY, ({ payload }) => {
    seen.telemetry.set(payload.robotId, payload);
    if (payload.status === 'COMPLETED') seen.completed += 1;
});
conn.subscribe(TOPICS.TASK_NEW, ({ payload }) => {
    seen.taskNew.add(payload.taskId);
});
conn.subscribe(TOPICS.TASK_ASSIGNED, () => {
    seen.assigned += 1;
});
conn.subscribe(TOPICS.AUCTION_RESULT, () => {
    seen.results += 1;
});

await conn.connect();
const connected = await pollUntil(() => conn.status === CONNECTION_STATUS.CONNECTED, 10000, 200);
check('dashboard connected to WS bridge via zenoh-ts', connected);

const worldOk = await pollUntil(() => seen.roster && seen.roster.length === roster.length, 15000, 300);
check('world/state received with full roster', worldOk === true && Array.isArray(seen.roster) && seen.roster.length === roster.length);
check('world/state carries warehouse dimensions', seen.dims && seen.dims.w === 20 && seen.dims.h === 14);

const allTelemetry = await pollUntil(() => roster.every((id) => seen.telemetry.has(id)), 20000, 300);
check('telemetry received from every Python robot', allTelemetry === true);
if (allTelemetry) {
    const amr = seen.telemetry.get(roster[0]);
    check('telemetry decoded from Python {origin,type,payload} envelope', amr && typeof amr.x === 'number' && typeof amr.y === 'number');
    check('telemetry lifecycle fields present', amr && typeof amr.battery === 'number' && typeof amr.status === 'string' && 'online' in amr && 'currentTaskId' in amr);
}

const firstAuction = await pollUntil(() => seen.results >= 1 && seen.assigned >= 1, 30000, 300);
check('coordinator ran an auction for the auto task (result + assign seen)', firstAuction === true);
check('task announced to fleet (tasks/new)', seen.taskNew.size >= 1);

const completed = await pollUntil(() => seen.completed >= 1, 60000, 500);
check('at least one task completed by a Python robot (via telemetry)', completed === true);

// --- Dashboard → coordinator command path ------------------------------------------
const beforeTasks = seen.taskNew.size;
await conn.publish(TOPICS.CONTROL_TASK_CREATE, { randomCount: 1 });
const commandOk = await pollUntil(() => seen.taskNew.size >= beforeTasks + 1, 20000, 300);
check('dashboard CONTROL_TASK_CREATE produced a second task on the bus', commandOk === true);

const secondCompleted = await pollUntil(() => seen.completed >= 2, 60000, 500);
check('second (command-created) task also completed', secondCompleted === true);

// --- Shutdown & cleanup ---------------------------------------------------------------
for (const child of children) {
    if (child.exitCode === null) child.kill('SIGTERM');
}
await Promise.all(children.map((child) => new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    child.once('exit', resolve);
    setTimeout(resolve, 8000);
})));
await conn.dispose();

check('coordinator shut down cleanly (exit 0)', server.exitCode === 0);
for (let i = 0; i < robots.length; i++) {
    check(`robot ${roster[i]} shut down cleanly (exit 0)`, robots[i].exitCode === 0);
}

for (const c of infra) c.kill('SIGTERM');

if (errors.length) {
    console.error('FAILURES:');
    for (const e of errors) console.error(' -', e);
    const debugDir = '/tmp/opencode/fleetdbg';
    fs.mkdirSync(debugDir, { recursive: true });
    fs.writeFileSync(path.join(debugDir, 'last-server.log'), server.getLog());
    for (let i = 0; i < robots.length; i++) {
        fs.writeFileSync(path.join(debugDir, `last-${roster[i]}.log`), robots[i].getLog());
    }
    process.exit(1);
}
console.log(`phase5 python-fleet checks: ${PASS.length} passed (Python→${TCP_URL}, dashboard→${WS_URL})`);
console.log('ALL PHASE 5 (PYTHON FLEET) CHECKS PASSED');