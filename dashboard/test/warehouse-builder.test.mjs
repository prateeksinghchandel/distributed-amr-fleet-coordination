import { buildWarehouseConfig, WAREHOUSE_PRESETS } from '../src/simulation/WarehouseBuilder.js';
import { createSimulation } from '../src/simulation/Simulation.js';

const errors = [];
const check = (desc, cond) => {
    if (!cond) errors.push(`FAIL: ${desc}`);
    else console.log(`PASS: ${desc}`);
};

// 1. Check presets
for (const [key, preset] of Object.entries(WAREHOUSE_PRESETS)) {
    const config = buildWarehouseConfig(preset);
    check(`preset ${key} width`, config.width === preset.width);
    check(`preset ${key} height`, config.height === preset.height);
    check(`preset ${key} delivery stations count`, config.deliveryZone.stations.length === preset.deliveryDocks);
    check(`preset ${key} charging pads count`, config.chargingZone.pads.length === preset.chargingPads);
    check(`preset ${key} robots count`, config.robots.length === preset.robotCount);
    check(`preset ${key} has shelf obstacles`, config.obstacles.length > 0);
}

// 2. Test Simulation with logistics layout
const sim = createSimulation(30, 20, { layout: 'logistics' });
check('sim has deliveryZone', Boolean(sim.warehouse.deliveryZone));
check('sim deliveryZone on left', sim.warehouse.deliveryZone.x === 0);
check('sim has chargingZone', Boolean(sim.warehouse.chargingZone));
check('sim chargingZone on right', sim.warehouse.chargingZone.x >= 24);
check('sim has shelves', sim.warehouse.shelves.length > 0);
check('sim shelves registered as obstacles', sim.warehouse.obstacles.length >= sim.warehouse.shelves.length);
check('sim default 3 robots spawned on charging pads', sim.robots.length === 3);

// Verify robots are spawned inside charging zone
for (const r of sim.robots) {
    check(`robot ${r.id} spawned in charging zone`, r.x >= sim.warehouse.chargingZone.x);
}

// 3. Test random task generation in logistics mode
const created = sim.generateRandomTasks(4);
check('created 4 logistics tasks', created === 4);
for (const task of sim.tasks) {
    // Dropoff should be at delivery station on the left
    check(`task ${task.id} dropoff in delivery zone`, task.dropoff.x <= sim.warehouse.deliveryZone.width + 1.0);
    // Pickup should be in center/shelf zone
    check(`task ${task.id} pickup in shelf area`, task.pickup.x >= sim.warehouse.deliveryZone.width);
}

// 4. Test custom reconfiguration via applyLogisticsLayout
const customConfig = buildWarehouseConfig({
    width: 25,
    height: 18,
    deliveryWidth: 4,
    deliveryDocks: 2,
    chargingWidth: 4,
    chargingPads: 4,
    shelfRows: 2,
    shelfCols: 2,
    shelfWidth: 3,
    shelfDepth: 1.5,
    robotCount: 4,
});
sim.applyLogisticsLayout(customConfig);
check('reconfigured width', sim.width === 25);
check('reconfigured height', sim.height === 18);
check('reconfigured robot count', sim.robots.length === 4);
check('reconfigured delivery docks', sim.warehouse.getDeliveryStations().length === 2);
check('reconfigured charging pads', sim.warehouse.getChargingPads().length === 4);

if (errors.length > 0) {
    console.error('\nFAILURES:');
    for (const e of errors) console.error(e);
    process.exit(1);
} else {
    console.log('\nALL WAREHOUSE BUILDER & LOGISTICS CHECKS PASSED!');
    process.exit(0);
}
