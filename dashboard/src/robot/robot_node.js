import { Robot } from '../simulation/Robot.js';
import { Obstacle } from '../simulation/Obstacle.js';
import { FleetAgent } from '../simulation/fleet/FleetAgent.js';
import { createTransport, zenohUrlOf } from '../simulation/messages/transport.js';
import { TOPICS } from '../simulation/messages/topics.js';
import { makeLogger, makeErrorLogger } from '../lib/proc_log.js';

const STEP_MS = 50;
const STEP_DT = STEP_MS / 1000;

function parseRobotArgs(argv) {
    const args = { id: null, url: null };
    for (let i = 2; i < argv.length; i++) {
        const a = argv[i];
        if (a === '--id') args.id = argv[++i];
        else if (a === '--url') args.url = argv[++i];
        else {
            console.error(`[robot] unknown option ${a}`);
            process.exit(2);
        }
    }
    if (!args.id) {
        console.error('usage: node src/robot/robot_node.js --id AMR1 [--url ws/127.0.0.1:10000]');
        process.exit(2);
    }
    return args;
}

const args = parseRobotArgs(process.argv);
const id = args.id;
const log = makeLogger(id);
const logError = makeErrorLogger(id);

const bus = createTransport(() => performance.now() / 1000, {
    mode: 'zenoh',
    url: args.url || zenohUrlOf(),
    connectTimeoutMs: 2500,
    noTrafficTimeoutMs: 4000,
    onStatus: ({ message, detail }) => {
        if (detail) logError(`${message} (${detail})`);
        else log(`${message}`);
    },
});

const robot = new Robot(id, 0, 0);
const pendingTasks = new Map();
let world = null;
let started = false;
let spawned = false;
let timer = null;
let lastTick = null;

log(`starting robot ${id}`);
log(`zenoh locator: ${zenohUrlOf()}`);

const agent = new FleetAgent(robot, bus, {
    onEvent: (msg) => log(msg),
    onAssign: () => false,
    disableFinalize: true,
    getFleetSnapshot: () => (world ? world.roster.map((r) => ({
        robotId: r.id,
        x: (r.id === id ? robot.x : null),
        y: (r.id === id ? robot.y : null),
        heading: 0,
        status: 'IDLE',
        battery: 100,
        currentTaskId: null,
        blocked: false,
        online: true,
    })) : []),
    getObstacles: () => (world ? world.obstacles : []),
});

bus.subscribe(TOPICS.WORLD_STATE, ({ payload }) => {
    world = {
        width: payload.width,
        height: payload.height,
        obstacles: (payload.obstacles || []).map((o) => new Obstacle(o.x, o.y, o.width, o.height)),
        roster: payload.roster || [],
    };
    if (!spawned) {
        const me = world.roster.find((r) => r.id === id);
        if (me) {
            robot.x = me.x;
            robot.y = me.y;
            spawned = true;
        }
    }
    log(`world state received (${payload.width}×${payload.height}, ${(payload.roster || []).length} robots)`);
});

bus.subscribe(TOPICS.TASK_NEW, ({ payload }) => {
    pendingTasks.set(payload.taskId, {
        id: payload.taskId,
        pickup: { x: payload.pickup.x, y: payload.pickup.y },
        dropoff: { x: payload.dropoff.x, y: payload.dropoff.y },
        priority: payload.priority,
    });
    log(`task ${payload.taskId} announced (${payload.pickup.x},${payload.pickup.y}) → (${payload.dropoff.x},${payload.dropoff.y})`);
});

bus.subscribe(TOPICS.AUCTION_RESULT, ({ payload }) => {
    agent.forgetRound(payload.taskId);
    const mine = payload.winner === id ? ' (mine)' : '';
    log(`[AUCTION] result ${payload.taskId} → winner=${payload.winner} committed=${payload.committed}${mine}`);
});

bus.subscribe(TOPICS.TASK_ASSIGNED, ({ payload }) => {
    agent.forgetRound(payload.taskId);
    if (payload.robotId !== id) return;
    const task = pendingTasks.get(payload.taskId);
    if (!task) {
        logError(`assigned task ${payload.taskId} but no announcement remembered`);
        return;
    }
    if (!world) logError(`assigned task ${payload.taskId} before world state — moving without a planned path`);
    robot.startTask(
        task,
        world ? world.obstacles : [],
        world ? { width: world.width, height: world.height } : null,
    );
    log(`${id} started task ${task.id} (via auction) — planned ${robot.currentPath.length} waypoints`);
});

bus.subscribe(TOPICS.TASK_CANCELLED, ({ payload }) => {
    pendingTasks.delete(payload.taskId);
    if (robot.currentTaskId === payload.taskId) {
        robot.cancelTask();
        log(`task ${payload.taskId} cancelled`);
    }
});

function startLoop() {
    if (started) return;
    started = true;
    lastTick = null;
    timer = setInterval(tick, STEP_MS);
}

function tick() {
    const now = performance.now() / 1000;
    bus.deliverDue(now);
    const dt = lastTick === null ? STEP_DT : Math.min(Math.max(now - lastTick, 0.01), 0.2);
    lastTick = now;
    if (!world) return;
    robot.update(dt, world.obstacles, { width: world.width, height: world.height }, (msg) => log(msg));
    agent.tick(dt, now);
}

try {
    await bus.whenReady(10000);
    log('connected to zenoh router');
    startLoop();
} catch (err) {
    logError(`${err.message}`);
    bus.dispose();
    process.exit(1);
}

function shutdown() {
    log('shutting down');
    if (timer) clearInterval(timer);
    agent.destroy();
    Promise.race([
        bus.dispose(),
        new Promise((resolve) => setTimeout(resolve, 2000)),
    ]).then(() => process.exit(0));
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);