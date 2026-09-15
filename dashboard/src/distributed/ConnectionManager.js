/**
 * ConnectionManager.js — Zenoh session lifecycle for the browser/React dashboard.
 *
 * - Connects to the zenoh-bridge-remote-api WebSocket endpoint.
 * - Publishes ONLY real commands (control/* topics) — no local simulation.
 * - Subscriptions are reference counted: the first subscriber declares on a
 *   topic, the last unsubscriber undeclares it.
 * - Handles status transitions, timeout/reconnect backoff and self-delivery
 *   suppression by origin.
 *
 * The zenoh-ts dependency is injected via `options.deps`
 * (`{ Config, Session }`) so tests can provide a fake session instead of a
 * real router.
 */

import { encodeEnvelope, decodeEnvelope } from './envelope.js';

export const CONNECTION_STATUS = {
    DISCONNECTED: 'DISCONNECTED',
    CONNECTING: 'CONNECTING',
    CONNECTED: 'CONNECTED',
    ERROR: 'ERROR',
};

export const DEFAULT_DASHBOARD_URL = 'ws/127.0.0.1:10000';

export function resolveDashboardUrl() {
    return (
        (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_ZENOH_URL) ||
        (typeof process !== 'undefined' && process.env && process.env.AMR_ZENOH_URL) ||
        DEFAULT_DASHBOARD_URL
    );
}

export class ConnectionManager {
    constructor(options = {}) {
        this.url = options.url || resolveDashboardUrl();
        this.origin = options.origin || 'dashboard';
        this.connectTimeoutMs = options.connectTimeoutMs ?? 2500;
        this.reconnectDelayMs = options.reconnectDelayMs ?? 2000;
        this.maxReconnectDelayMs = options.maxReconnectDelayMs ?? 15000;
        this.noTrafficTimeoutMs = options.noTrafficTimeoutMs ?? 15000;
        this.autoReconnect = options.autoReconnect !== false;
        this.onStatus = typeof options.onStatus === 'function' ? options.onStatus : null;
        this.onLog = typeof options.onLog === 'function' ? options.onLog : null;
        this.deps = options.deps || null;

        this.status = CONNECTION_STATUS.DISCONNECTED;
        this.statusDetail = null;
        this.connectedAt = null;
        this.lastSampleAt = 0;

        this.session = null;
        this.subscribers = new Map();
        this._connectPromise = null;
        this._reconnectTimer = null;
        this._watchdog = null;
        this._retryDelay = this.reconnectDelayMs;
        this._manualDisconnect = true;
        this._disposed = false;
    }

    _log(message) {
        if (this.onLog) this.onLog(`[ZENOH] ${message}`);
    }

    _setStatus(status, detail = null) {
        if (this.status === status && this.statusDetail === detail) return;
        const prevStatus = this.status;
        this.status = status;
        this.statusDetail = detail;
        if (this.onStatus) {
            try {
                this.onStatus({ status, prevStatus, detail });
            } catch (err) {
                this._log(`status callback error: ${err && err.message ? err.message : err}`);
            }
        }
    }

    connect() {
        if (this._disposed) return Promise.resolve();
        this._manualDisconnect = false;
        if (this.session) return Promise.resolve();
        if (this._connectPromise) return this._connectPromise;
        this._connectPromise = this._connect();
        return this._connectPromise;
    }

    async _connect() {
        this._setStatus(CONNECTION_STATUS.CONNECTING);
        try {
            const deps = this.deps || (await import('@eclipse-zenoh/zenoh-ts'));
            if (this._disposed || this._manualDisconnect) return this.session;
            const { Config, Session } = deps;
            const config = new Config(this.url, this.connectTimeoutMs);
            const session = await Session.open(config);
            if (this._disposed || this._manualDisconnect) {
                session.close().catch(() => {});
                return this.session;
            }
            this.session = session;
            this.connectedAt = Date.now();
            this.lastSampleAt = Date.now();
            this._retryDelay = this.reconnectDelayMs;
            this._startWatchdog();
            this._setStatus(CONNECTION_STATUS.CONNECTED);
            this._log(`connected to ${this.url}`);
            await this._deployAll();
        } catch (err) {
            this.session = null;
            const detail = err && err.message ? err.message : String(err);
            const wasDisposed = this._disposed || this._manualDisconnect;
            this._setStatus(CONNECTION_STATUS.ERROR, detail);
            this._log(`connect failed: ${detail}`);
            if (this.autoReconnect && !wasDisposed) this._scheduleReconnect();
        } finally {
            this._connectPromise = null;
        }
    }

    _scheduleReconnect() {
        if (this._reconnectTimer || this._disposed || this._manualDisconnect) return;
        this._log(`will retry in ${this._retryDelay}ms`);
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            if (this._disposed || this._manualDisconnect || this.session) return;
            this._retryDelay = Math.min(this._retryDelay * 2, this.maxReconnectDelayMs);
            this.connect();
        }, this._retryDelay);
    }

    retryConnection() {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this._disposed) return Promise.resolve();
        if (this.status === CONNECTION_STATUS.ERROR || this.status === CONNECTION_STATUS.DISCONNECTED) {
            return this.connect();
        }
        return Promise.resolve();
    }

    async disconnect() {
        this._manualDisconnect = true;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        this._stopWatchdog();
        await this._teardownSession();
        if (!this._disposed) this._setStatus(CONNECTION_STATUS.DISCONNECTED, null);
        this._log('disconnected');
    }

    async dispose() {
        this._disposed = true;
        await this.disconnect();
    }

    subscribe(keyexpr, handler) {
        let entry = this.subscribers.get(keyexpr);
        if (!entry) {
            entry = { sub: null, handlers: new Set(), deploy: null };
            this.subscribers.set(keyexpr, entry);
            if (this.session && this.status === CONNECTION_STATUS.CONNECTED) {
                this._deploy(keyexpr);
            }
        }
        entry.handlers.add(handler);

        let unsubscribed = false;
        return () => {
            if (unsubscribed) return;
            unsubscribed = true;
            const current = this.subscribers.get(keyexpr);
            if (!current) return;
            current.handlers.delete(handler);
            if (current.handlers.size === 0) {
                this.subscribers.delete(keyexpr);
                if (current.sub) {
                    const sub = current.sub;
                    current.sub = null;
                    sub.undeclare().catch(() => {});
                }
            }
        };
    }

    publish(topic, payload) {
        if (!this.session || this.status !== CONNECTION_STATUS.CONNECTED) {
            this._log(`publish skipped (not connected): ${topic}`);
            return false;
        }
        const raw = encodeEnvelope(topic, payload, this.origin);
        this.session.put(topic, raw).catch((err) => {
            this._log(`publish failed on ${topic}: ${err && err.message ? err.message : err}`);
        });
        return true;
    }

    whenReady(timeoutMs = 10000) {
        if (this.status === CONNECTION_STATUS.CONNECTED) return Promise.resolve(this.status);
        return new Promise((resolve, reject) => {
            const deadline = Date.now() + timeoutMs;
            const poll = setInterval(() => {
                if (this.status === CONNECTION_STATUS.CONNECTED) {
                    clearInterval(poll);
                    clearTimeout(timer);
                    resolve(this.status);
                } else if (this.status === CONNECTION_STATUS.ERROR || this.status === CONNECTION_STATUS.DISCONNECTED) {
                    clearInterval(poll);
                    clearTimeout(timer);
                    reject(new Error(this.statusDetail || this.status));
                } else if (Date.now() > deadline) {
                    clearInterval(poll);
                    clearTimeout(timer);
                    reject(new Error(`zenoh not connected at ${this.url} (${this.status}) within ${timeoutMs}ms`));
                }
            }, 50);
            const timer = setTimeout(() => {
                clearInterval(poll);
                reject(new Error(`zenoh not connected at ${this.url} (${this.status}) within ${timeoutMs}ms`));
            }, timeoutMs);
        });
    }

    async _deployAll() {
        for (const keyexpr of this.subscribers.keys()) {
            await this._deploy(keyexpr);
        }
    }

    async _deploy(keyexpr) {
        const entry = this.subscribers.get(keyexpr);
        if (!entry || !this.session || entry.sub) return;
        if (entry.deploy) return entry.deploy;
        entry.deploy = (async () => {
            try {
                const created = await this.session.declareSubscriber(keyexpr, {
                    handler: (sample) => this._dispatchSample(sample, keyexpr),
                });
                if (this._disposed || !this.subscribers.has(keyexpr)) {
                    created.undeclare().catch(() => {});
                    return;
                }
                if (entry.sub) created.undeclare().catch(() => {});
                else entry.sub = created;
            } catch (err) {
                this._log(`subscribe failed for ${keyexpr}: ${err && err.message ? err.message : err}`);
            } finally {
                entry.deploy = null;
            }
        })();
        return entry.deploy;
    }

    _dispatchSample(sample, keyexpr) {
        this.lastSampleAt = Date.now();
        let raw;
        try {
            raw = sample.payload();
        } catch {
            return;
        }
        const env = decodeEnvelope(raw);
        if (!env) return;
        if (env.origin === this.origin) return;
        const entry = this.subscribers.get(keyexpr);
        if (!entry) return;
        const msg = {
            topic: env.topic || String(keyexpr),
            payload: env.payload,
            sender: env.sender,
            origin: env.origin,
        };
        for (const handler of entry.handlers) {
            try {
                handler(msg);
            } catch (err) {
                this._log(`handler error on ${keyexpr}: ${err && err.message ? err.message : err}`);
            }
        }
    }

    _startWatchdog() {
        this._stopWatchdog();
        this._watchdog = setInterval(() => {
            if (this._disposed || this._manualDisconnect || !this.session) return;
            if (this.status !== CONNECTION_STATUS.CONNECTED) return;
            if (Date.now() - this.lastSampleAt > this.noTrafficTimeoutMs) {
                this._log('no traffic received; forcing reconnect');
                this._teardownSession();
                this.connect();
            }
        }, 1000);
        if (this._watchdog.unref) this._watchdog.unref();
    }

    _stopWatchdog() {
        if (this._watchdog) {
            clearInterval(this._watchdog);
            this._watchdog = null;
        }
    }

    async _teardownSession() {
        const session = this.session;
        this.session = null;
        for (const entry of this.subscribers.values()) {
            if (entry.sub) {
                const sub = entry.sub;
                entry.sub = null;
                sub.undeclare().catch(() => {});
            }
        }
        if (session) {
            try {
                await session.close();
            } catch {
                /* already closed */
            }
        }
    }
}