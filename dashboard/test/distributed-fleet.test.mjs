import { spawn } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WS_PORT = Number.parseInt(process.env.ZENOH_WS_PORT || '10000', 10);
const ZENOH_URL = `ws/127.0.0.1:${WS_PORT}`;
const TASK_SPEC = '3,5,25,15';

const PASS = [];
const errors = [];
let bridgeChild = null;
const children = [];

function resolveBridge() {
    if (process.env.ZENOH_BRIDGE && fs.existsSync(process.env.ZENOH_BRIDGE)) return process.env.ZENOH_BRIDGE;
    const local = path.join(ROOT, 'tools', 'zenoh', 'zenoh-bridge-remote-api');
    if (fs.existsSync(local)) return local;
    return null;
}

function portOpen(port, timeoutMs = 3000) {
    return new Promise((resolve) => {
        const sock = net.connect({ port, host: '127.0.0.1' });
        const t = setTimeout(() => {
            sock.destroy();
            resolve(false);
        }, timeoutMs);
        sock.on('connect', () => {
            clearTimeout(t);
            sock.end();
            resolve(true);
        });
        sock.on('error', () => {
            clearTimeout(t);
            resolve(false);
        });
    });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pollUntil(fn, timeoutMs, intervalMs = 200) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        if (await fn()) return true;
        await sleep(intervalMs);
    }
    return await fn();
}

function spawnNode(script, args) {
    const child = spawn(process.execPath, [script, ...args], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    const out = [];
    child.stdout.on('data', (d) => out.push(d.toString()));
    child.stderr.on('data', (d) => out.push(`[STDERR] ${d.toString()}`));
    children.push(child);
    child.getLog = () => out.join('');
    return child;
}

const check = (name, pass) => {
    if (pass) PASS.push(`PASS ${name}`);
    else errors.push(`FAIL ${name}`);
};

const bridge = resolveBridge();
if (!bridge) {
    console.log('SKIPPED: zenoh-bridge-remote-api not found (set ZENOH_BRIDGE or add tools/zenoh/zenoh-bridge-remote-api). The distributed test needs a real router on port ' + WS_PORT + '.');
    process.exit(0);
}

if (await portOpen(WS_PORT)) {
    check('router already running (reused)', true);
} else {
    bridgeChild = spawn(bridge, ['--no-multicast-scouting', '--ws-port', String(WS_PORT)], {
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    bridgeChild.stderr.on('data', () => {});
    const up = await pollUntil(() => portOpen(WS_PORT, 500), 10000, 300);
    check('router started', up);
    if (!up) {
        console.error('FAILURES: router did not come up at ws port ' + WS_PORT);
        process.exit(1);
    }
}

const coord = spawnNode('src/coordinator/coordinator_node.js', ['--url', ZENOH_URL, '--task', TASK_SPEC]);
const robotIds = ['AMR1', 'AMR2', 'AMR3'];
const robots = robotIds.map((id) => spawnNode('src/robot/robot_node.js', ['--id', id, '--url', ZENOH_URL]));

const observed = await pollUntil(
    () => {
        const coordLog = coord.getLog();
        const robotLogs = robots.map((c) => c.getLog());
        return (
            /All 3 robot\(s\) reported in/.test(coordLog) &&
            /announced to 3 robot\(s\)/.test(coordLog) &&
            /committed for (AMR\d) by coordinator/.test(coordLog) &&
            /completed by/.test(coordLog) &&
            robotLogs.every((t) => /bid from (AMR\d): [0-9.]+/.test(t)) &&
            robotLogs.every((t) => /result T\d+ → winner=(AMR\d) committed=true/.test(t))
        );
    },
    45000,
    250
);

const coordLog = coord.getLog();
const robotLogs = robots.map((c) => c.getLog());

check('auction round + completion observed within 45s', observed);
if (!observed) {
    console.error('--- coordinator tail ---');
    console.error(coordLog.slice(-2000));
    for (let i = 0; i < robots.length; i++) {
        console.error(`--- ${robotIds[i]} tail ---`);
        console.error(robotLogs[i].slice(-1200));
    }
}

check('coordinator heard telemetry from all 3 robots', /All 3 robot\(s\) reported in/.test(coordLog));
check('coordinator announced task to all 3 robots', /announced to 3 robot\(s\)/.test(coordLog));

const bidLines = robotLogs.map((t) => t.match(/bid from (AMR\d): ([0-9.]+)/));
check('every robot placed a bid', bidLines.every(Boolean));

const commitMatch = coordLog.match(/committed for (AMR\d) by coordinator/);
check('coordinator committed a single winner', Boolean(commitMatch));
const commitWinner = commitMatch ? commitMatch[1] : null;

const resultLines = robotLogs.map((t) => t.match(/result T\d+ → winner=(AMR\d) committed=(true|false)/));
check('every robot received one auction result', resultLines.every(Boolean));
const resultWinners = resultLines.filter(Boolean).map((m) => m[1]);
const resultCommitted = resultLines.filter(Boolean).map((m) => m[2]);
check('all robots observed the same winner', resultWinners.length > 0 && resultWinners.every((w) => w === resultWinners[0]));
check('coordinator committed exactly the winner robots observed', Boolean(commitWinner) && resultWinners.every((w) => w === commitWinner));
check('auction results delivered as committed', resultCommitted.length > 0 && resultCommitted.every((c) => c === 'true'));

const startedCount = robotLogs.reduce((n, t) => n + (/started task (T\d+) \(via auction\)/.test(t) ? 1 : 0), 0);
check('exactly one robot committed the task (single winner)', startedCount === 1);
check('the winner robot started the task', Boolean(commitWinner) && robotLogs.some((t) => t.includes(`${commitWinner} started task`)));

let lowBidder = null;
if (bidLines.every(Boolean)) {
    const minBid = Math.min(...bidLines.map((m) => Number(m[2])));
    if (bidLines.some((m) => Number(m[2]) === minBid)) lowBidder = bidLines.find((m) => Number(m[2]) === minBid)[1];
}
check('lowest bidder was selected (min-bid auction preserved)', Boolean(commitWinner) && lowBidder === commitWinner);

check('task completed by the winner', Boolean(commitWinner) && new RegExp(`Task T\\d+ completed by ${commitWinner}`).test(coordLog));
check('winner robot reported completion', Boolean(commitWinner) && robotLogs.some((t) => t.includes(`Robot ${commitWinner} completed task`)));
check('no auction retry / commit-failure occurred', !/re-queued for auction/.test(coordLog) && !/commit failed/.test(coordLog));

for (const child of children) {
    if (child.exitCode === null) child.kill('SIGTERM');
}
await Promise.all(children.map((child) => new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    child.once('exit', resolve);
    setTimeout(resolve, 5000);
})));
if (bridgeChild) {
    bridgeChild.kill('SIGTERM');
    await new Promise((resolve) => {
        if (bridgeChild.exitCode !== null) return resolve();
        bridgeChild.once('exit', resolve);
        setTimeout(resolve, 3000);
    });
}

for (let i = 0; i < robots.length; i++) {
    check(`robot ${robotIds[i]} shut down cleanly (exit 0)`, robots[i].exitCode === 0);
}
check('coordinator shut down cleanly (exit 0)', coord.exitCode === 0);

if (errors.length) {
    console.error('FAILURES:');
    for (const e of errors) console.error(' -', e);
    fs.mkdirSync('/tmp/opencode/fleetdbg', { recursive: true });
    fs.writeFileSync('/tmp/opencode/fleetdbg/last-coord.log', `${coordLog}\n`);
    for (let i = 0; i < robots.length; i++) {
        fs.writeFileSync(`/tmp/opencode/fleetdbg/last-${robotIds[i]}.log`, `${robotLogs[i]}\n`);
    }
    process.exit(1);
}
console.log(`distributed fleet checks: ${PASS.length} passed on ${ZENOH_URL}`);
console.log('ALL DISTRIBUTED FLEET CHECKS PASSED');