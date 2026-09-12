export const TASK_POINT_PADDING = 0.5;
export const MIN_TASK_DISTANCE = 1.0;

export function validateTask(warehouse, pickup, dropoff) {
    if (!pickup || !dropoff) return 'Task needs both a pickup and a dropoff point';
    if (!warehouse.isValidPoint(pickup.x, pickup.y, TASK_POINT_PADDING)) {
        return `Invalid pickup (${pickup.x.toFixed(1)}, ${pickup.y.toFixed(1)}): outside warehouse or inside obstacle`;
    }
    if (!warehouse.isValidPoint(dropoff.x, dropoff.y, TASK_POINT_PADDING)) {
        return `Invalid dropoff (${dropoff.x.toFixed(1)}, ${dropoff.y.toFixed(1)}): outside warehouse or inside obstacle`;
    }
    const dist = Math.hypot(pickup.x - dropoff.x, pickup.y - dropoff.y);
    if (dist < MIN_TASK_DISTANCE) return 'Pickup and dropoff must be different locations';
    return null;
}

export function parsePointList(text) {
    const points = [];
    const invalid = [];
    for (const rawLine of text.split('\n')) {
        const line = rawLine.trim();
        if (!line) continue;
        const parts = line.split(/[,;\t]+/).map((s) => s.trim()).filter(Boolean);
        if (parts.length !== 2) {
            invalid.push(`Unrecognized pickup entry "${line}"`);
            continue;
        }
        const x = Number.parseFloat(parts[0]);
        const y = Number.parseFloat(parts[1]);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
            invalid.push(`Unrecognized pickup entry "${line}"`);
            continue;
        }
        points.push({ x, y });
    }
    return { points, invalid };
}