import { createTransport, zenohUrlOf } from '../simulation/messages/transport.js';
import { FleetCoordinator, DEFAULT_ROSTER } from './FleetCoordinator.js';
import { makeLogger, makeErrorLogger } from '../lib/proc_log.js';

const STEP_MS = 50;
const STEP_DT = STEP_MS / 1000;

function parseCoordinatorArgs(argv) {
    const args = { taskSpecs: [], count: 0, url: null, width: 30, height: 20, roster: DEFAULT_ROSTER };
    for (let i = 2; i < argv.length; i++) {
        const a = argv[i];
        if (a === '--task') {
            const raw = argv[++i];
            const parts = String(raw).split(/[,;\s]+/).map(Number);
            if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) {
                console.error(`[coordinator] invalid --task "${raw}" (expected x1,y1,x2,y2)`);
                process.exit(2);
            }
            args.taskSpecs.push({ pickup: { x: parts[0], y: parts[1] }, dropoff: { x: parts[2], y: parts[3] } });
        } else if (a === '--tasks') {
            args.count = Number.parseInt(argv[++i], 10) || 0;
        } else if (a === '--url') {
            args.url = argv[++i];
        } else if (a === '--width') {
            args.width = Number.parseInt(argv[++i], 10);
        } else if (a === '--height') {
            args.height = Number.parseInt(argv[++i], 10);
        } else if (a === '--roster') {
            args.roster = String(argv[++i]).split(',').filter(Boolean).map((entry) => {
                const parts = entry.split(':');
                return { id: parts[0], x: Number.parseFloat(parts[1]), y: Number.parseFloat(parts[2]) };
            });
        } else {
            console.error(`[coordinator] unknown option ${a}`);
            process.exit(2);
        }
    }
    if (args.taskSpecs.length === 0 && args.count <= 0) args.count = 1;
    return args;
}

const args = parseCoordinatorArgs(process.argv);
const tag = 'coordinator';
const log = makeLogger(tag);
const logError = makeErrorLogger(tag);

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

log(`starting (${args.width}×${args.height}, roster: ${args.roster.map((r) => r.id).join(', ')})`);
log(`zenoh locator: ${zenohUrlOf()}`);

try {
    await bus.whenReady(10000);
    log('connected to zenoh router');
} catch (err) {
    logError(`${err.message}`);
    bus.dispose();
    process.exit(1);
}

const coordinator = new FleetCoordinator(bus, {
    width: args.width,
    height: args.height,
    roster: args.roster,
    onEvent: (msg) => log(msg),
    start: { taskSpecs: args.taskSpecs, count: args.count },
});

function shutdown() {
    log('shutting down');
    coordinator.dispose();
    Promise.race([
        bus.dispose(),
        new Promise((resolve) => setTimeout(resolve, 2000)),
    ]).then(() => process.exit(0));
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

setInterval(() => coordinator.step(STEP_DT, performance.now() / 1000), STEP_MS);