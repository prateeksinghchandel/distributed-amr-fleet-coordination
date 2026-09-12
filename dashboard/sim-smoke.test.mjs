import { createSimulation } from './src/simulation/Simulation.js';
import { TASK_STATUS } from './src/simulation/Task.js';

const sim = createSimulation(30, 20);
const errors = [];

console.log('dims:', sim.width, sim.height);
if (sim.width !== 30 || sim.height !== 20) errors.push('default dims wrong');
if (sim.robots.length !== 3) errors.push('default fleet wrong');

const t1 = sim.createTask({ x: 5, y: 8 }, { x: 25, y: 15 });
if (!t1 || t1.status !== TASK_STATUS.PENDING) errors.push('task create failed');
if (t1.pickup.x !== 5 || t1.dropoff.y !== 15 || t1.assignedRobotId !== null) errors.push('task model wrong');
if (typeof t1.createdAt !== 'number' || t1.completedAt !== null) errors.push('task timestamps wrong');

const bad = sim.createTask({ x: -3, y: 8 }, { x: 25, y: 15 });
if (bad !== null) errors.push('invalid pickup not rejected');
const same = sim.createTask({ x: 5, y: 5 }, { x: 5, y: 5 });
if (same !== null) errors.push('same pickup/dropoff not rejected');

sim.addObstacle(3, 15, 4, 3);
const o1 = sim.obstacles[0];
if (o1.x < 0 || o1.y < 0 || o1.x + o1.width > 30 || o1.y + o1.height > 20) errors.push('obstacle outside bounds');
const inObstacle = sim.createTask({ x: 5, y: 16 }, { x: 25, y: 15 });
if (inObstacle !== null) errors.push('obstacle point not rejected');

const nSame = sim.generateSameDropoff({ x: 26, y: 15 }, [{ x: 4, y: 4 }, { x: 12, y: 4 }, { x: -1, y: 4 }]);
if (nSame !== 2) errors.push('same-dropoff should create 2, got ' + nSame);

const ok = sim.assignTask(t1.id, 'AMR1');
if (!ok) errors.push('assignment failed');
if (t1.assignedRobotId !== 'AMR1' || t1.status !== TASK_STATUS.ASSIGNED) errors.push('assignment state wrong');
const amr1 = sim.robots[0];
if (amr1.currentTaskId !== t1.id || amr1.status !== 'MOVING_TO_PICKUP') errors.push('robot task state wrong');
const busy = sim.assignTask(t1.id, 'AMR2');
if (busy) errors.push('double assignment allowed');

for (let i = 0; i < 40 * 60; i++) {
    sim.step(1 / 60);
}
if (t1.status !== TASK_STATUS.COMPLETED) errors.push('task did not complete, status=' + t1.status);
if (t1.completedAt === null) errors.push('completedAt not set');
if (amr1.status !== 'COMPLETED') errors.push('robot did not reach COMPLETED, status=' + amr1.status);
if (sim.robots.some((r) => r.x < 0 || r.x > sim.width || r.y < 0 || r.y > sim.height)) errors.push('robot out of bounds');

const pendingBeforeResize = sim.createTask({ x: 4, y: 4 }, { x: 26, y: 12 });
sim.setDimensions(12, 6);
if (sim.width !== 12 || sim.height !== 6) errors.push('resize failed');
if (pendingBeforeResize.status !== TASK_STATUS.CANCELLED) errors.push('pending task not cancelled on resize');
for (const o of sim.obstacles) {
    if (o.x < 0 || o.y < 0 || o.x + o.width > 12 || o.y + o.height > 6) errors.push('obstacle outside after resize');
}
for (const r of sim.robots) {
    if (r.x < r.radius || r.x > 12 - r.radius || r.y < r.radius || r.y > 6 - r.radius) errors.push('robot outside after resize: ' + r.id);
}
sim.removeObstacle(o1.id);

const t2 = sim.createTask({ x: 2, y: 2 }, { x: 4, y: 4 });
if (t2 === null) errors.push('createTask failed inside resized warehouse');
const retake = sim.assignTask(t2.id, 'AMR1');
if (!retake) errors.push('completed robot could not retake task');
let t2done = false;
for (let i = 0; i < 40 * 60; i++) {
    sim.step(1 / 60);
    if (t2.status === TASK_STATUS.COMPLETED) { t2done = true; break; }
}
if (!t2done) errors.push('retaken task never completed, status=' + t2.status);

const nRand = sim.generateRandomTasks(8);
if (nRand !== 8) errors.push('random gen made ' + nRand);
for (const t of sim.tasks) {
    if (t.status === TASK_STATUS.PENDING) {
        if (!sim.warehouse.isValidPoint(t.pickup.x, t.pickup.y)) errors.push('random pickup invalid');
        if (!sim.warehouse.isValidPoint(t.dropoff.x, t.dropoff.y)) errors.push('random dropoff invalid');
        const d = Math.hypot(t.pickup.x - t.dropoff.x, t.pickup.y - t.dropoff.y);
        if (d < 1) errors.push('random pickup==dropoff');
    }
}

sim.pause();
if (sim.running) errors.push('pause failed');
sim.resume();
if (!sim.running) errors.push('resume failed');
sim.setSpeed(2);
if (sim.speed !== 2) errors.push('speed failed');

sim.reset();
if (sim.robots.length !== 3 || sim.tasks.length !== 0 || sim.obstacles.length !== 0) errors.push('reset failed');
if (!sim.running || sim.speed !== 1 || sim.width !== 30 || sim.height !== 20) errors.push('reset defaults failed');

console.log('events:', sim.events.length);
if (errors.length) {
    console.error('FAILURES:');
    for (const e of errors) console.error(' -', e);
    process.exit(1);
}
console.log('ALL SIMULATION CHECKS PASSED');