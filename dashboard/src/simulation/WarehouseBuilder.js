/**
 * WarehouseBuilder.js
 * Generates structured warehouse logistics layouts:
 * - Left: Delivery Station Area (dropoff docks)
 * - Right: AMR Area (charging pads and robot staging)
 * - Center: Storage Shelves / Racks (inventory pick-faces)
 */

export const WAREHOUSE_PRESETS = {
    ECOMMERCE: {
        id: 'ecommerce',
        name: 'E-Commerce Fulfillment Center',
        description: 'Standard 30×20m warehouse with 3 delivery docks, 3 shelf aisles, and 3 charging pads.',
        width: 30,
        height: 20,
        deliveryWidth: 4.5,
        deliveryDocks: 3,
        chargingWidth: 4.5,
        chargingPads: 3,
        shelfRows: 2,
        shelfCols: 3,
        shelfWidth: 3.5,
        shelfDepth: 1.6,
        aisleWidth: 3.0,
        robotCount: 3,
    },
    DISTRIBUTION: {
        id: 'distribution',
        name: 'High-Density Distribution Hub',
        description: 'Large 40×25m facility with 4 delivery docks, 4 long rack aisles, and 5 charging pads.',
        width: 40,
        height: 25,
        deliveryWidth: 5.0,
        deliveryDocks: 4,
        chargingWidth: 5.0,
        chargingPads: 5,
        shelfRows: 3,
        shelfCols: 4,
        shelfWidth: 4.0,
        shelfDepth: 1.6,
        aisleWidth: 2.8,
        robotCount: 5,
    },
    MICRO_FULFILLMENT: {
        id: 'micro',
        name: 'Micro-Fulfillment Facility',
        description: 'Compact 20×14m facility with 2 delivery bays, 2 shelf rows, and 2 charging pads.',
        width: 20,
        height: 14,
        deliveryWidth: 3.5,
        deliveryDocks: 2,
        chargingWidth: 3.5,
        chargingPads: 2,
        shelfRows: 2,
        shelfCols: 2,
        shelfWidth: 3.0,
        shelfDepth: 1.4,
        aisleWidth: 2.5,
        robotCount: 2,
    },
};

/**
 * Builds a structured warehouse logistics configuration.
 */
export function buildWarehouseConfig(params = {}) {
    const width = Math.max(12, Math.min(params.width || 30, 100));
    const height = Math.max(10, Math.min(params.height || 20, 100));

    const deliveryWidth = Math.max(2.5, Math.min(params.deliveryWidth || 4.5, width * 0.3));
    const deliveryDocksCount = Math.max(1, Math.min(params.deliveryDocks || 3, 8));

    const chargingWidth = Math.max(2.5, Math.min(params.chargingWidth || 4.5, width * 0.3));
    const chargingPadsCount = Math.max(1, Math.min(params.chargingPads || 3, 12));

    const robotCount = Math.max(1, Math.min(params.robotCount || 3, chargingPadsCount));

    // 1. Delivery Area on the Left (x: 0 to deliveryWidth)
    const deliveryZone = {
        id: 'delivery-zone',
        name: 'Delivery Station Area',
        x: 0,
        y: 0,
        width: deliveryWidth,
        height: height,
        stations: [],
    };

    const dockHeight = Math.min(2.5, (height - 2) / deliveryDocksCount - 0.5);
    const dockSpacing = (height - 2 - dockHeight * deliveryDocksCount) / (deliveryDocksCount + 1);
    for (let i = 0; i < deliveryDocksCount; i++) {
        const sy = 1 + dockSpacing + i * (dockHeight + dockSpacing);
        const dockW = deliveryWidth - 1.2;
        deliveryZone.stations.push({
            id: `DOCK-${i + 1}`,
            name: `Delivery Dock ${i + 1}`,
            x: 0.6,
            y: sy,
            width: dockW,
            height: dockHeight,
            dropoffPoint: {
                x: 0.6 + dockW / 2,
                y: sy + dockHeight / 2,
            },
        });
    }

    // 2. AMR Area & Charging Docks on the Right (x: width - chargingWidth to width)
    const chargingZoneX = width - chargingWidth;
    const chargingZone = {
        id: 'charging-zone',
        name: 'AMR Charging Area',
        x: chargingZoneX,
        y: 0,
        width: chargingWidth,
        height: height,
        pads: [],
    };

    const padHeight = Math.min(2.2, (height - 2) / chargingPadsCount - 0.4);
    const padSpacing = (height - 2 - padHeight * chargingPadsCount) / (chargingPadsCount + 1);
    for (let i = 0; i < chargingPadsCount; i++) {
        const py = 1 + padSpacing + i * (padHeight + padSpacing);
        const padW = chargingWidth - 1.2;
        const padX = chargingZoneX + 0.6;
        chargingZone.pads.push({
            id: `BAY-${i + 1}`,
            name: `Charging Bay ${i + 1}`,
            x: padX,
            y: py,
            width: padW,
            height: padHeight,
            spawnPoint: {
                x: padX + padW / 2,
                y: py + padHeight / 2,
            },
            assignedRobotId: i < robotCount ? `AMR${i + 1}` : null,
        });
    }

    // 3. Storage Shelves in the Center (between deliveryWidth + buffer and chargingZoneX - buffer)
    const centerStartX = deliveryWidth + 1.5;
    const centerEndX = chargingZoneX - 1.5;
    const centerWidth = Math.max(4, centerEndX - centerStartX);

    const shelfRows = Math.max(1, Math.min(params.shelfRows || 2, 6));
    const shelfCols = Math.max(1, Math.min(params.shelfCols || 3, 8));
    const shelfWidth = Math.max(1.5, Math.min(params.shelfWidth || 3.5, 6));
    const shelfDepth = Math.max(0.8, Math.min(params.shelfDepth || 1.6, 3));

    const shelves = [];
    const shelfObstacles = [];

    // Layout shelf blocks evenly across the center area
    const xSpacing = shelfCols > 1 ? (centerWidth - shelfCols * shelfWidth) / (shelfCols - 1) : 0;
    const availableHeight = height - 4;
    const ySpacing = shelfRows > 1 ? (availableHeight - shelfRows * shelfDepth) / (shelfRows - 1) : 0;

    let shelfIndex = 1;
    for (let r = 0; r < shelfRows; r++) {
        for (let c = 0; c < shelfCols; c++) {
            const sx = shelfCols > 1
                ? centerStartX + c * (shelfWidth + Math.max(1.2, xSpacing))
                : centerStartX + (centerWidth - shelfWidth) / 2;
            const sy = shelfRows > 1
                ? 2 + r * (shelfDepth + Math.max(1.8, ySpacing))
                : (height - shelfDepth) / 2;

            if (sx + shelfWidth > centerEndX + 0.2 || sy + shelfDepth > height - 1.0) continue;

            const shelfId = `SH-${shelfIndex++}`;
            const shelfObj = {
                id: shelfId,
                name: `Shelf ${shelfId}`,
                x: sx,
                y: sy,
                width: shelfWidth,
                height: shelfDepth,
                type: 'shelf',
                // Pick points along the north and south faces of the shelf in the aisles
                pickPoints: [
                    { x: sx + shelfWidth * 0.25, y: Math.max(0.8, sy - 0.7) },
                    { x: sx + shelfWidth * 0.75, y: Math.max(0.8, sy - 0.7) },
                    { x: sx + shelfWidth * 0.25, y: Math.min(height - 0.8, sy + shelfDepth + 0.7) },
                    { x: sx + shelfWidth * 0.75, y: Math.min(height - 0.8, sy + shelfDepth + 0.7) },
                ],
            };

            shelves.push(shelfObj);
            shelfObstacles.push({
                id: shelfId,
                x: sx,
                y: sy,
                width: shelfWidth,
                height: shelfDepth,
                type: 'shelf',
            });
        }
    }

    // 4. Robot spawn configuration
    const robots = [];
    for (let i = 0; i < robotCount; i++) {
        const pad = chargingZone.pads[i];
        robots.push({
            id: `AMR${i + 1}`,
            x: pad ? pad.spawnPoint.x : width - 2,
            y: pad ? pad.spawnPoint.y : 3 + i * 3,
            radius: params.robotRadius || 0.4,
            maxSpeed: params.robotSpeed || 2.0,
        });
    }

    return {
        width,
        height,
        deliveryZone,
        chargingZone,
        shelves,
        obstacles: shelfObstacles,
        robots,
    };
}
