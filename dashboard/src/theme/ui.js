/**
 * ui.js — shared style helpers built from the active palette.
 *
 * Components use these so visual styling stays centralized and themable.
 */

export const ui = (P) => ({
    button(variant = 'default') {
        const base = {
            padding: '5px 12px',
            background: P.surface,
            color: P.text,
            border: `1px solid ${P.borderStrong}`,
            borderRadius: 4,
            cursor: 'pointer',
            fontSize: 12,
            fontFamily: 'inherit',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
        };
        if (variant === 'primary') {
            return { ...base, background: P.accent, borderColor: P.accent, color: P.accentContrast, fontWeight: 'bold' };
        }
        if (variant === 'danger') {
            return { ...base, color: P.danger, borderColor: P.danger };
        }
        if (variant === 'subtle') {
            return { ...base, background: 'transparent', borderColor: 'transparent', color: P.textMuted };
        }
        if (variant === 'ghost') {
            return { ...base, background: 'transparent' };
        }
        return base;
    },
    input: {
        width: '100%',
        padding: '5px 8px',
        background: P.surfaceElevated,
        color: P.text,
        border: `1px solid ${P.borderStrong}`,
        borderRadius: 4,
        fontSize: 12,
        outline: 'none',
    },
    select: {
        padding: '5px 8px',
        background: P.surfaceElevated,
        color: P.text,
        border: `1px solid ${P.borderStrong}`,
        borderRadius: 4,
        fontSize: 12,
        fontFamily: 'inherit',
        outline: 'none',
    },
    textarea: {
        width: '100%',
        padding: '5px 8px',
        background: P.surfaceElevated,
        color: P.text,
        border: `1px solid ${P.borderStrong}`,
        borderRadius: 4,
        fontSize: 12,
        resize: 'vertical',
        outline: 'none',
    },
    card: {
        background: P.surface,
        border: `1px solid ${P.border}`,
        borderRadius: 6,
        padding: 10,
    },
    sectionTitle: {
        fontSize: 11,
        letterSpacing: 1,
        fontWeight: 'bold',
        color: P.accent,
        textTransform: 'uppercase',
    },
    muted: {
        color: P.textMuted,
    },
    dim: {
        color: P.textDim,
    },
    label: {
        fontSize: 11,
        color: P.textMuted,
        marginBottom: 3,
    },
    chip(color) {
        return {
            display: 'inline-block',
            borderRadius: 3,
            padding: '1px 7px',
            fontSize: 10,
            border: `1px solid ${color}`,
            color,
            background: 'transparent',
        };
    },
    dot(color) {
        return {
            display: 'inline-block',
            width: 9,
            height: 9,
            borderRadius: '50%',
            background: color,
            flex: '0 0 auto',
        };
    },
    tab(active) {
        return {
            flex: 1,
            padding: '5px 0',
            background: active ? P.accent : P.surfaceActive,
            color: active ? P.accentContrast : P.textMuted,
            border: 'none',
            borderRadius: 4,
            cursor: 'pointer',
            fontSize: 11,
            fontWeight: active ? 'bold' : 'normal',
            fontFamily: 'inherit',
        };
    },
    panelHeader: {
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 8,
    },
});

/** Count helpers reused across summary components. */
export const TASK_STATUS = {
    PENDING: 'PENDING',
    ASSIGNED: 'ASSIGNED',
    PICKING_UP: 'PICKING_UP',
    DELIVERING: 'DELIVERING',
    COMPLETED: 'COMPLETED',
    CANCELLED: 'CANCELLED',
    FAILED: 'FAILED',
};

export const ACTIVE_STATUSES = new Set([TASK_STATUS.ASSIGNED, TASK_STATUS.PICKING_UP, TASK_STATUS.DELIVERING]);

export const taskCounts = (tasks) => {
    const counts = { pending: 0, active: 0, completed: 0, failed: 0, cancelled: 0 };
    for (const t of tasks) {
        if (t.status === TASK_STATUS.COMPLETED) counts.completed += 1;
        else if (t.status === TASK_STATUS.FAILED) counts.failed += 1;
        else if (t.status === TASK_STATUS.CANCELLED) counts.cancelled += 1;
        else if (ACTIVE_STATUSES.has(t.status)) counts.active += 1;
        else counts.pending += 1;
    }
    return counts;
};

export const taskColor = (P, status) => {
    switch (status) {
        case TASK_STATUS.COMPLETED: return P.success;
        case TASK_STATUS.CANCELLED:
        case TASK_STATUS.FAILED: return P.danger;
        case TASK_STATUS.PICKING_UP:
        case TASK_STATUS.DELIVERING:
        case TASK_STATUS.ASSIGNED: return P.info;
        default: return P.warning;
    }
};

export const robotStatusColor = (P, robot) => {
    if (!robot.online) return P.textDim;
    if (robot.blocked) return P.danger;
    if (robot.status === 'CHARGING') return P.success;
    if (robot.status === 'COMPLETED' || robot.status === 'IDLE') return P.textMuted;
    return P.info;
};