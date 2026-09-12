import { createSimulation } from './src/simulation/Simulation.js';
import { TASK_STATUS } from './src/simulation/Task.js';
import { createTransport, resolveTransportMode, TRANSPORT_KIND_IN_MEMORY, TRANSPORT_KIND_ZENOH } from './src/simulation/messages/transport.js';
import { encodeEnvelope, decodeEnvelope } from './src/simulation/messages/zenoh/ZenohTransport.js';

const errors = [];
const check = (name, cond, extra) => {
    if (cond) {
        console.log('PASS', name);
    } else {
        errors.push(`${name}${extra ? ' :: ' + extra : ''}`);
        console.log('FAIL', name, extra || '');
    }
};

function runAuctionScenario(kind, tag) {
    const sim = createSimulation(30, 20, { mode: kind });
    check(`${tag}: default fleet + agents`, sim.agents.size === 3);
    check(`${tag}: auction enabled by default`, sim.auctionEnabled);
    for (let i = 0; i < 20; i++) sim.step(1 / 60);

    const at1 = sim.createTask({ x: 5, y: 8 }, { x: 25, y: 15 }, true);
    check(`${tag}: announced task in flight`, sim.auctionInFlightId === at1.id);
    check(`${tag}: announce buffered on bus`, sim.bus.pendingCount() > 0);

    let auctioned = false;
    for (let i = 0; i < 40 * 60; i++) {
        sim.step(1 / 60);
        if (at1.status !== TASK_STATUS.PENDING) { auctioned = true; break; }
    }
    for (let i = 0; i < 10; i++) sim.step(1 / 60);
    check(`${tag}: assigned by auction`, auctioned && Boolean(at1.assignedRobotId));
    const res1 = sim.auctions.find((a) => a.taskId === at1.id);
    check(`${tag}: auction recorded`, Boolean(res1));
    if (res1) {
        check(`${tag}: recorded winner == assignee`, res1.winner === at1.assignedRobotId);
        const num = res1.bids.filter((b) => typeof b.bid === 'number');
        check(`${tag}: numeric bids present`, num.length > 0);
        const minBid = Math.min(...num.map((b) => b.bid));
        const wb = res1.bids.find((b) => b.robotId === res1.winner);
        check(`${tag}: winner bid minimal`, Boolean(wb) && Math.abs(wb.bid - minBid) < 1e-9);
    }

    let at1done = false;
    for (let i = 0; i < 40 * 60; i++) {
        sim.step(1 / 60);
        if (at1.status === TASK_STATUS.COMPLETED) { at1done = true; break; }
    }
    check(`${tag}: task completed`, at1done);

    sim.robots.find((r) => r.id === 'AMR3').online = false;
    const at2 = sim.createTask({ x: 8, y: 8 }, { x: 22, y: 16 }, true);
    let offlineAssigned = false;
    for (let i = 0; i < 40 * 60; i++) {
        sim.step(1 / 60);
        if (at2.status !== TASK_STATUS.PENDING) { offlineAssigned = true; break; }
    }
    check(`${tag}: offline robot excluded (deadline)`, offlineAssigned && at2.assignedRobotId !== 'AMR3');

    return {
        tag,
        kind,
        assignedRobotId: at1.assignedRobotId,
        winner: res1 && res1.winner,
        allNumericBids: res1 ? res1.bids.filter((b) => typeof b.bid === 'number').map((b) => [b.robotId, b.bid]) : [],
        auctionEventCount: sim.events.filter((e) => e.message.includes('[AUCTION]')).length,
        latest: sim.auctions[sim.auctions.length - 1] ? { winner: sim.auctions[sim.auctions.length - 1].winner } : null,
    };
}

const mem = runAuctionScenario(TRANSPORT_KIND_IN_MEMORY, 'mem');
const zen = runAuctionScenario(TRANSPORT_KIND_ZENOH, 'zenoh');

check('parity: same auction winner', mem.winner === zen.winner, `${mem.winner} vs ${zen.winner}`);
check('parity: same assigned robot', mem.assignedRobotId === zen.assignedRobotId);
check('parity: same numeric bid set', JSON.stringify(mem.allNumericBids) === JSON.stringify(zen.allNumericBids));
check('parity: same [AUCTION] event count', mem.auctionEventCount === zen.auctionEventCount, `${mem.auctionEventCount} vs ${zen.auctionEventCount}`);
check('parity: same latest auction winner', (mem.latest && zen.latest && mem.latest.winner === zen.latest.winner) || (!mem.latest && !zen.latest));

check('mode: explicit in-memory', resolveTransportMode(TRANSPORT_KIND_IN_MEMORY, false) === TRANSPORT_KIND_IN_MEMORY);
check('mode: explicit zenoh', resolveTransportMode(TRANSPORT_KIND_ZENOH, false) === TRANSPORT_KIND_ZENOH);
check('mode: auto without url = in-memory', resolveTransportMode(undefined, false) === TRANSPORT_KIND_IN_MEMORY);
check('mode: auto with url = zenoh', resolveTransportMode(undefined, true) === TRANSPORT_KIND_ZENOH);

check('envelope: round-trip', (() => {
    const raw = encodeEnvelope('tasks/new', { taskId: 'T1' }, 'server', 'z0');
    const d = decodeEnvelope(raw);
    return d && d.topic === 'tasks/new' && d.payload.taskId === 'T1' && d.sender === 'server' && d.origin === 'z0';
})());
check('envelope: bad payload rejected', decodeEnvelope('not-json') === null);

const s0 = createTransport(() => 1, {});
check('factory: default kind in-memory', s0.transportKind === TRANSPORT_KIND_IN_MEMORY);
check('factory: default bus API', ['setClock', 'subscribe', 'publish', 'deliverDue', 'pendingCount'].every((k) => typeof s0[k] === 'function'));

const s1 = createTransport(() => 1, { mode: TRANSPORT_KIND_ZENOH, url: 'ws://127.0.0.1:1', connectTimeoutMs: 300 });
check('factory: zenoh kind', s1.transportKind === TRANSPORT_KIND_ZENOH);
check('zenoh: bus API present', ['setClock', 'subscribe', 'publish', 'deliverDue', 'pendingCount', 'dispose'].every((k) => typeof s1[k] === 'function'));
let received = null;
s1.subscribe('tasks/new', (m) => { received = m; });
s1.publish('tasks/new', { taskId: 'T9' }, { sender: 'server', delay: 0 });
s1.deliverDue(1);
check('zenoh: local delivery works before/without router', Boolean(received) && received.payload.taskId === 'T9');
s1.dispose();

console.log('---RESULT---');
if (errors.length) {
    console.error('TRANSPORT TEST FAILURES:');
    for (const e of errors) console.error(' -', e);
    process.exit(1);
}
console.log('ALL TRANSPORT CHECKS PASSED');
process.exit(0);