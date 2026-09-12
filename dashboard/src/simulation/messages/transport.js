import { MessageBus } from './MessageBus.js';
import { ZenohTransport } from './zenoh/ZenohTransport.js';

export const TRANSPORT_KIND_IN_MEMORY = 'in-memory';
export const TRANSPORT_KIND_ZENOH = 'zenoh';
export const DEFAULT_ZENOH_URL = 'ws/127.0.0.1:10000';

function readEnv(name) {
    try {
        const v = import.meta.env && import.meta.env[name];
        if (v) return String(v);
    } catch {
        /* no bundler env available */
    }
    try {
        const v = (typeof process !== 'undefined' && process.env && process.env[name]) || null;
        if (v) return v;
    } catch {
        /* no process env available */
    }
    return null;
}

export function resolveTransportMode(explicit, urlProvided) {
    const raw = explicit || readEnv('AMR_TRANSPORT') || 'auto';
    if (raw === TRANSPORT_KIND_ZENOH) return TRANSPORT_KIND_ZENOH;
    if (raw === TRANSPORT_KIND_IN_MEMORY) return TRANSPORT_KIND_IN_MEMORY;
    return urlProvided ? TRANSPORT_KIND_ZENOH : TRANSPORT_KIND_IN_MEMORY;
}

export function zenohUrlOf(options = {}) {
    return options.url || readEnv('AMR_ZENOH_URL') || DEFAULT_ZENOH_URL;
}

export function createTransport(clock, options = {}) {
    const opts = options || {};
    const url = zenohUrlOf(opts);
    const mode = resolveTransportMode(opts.mode, Boolean(url) && url !== DEFAULT_ZENOH_URL);
    const bus = mode === TRANSPORT_KIND_ZENOH
        ? new ZenohTransport({
            url,
            connectTimeoutMs: opts.connectTimeoutMs,
            noTrafficTimeoutMs: opts.noTrafficTimeoutMs,
            onStatus: opts.onStatus,
        })
        : new MessageBus();
    if (typeof clock === 'function') bus.setClock(clock);
    return bus;
}