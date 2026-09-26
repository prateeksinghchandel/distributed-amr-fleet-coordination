export const fmt = (v, digits = 2) => {
    const n = Number(v);
    if (v === null || v === undefined || Number.isNaN(n)) return '—';
    return n.toFixed(digits);
};

export const fmtInt = (v) => {
    const n = Number(v);
    if (v === null || v === undefined || Number.isNaN(n)) return '—';
    return n.toLocaleString();
};

export const fmtBytes = (n) => {
    if (n === null || n === undefined) return '—';
    const units = ['B', 'KB', 'MB', 'GB'];
    let v = Number(n);
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
    }
    return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
};

export const fmtTime = (sec) => {
    const n = Number(sec);
    if (!Number.isFinite(n)) return '—';
    if (n < 60) return `${n.toFixed(1)}s`;
    const m = Math.floor(n / 60);
    const s = Math.round(n % 60);
    return `${m}m ${s}s`;
};

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));