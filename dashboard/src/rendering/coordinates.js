export function getGridSpacing(zoom) {
    if (zoom < 0.3) return 20;
    if (zoom < 0.6) return 10;
    if (zoom < 1.5) return 5;
    if (zoom < 4) return 2;
    if (zoom < 8) return 1;
    return 0.5;
}

export function formatWorldCoord(val) {
    return val.toFixed(2);
}

export function formatHeading(radians) {
    const degrees = (radians * 180 / Math.PI) % 360;
    return degrees.toFixed(1) + "°";
}
