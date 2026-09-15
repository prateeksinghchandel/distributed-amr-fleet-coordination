/**
 * distributed-state.test.mjs — Dashboard distributed state with a MOCKED
 * Zenoh transport (fake Config/Session). No router is required.
 *
 * Covers: connection (success, failure, disconnect, reconnect, cleanup),
 * telemetry (update, position, multiple robots, malformed, stale), tasks
 * (new/assign/cancel + status derivation from telemetry), auctions, and
 * subscription bookkeeping (declared once, cleaned up, no duplicates).
 */

import { ConnectionManager, CONNECTION_STATUS } from '../src/distributed/ConnectionManager.js';
import { DistributedFleetState, TASK_STATUS } from '../src/distributed/DistributedFleetState.js';
import { TOPICS } from '../src/simulation/messages/topics.js';

const PASS = [];
const errors = [];
const check = (name, pass) => {
    if (pass) PASS.push(`PASS ${name}`);
    else errors.push(`FAIL ${name}`);
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Fake zenoh-ts session
// ---------------------------------------------------------------------------

class FakeSample {
    constructor(payload, keyexpr) {
        this._payload = payload;
        this._keyexpr = keyexpr;
    }

    payload() {
        return this._payload;
    }

    keyexpr() {
        return this._keyexpr;
    }
}

export class FakeSession {
    static openCalls = [];

    constructor(config, opts = {}) {
        this.config = config;
        this.failOpen = opts.failOpen === true;
        this.subscribers = new Map();
        this.puts = [];
        this.closed = false;
    }

    static create(opts = {}) {
        class Session extends FakeSession {
            static async open(config) {
                FakeSession.openCalls.push(config);
                const session = new Session(config, opts);
                if (session.failOpen) {
                    throw new Error('FakeSession.open: simulated connect failure');
                }
                return session;
            }
        }
        return Session;
    }

    async declareSubscriber(keyexpr, opts) {
        const sub = {
            keyexpr,
            handler: opts.handler,
            undeclared: false,
            undeclare: async () => {
                sub.undeclared = true;
                this.subscribers.delete(keyexpr);
            },
        };
        this.subscribers.set(keyexpr, sub);
        return sub;
    }

    async put(topic, payload) {
        this.puts.push({ topic, payload: String(payload) });
    }

    async close() {
        this.closed = true;
    }

    deliver(topic, rawPayload, keyexpr = topic) {
        const sub = this.subscribers.get(keyexpr);
        if (sub) {
            sub.handler(new FakeSample(rawPayload, topic));
        } else {
            for (const s of this.subscribers.values()) {
                if (s.keyexpr === topic) {
                    s.handler(new FakeSample(rawPayload, topic));
                }
            }
        }
    }

    deliverPython(topic, payload, origin = 'server') {
        this.deliver(topic, JSON.stringify({ origin, type: topic, payload }));
    }
}

class FakeConfig {
    constructor(url, timeoutMs) {
        this.url = url;
        this.timeoutMs = timeoutMs;
    }
}

function makeConn(opts = {}, sessionOpts = {}) {
    const Session = FakeSession.create(sessionOpts);
    const conn = new ConnectionManager({
        url: 'ws/127.0.0.1:10000',
        autoReconnect: false,
        ...opts,
        deps: { Config: FakeConfig, Session },
    });
    return { conn, Session };
}

async function connectedState(sessionOpts = {}) {
    const { conn } = makeConn({}, sessionOpts);
    const state = new DistributedFleetState(conn, { emitThrottleMs: 0 });
    await state.connect();
    await sleep(10);
    return state;
}

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------

{
    const { conn, Session } = makeConn();
    const statuses = [];
    conn.onStatus = (s) => statuses.push(s.status);
    await conn.connect();
    await sleep(5);
    check('connect succeeds (status CONNECTED)', conn.status === CONNECTION_STATUS.CONNECTED);
    check('status sequence starts CONNECTING → CONNECTED',
        statuses[0] === CONNECTION_STATUS.CONNECTING && statuses[statuses.length - 1] === CONNECTION_STATUS.CONNECTED);
    check('session opened exactly once', Session.openCalls.length === 1);
    await conn.connect();
    check('second connect() while connected is a no-op', conn.status === CONNECTION_STATUS.CONNECTED && Session.openCalls.length === 1);
    check('connectedAt is set', typeof conn.connectedAt === 'number' && conn.connectedAt > 0);
    await conn.dispose();
    check('dispose closes the session', conn.session === null);
}

{
    const { conn } = makeConn({ autoReconnect: false }, { failOpen: true });
    await conn.connect();
    await sleep(5);
    check('connect failure → status ERROR', conn.status === CONNECTION_STATUS.ERROR);
    check('failure detail surfaces', typeof conn.statusDetail === 'string' && conn.statusDetail.length > 0);
    check('no session retained on failure', conn.session === null);
    await conn.dispose();
}

{
    const { conn } = makeConn({});
    await conn.connect();
    await sleep(5);
    await conn.disconnect();
    check('disconnect → status DISCONNECTED', conn.status === CONNECTION_STATUS.DISCONNECTED);
    check('disconnect tears down session', conn.session === null);
}

{
    // Reconnect: first attempt fails, manual retry succeeds.
    const { conn } = makeConn({ autoReconnect: false }, { failOpen: true });
    await conn.connect();
    await sleep(5);
    check('reconnect precondition: ERROR after failed connect', conn.status === CONNECTION_STATUS.ERROR);
    // Now make the session succeed on the next attempt.
    conn.deps = { Config: FakeConfig, Session: FakeSession.create({}) };
    await conn.retryConnection();
    await sleep(5);
    check('retryConnection recovers to CONNECTED', conn.status === CONNECTION_STATUS.CONNECTED);
    await conn.dispose();
}

{
    // Cleanup: dispose is idempotent and leaves no pending timers/session.
    const { conn } = makeConn({});
    await conn.connect();
    await sleep(5);
    await conn.dispose();
    await conn.dispose();
    check('dispose is idempotent', conn._disposed === true && conn.session === null);
}

// ---------------------------------------------------------------------------
// Subscriptions (once per topic, cleaned up, no duplicates)
// ---------------------------------------------------------------------------

{
    const { conn } = makeConn({});
    let session = null;
    conn.onStatus = ({ status }) => {
        if (status === CONNECTION_STATUS.CONNECTED) session = conn.session;
    };
    await conn.connect();
    await sleep(10);

    const got = [];
    const a = conn.subscribe(TOPICS.ROBOT_TELEMETRY, (m) => got.push(['a', m.topic]));
    const b = conn.subscribe(TOPICS.ROBOT_TELEMETRY, (m) => got.push(['b', m.topic]));
    await sleep(10);
    check('one Zenoh subscriber declared for topic with two handlers',
        session.subscribers.has(TOPICS.ROBOT_TELEMETRY) &&
        conn.subscribers.get(TOPICS.ROBOT_TELEMETRY).handlers.size === 2);

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, { robotId: 'AMR1', x: 1, y: 2 });
    await sleep(5);
    check('both handlers received the sample', got.length === 2);

    a();
    session.deliverPython(TOPICS.ROBOT_TELEMETRY, { robotId: 'AMR1', x: 3, y: 4 });
    await sleep(5);
    check('unsubscribed handler no longer called (b still receives)', got.length === 3);

    b();
    await sleep(5);
    check('last unsubscribe undeclares the Zenoh subscriber',
        !session.subscribers.has(TOPICS.ROBOT_TELEMETRY));

    const c = conn.subscribe(TOPICS.ROBOT_TELEMETRY, () => {});
    await sleep(5);
    check('re-subscribing redeclares the subscriber',
        session.subscribers.has(TOPICS.ROBOT_TELEMETRY));
    c();
    await conn.dispose();
}

// ---------------------------------------------------------------------------
// Envelope parsing (Python {origin,type,payload} vs JS {topic,payload,...})
// ---------------------------------------------------------------------------

{
    const { conn } = makeConn({});
    let session = null;
    conn.onStatus = ({ status }) => {
        if (status === CONNECTION_STATUS.CONNECTED) session = conn.session;
    };
    await conn.connect();
    await sleep(10);

    const seen = [];
    const unsub = conn.subscribe(TOPICS.WORLD_STATE, (m) => seen.push(m));
    await sleep(5);

    session.deliverPython(TOPICS.WORLD_STATE, { width: 30, height: 20 });
    session.deliver(TOPICS.WORLD_STATE, JSON.stringify({
        topic: TOPICS.WORLD_STATE, payload: { width: 40, height: 25 },
        sender: 'js-coord', origin: 'js-coord',
    }));
    await sleep(5);
    check('python envelope parsed (topic from type field)', seen.length === 2 && seen[0].topic === TOPICS.WORLD_STATE);
    check('python envelope payload passed through', seen[0].payload.width === 30);
    check('js envelope parsed (topic from topic field)', seen[1].payload.width === 40);
    check('sender alignment (python origin → sender)', seen[0].sender === 'server');

    session.deliver(TOPICS.WORLD_STATE, 'not-json');
    session.deliver(TOPICS.WORLD_STATE, JSON.stringify({ foo: 1 }));
    await sleep(5);
    check('malformed / non-envelope samples are ignored', seen.length === 2);

    unsub();
    await conn.dispose();
}

// ---------------------------------------------------------------------------
// DistributedFleetState — telemetry
// ---------------------------------------------------------------------------

{
    const state = await connectedState();
    const session = state.conn.session;

    session.deliverPython(TOPICS.WORLD_STATE, {
        width: 30, height: 20,
        obstacles: [{ id: 'S1', x: 10, y: 5, width: 3.5, height: 1.6, type: 'shelf' }],
        chargingPads: [{ id: 'P1', x: 25, y: 0, width: 1, height: 1, spawnPoint: { x: 25.5, y: 1 } }],
        deliveryDocks: [{ id: 'D1', x: 0, y: 2, width: 1, height: 1, dropoffPoint: { x: 1, y: 2.5 } }],
        roster: [{ id: 'AMR1', x: 5, y: 5 }, { id: 'AMR2', x: 15, y: 7 }],
    });
    await sleep(5);

    check('world state builds warehouse view', state.warehouse && state.warehouse.width === 30 && state.warehouse.height === 20);
    check('roster captured', state.warehouse.roster.length === 2);
    check('shelves derived from shelf obstacles', state.warehouse.shelves.length === 1 && state.warehouse.shelves[0].id === 'S1');
    check('deliveryZone built from docks', state.warehouse.deliveryZone && state.warehouse.deliveryZone.stations.length === 1);
    check('chargingZone built from pads', state.warehouse.chargingZone && state.warehouse.chargingZone.pads.length === 1);

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, {
        robotId: 'AMR1', x: 6.0, y: 6.0, heading: 0.5, status: 'IDLE', battery: 88, currentTaskId: null, blocked: false, online: true,
    });
    await sleep(5);
    check('telemetry registers a robot', state.robotsList.length === 1);
    check('robot view uses backend-schema fields', state.robotsList[0].robotId === 'AMR1' && state.robotsList[0].x === 6.0 && state.robotsList[0].y === 6.0);
    check('heading / battery / status captured', state.robotsList[0].heading === 0.5 && state.robotsList[0].battery === 88 && state.robotsList[0].status === 'IDLE');
    check('roster color assigned in order', state.robotsList[0].color === '#00c8ff');

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, {
        robotId: 'AMR1', x: 7.5, y: 6.5, heading: 0.5, status: 'MOVING_TO_PICKUP', battery: 88, currentTaskId: 'T-123', blocked: false, online: true,
    });
    await sleep(5);
    check('telemetry updates position + task', state.robotsList.length === 1 && state.robotsList[0].x === 7.5 && state.robotsList[0].currentTaskId === 'T-123');

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, {
        robotId: 'AMR2', x: 15, y: 7, status: 'IDLE', battery: 100, currentTaskId: null, blocked: true, online: true,
    });
    await sleep(5);
    check('multiple robots tracked', state.robotsList.length === 2);
    check('blocked flag surfaces', state.robotsList.find((r) => r.robotId === 'AMR2').blocked === true);

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, { bogus: true });
    session.deliverPython(TOPICS.ROBOT_TELEMETRY, { robotId: 'AMR3', x: 'NaN' });
    await sleep(5);
    check('malformed telemetry is ignored (no crash, no ghost robot)', state.robotsList.length === 2);

    // Staleness → offline (mirrors TelemetryManager.prune_stale)
    state.robots.get('AMR1').lastTelemetryAt = Date.now() - 60 * 1000;
    state._pruneStale();
    check('stale telemetry marks robot offline', state.robots.get('AMR1').online === false);
    await state.dispose();
}

// ---------------------------------------------------------------------------
// DistributedFleetState — tasks
// ---------------------------------------------------------------------------

{
    const state = await connectedState();
    const session = state.conn.session;

    session.deliverPython(TOPICS.TASK_NEW, {
        taskId: 'T-A1', pickup: { x: 4, y: 4 }, dropoff: { x: 26, y: 15 }, priority: 1,
    });
    await sleep(5);
    check('task announced → new pending task', state.tasksList.length === 1 && state.tasksList[0].status === TASK_STATUS.PENDING);

    // Re-announce the same task (first-seen merge, no duplicate).
    session.deliverPython(TOPICS.TASK_NEW, {
        taskId: 'T-A1', pickup: { x: 4, y: 4 }, dropoff: { x: 26, y: 15 }, priority: 1,
    });
    await sleep(5);
    check('re-announcing the same task does not duplicate', state.tasksList.length === 1);

    session.deliverPython(TOPICS.TASK_ASSIGNED, { taskId: 'T-A1', robotId: 'AMR1', source: 'manual' });
    await sleep(5);
    check('assigned task becomes ASSIGNED with robot', state.tasksList[0].status === TASK_STATUS.ASSIGNED && state.tasksList[0].assignedRobotId === 'AMR1');
    check('assigned task keeps pickup/dropoff', state.tasksList[0].pickup.x === 4 && state.tasksList[0].dropoff.x === 26);

    // Status derivation from telemetry (backend sync_tasks mirror).
    session.deliverPython(TOPICS.ROBOT_TELEMETRY, {
        robotId: 'AMR1', x: 4, y: 4, status: 'PICKING', battery: 90, currentTaskId: 'T-A1', online: true,
    });
    await sleep(5);
    check('sync: PICKING telemetry → task PICKING_UP', state.tasksList[0].status === TASK_STATUS.PICKING_UP);

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, {
        robotId: 'AMR1', x: 26, y: 15, status: 'MOVING_TO_DROPOFF', battery: 90, currentTaskId: 'T-A1', online: true,
    });
    await sleep(5);
    check('sync: MOVING_TO_DROPOFF telemetry → task DELIVERING', state.tasksList[0].status === TASK_STATUS.DELIVERING);

    session.deliverPython(TOPICS.ROBOT_TELEMETRY, {
        robotId: 'AMR1', x: 26, y: 15, status: 'COMPLETED', battery: 90, currentTaskId: 'T-A1', online: true,
    });
    await sleep(5);
    check('sync: COMPLETED telemetry → task COMPLETED', state.tasksList[0].status === TASK_STATUS.COMPLETED);

    // Cancellation of a different task.
    session.deliverPython(TOPICS.TASK_NEW, {
        taskId: 'T-B2', pickup: { x: 3, y: 3 }, dropoff: { x: 25, y: 2 }, priority: 1,
    });
    await sleep(5);
    session.deliverPython(TOPICS.TASK_CANCELLED, { taskId: 'T-B2' });
    await sleep(5);
    check('cancelled task becomes CANCELLED', state.tasksList.find((t) => t.id === 'T-B2').status === TASK_STATUS.CANCELLED);

    // Cancel cannot demote a completed task.
    session.deliverPython(TOPICS.TASK_CANCELLED, { taskId: 'T-A1' });
    await sleep(5);
    check('completed task is not demoted by a late cancel', state.tasksList.find((t) => t.id === 'T-A1').status === TASK_STATUS.COMPLETED);

    await state.dispose();
}

// ---------------------------------------------------------------------------
// DistributedFleetState — auctions
// ---------------------------------------------------------------------------

{
    const state = await connectedState();
    const session = state.conn.session;

    session.deliverPython(TOPICS.BID_PLACED, { taskId: 'T-C1', robotId: 'AMR1', bid: 5.2, costs: { travel: 3.1, congestion: 1.0, battery: 0.4, workload: 0.7 } });
    session.deliverPython(TOPICS.BID_PLACED, { taskId: 'T-C1', robotId: 'AMR2', bid: 4.1, costs: { travel: 2.9, congestion: 0.2, battery: 0.6, workload: 0.4 } });
    session.deliverPython(TOPICS.BID_PLACED, { taskId: 'T-C1', robotId: 'AMR3', bid: null, reason: 'busy' });
    await sleep(5);
    check('live bids recorded with costs + ineligibility', state.liveBids.size === 1 && state.liveBids.get('T-C1').size === 3);
    check('ineligible bid (null) keeps reason', state.liveBids.get('T-C1').get('AMR3').reason === 'busy');

    session.deliverPython(TOPICS.AUCTION_RESULT, {
        taskId: 'T-C1',
        winner: 'AMR2',
        bids: [
            { robotId: 'AMR1', bid: 5.2, costs: { travel: 3.1 }, reason: null },
            { robotId: 'AMR2', bid: 4.1, costs: { travel: 2.9 }, reason: null },
            { robotId: 'AMR3', bid: null, costs: null, reason: 'busy' },
        ],
        committed: true,
    });
    await sleep(5);
    check('auction result recorded in history', state.auctions.length === 1 && state.auctions[0].winner === 'AMR2');
    check('auction result committed flag', state.auctions[0].committed === true);
    check('auction bids preserved', state.auctions[0].bids.length === 3 && state.auctions[0].bids.find((b) => b.robotId === 'AMR2').bid === 4.1);
    check('live bids cleared after result', state.liveBids.size === 0);

    await state.dispose();
}

// ---------------------------------------------------------------------------
// DistributedFleetState — commands (real control/* publishes)
// ---------------------------------------------------------------------------

{
    const state = await connectedState();
    const session = state.conn.session;

    const okCreate = state.createTask({ x: 5, y: 5 }, { x: 25, y: 15 });
    const okRandom = state.generateRandomTasks(3);
    const okAssign = state.assignTask('T-A1', 'AMR1');
    const okCancel = state.cancelTask('T-A1');
    await sleep(5);

    check('createTask publishes control/tasks/create', okCreate === true);
    check('generateRandomTasks publishes control/tasks/create (randomCount)', okRandom === true);
    check('assignTask publishes control/tasks/assign', okAssign === true);
    check('cancelTask publishes control/tasks/cancel', okCancel === true);

    const topicsPublished = session.puts.map((p) => p.topic);
    check('all control topics published once', ['control/tasks/create', 'control/tasks/create', 'control/tasks/assign', 'control/tasks/cancel']
        .every((t, i) => topicsPublished[i] === t));

    const createPut = session.puts.find((p) => p.topic === TOPICS.CONTROL_TASK_CREATE);
    const parsed = JSON.parse(createPut.payload);
    check('command envelope uses Python {origin, type, payload} format',
        parsed.origin === 'dashboard' && parsed.type === TOPICS.CONTROL_TASK_CREATE);
    check('create payload carries pickup/dropoff', parsed.payload.pickup.x === 5 && parsed.payload.dropoff.x === 25);

    const randomPut = session.puts.find((p) => p.topic === TOPICS.CONTROL_TASK_CREATE && /randomCount/.test(p.payload));
    check('random command carries randomCount', randomPut && JSON.parse(randomPut.payload).payload.randomCount === 3);

    await state.dispose();
}

// ---------------------------------------------------------------------------

if (errors.length) {
    console.error('FAILURES:');
    for (const e of errors) console.error(' -', e);
    process.exit(1);
}
console.log(`distributed-state checks: ${PASS.length} passed`);
console.log('ALL DISTRIBUTED-STATE CHECKS PASSED');