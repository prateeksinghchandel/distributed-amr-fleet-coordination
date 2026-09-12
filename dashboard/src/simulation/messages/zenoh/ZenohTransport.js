import { MessageBus } from '../MessageBus.js';

let transportSeq = 0;

export function encodeEnvelope(topic, payload, sender, origin) {
    return JSON.stringify({ topic, payload, sender: sender || null, origin });
}

export function decodeEnvelope(raw) {
    try {
        const data = JSON.parse(String(raw));
        if (data && typeof data === 'object' && typeof data.topic === 'string') return data;
    } catch {
        return null;
    }
    return null;
}

export class ZenohTransport {
    constructor(options = {}) {
        const uniqueId = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
        this.id = `zenoh-${uniqueId}-${transportSeq++}`;
        this.url = options.url || 'ws://127.0.0.1:10000';
        this.connectTimeoutMs = options.connectTimeoutMs || 2500;
        this.onStatus = typeof options.onStatus === 'function' ? options.onStatus : null;
        this.nowFn = () => 0;
        this.local = new MessageBus();
        this.local.setClock(() => this.nowFn());
        this.session = null;
        this.subs = new Map();
        this.remoteQueue = [];
        this.patternCounts = new Map();
        this.disposed = false;
        this.connecting = false;
        this.status = 'connecting';
        this.failDetail = null;
        this.lastSampleAt = 0;
        this.noTrafficTimeoutMs = options.noTrafficTimeoutMs || 10000;
        this._connect();
        this._watchdog = setInterval(() => this._checkWatchdog(), 1000);
    }

    get transportKind() {
        return 'zenoh';
    }

    whenReady(timeoutMs = 10000) {
        if (this.session && !this.disposed) return Promise.resolve(this.status);
        return new Promise((resolve, reject) => {
            const deadline = Date.now() + timeoutMs;
            const timer = setTimeout(
                () => {
                    clearInterval(poll);
                    reject(new Error(`zenoh not connected at ${this.url} (${this.status}) within ${timeoutMs}ms`));
                },
                timeoutMs
            );
            const poll = setInterval(() => {
                if (this.session && !this.disposed) {
                    clearInterval(poll);
                    clearTimeout(timer);
                    resolve(this.status);
                } else if (this.status === 'unavailable (local-only)') {
                    clearInterval(poll);
                    clearTimeout(timer);
                    reject(new Error(this.failDetail || this.status));
                } else if (Date.now() > deadline) {
                    clearInterval(poll);
                    clearTimeout(timer);
                    reject(new Error(`zenoh not connected at ${this.url} (${this.status})`));
                }
            }, 50);
        });
    }

    setClock(fn) {
        this.nowFn = fn;
        this.local.setClock(fn);
    }

    subscribe(pattern, handler) {
        this.patternCounts.set(pattern, (this.patternCounts.get(pattern) || 0) + 1);
        const unsub = this.local.subscribe(pattern, handler);
        this._declareSub(pattern);
        return () => {
            unsub();
            const remaining = (this.patternCounts.get(pattern) || 1) - 1;
            this.patternCounts.set(pattern, remaining);
            if (remaining <= 0) this._undeclareSub(pattern);
        };
    }

    publish(topic, payload, meta = {}) {
        this.local.publish(topic, payload, meta);
        if (this.session && !this.disposed) {
            this.session.put(topic, encodeEnvelope(topic, payload, meta.sender, this.id)).catch(() => {});
        }
    }

    deliverDue(now) {
        this._flushRemote();
        this.local.deliverDue(now);
    }

    pendingCount() {
        return this.local.pendingCount() + this.remoteQueue.length;
    }

    dispose() {
        this.disposed = true;
        clearInterval(this._watchdog);
        const closing = [];
        for (const sub of this.subs.values()) {
            closing.push(sub.undeclare().catch(() => {}));
        }
        this.subs.clear();
        if (this.session) {
            const s = this.session;
            this.session = null;
            closing.push(s.close().catch(() => {}));
        }
        return Promise.allSettled(closing).then(() => undefined);
    }

    async _connect() {
        if (this.connecting || this.disposed) return;
        this.connecting = true;
        try {
            const { Config, Session } = await import('@eclipse-zenoh/zenoh-ts');
            if (this.disposed) return;
            const config = new Config(this.url, this.connectTimeoutMs);
            this.session = await Session.open(config);
            for (const pattern of this.patternCounts.keys()) {
                await this._declareSub(pattern);
            }
            this.lastSampleAt = Date.now();
            this.status = `zenoh connected to ${this.url}`;
            this._emitStatus(this.status, null);
        } catch (err) {
            this.session = null;
            this.status = 'unavailable (local-only)';
            this.failDetail = err && err.message ? err.message : String(err);
            this._emitStatus(null, this.failDetail);
        } finally {
            this.connecting = false;
        }
    }

    _checkWatchdog() {
        if (this.disposed || !this.session) return;
        if (Date.now() - this.lastSampleAt <= this.noTrafficTimeoutMs) return;
        this._emitStatus('no zenoh traffic received; reconnecting', null);
        this.status = 'reconnecting';
        const s = this.session;
        this.session = null;
        s.close().catch(() => {});
        this.subs.clear();
        this.lastSampleAt = Date.now();
        this._connect();
    }

    _emitStatus(message, detail) {
        if (this.onStatus) this.onStatus({ message, detail });
    }

    async _declareSub(pattern) {
        if (!this.session || this.subs.has(pattern)) return;
        try {
            const sub = await this.session.declareSubscriber(pattern, {
                handler: (sample) => this._onSample(sample),
            });
            if (this.disposed || !this.patternCounts.has(pattern)) {
                sub.undeclare().catch(() => {});
                return;
            }
            this.subs.set(pattern, sub);
        } catch (err) {
            if (this.disposed || !this.patternCounts.has(pattern)) return;
            this._emitStatus(null, `zenoh subscribe failed for ${pattern}: ${err.message || err}`);
            setTimeout(() => this._declareSub(pattern), 500);
        }
    }

    async _undeclareSub(pattern) {
        const sub = this.subs.get(pattern);
        if (!sub) return;
        this.subs.delete(pattern);
        try {
            await sub.undeclare();
        } catch {
            /* already undeclared */
        }
    }

    _onSample(sample) {
        this.lastSampleAt = Date.now();
        const raw = sample.payload().toString();
        const data = decodeEnvelope(raw);
        if (!data || data.origin === this.id) return;
        const topic = data.topic || sample.keyexpr().toString();
        this.remoteQueue.push({ topic, payload: data.payload, sender: data.sender || null });
    }

    _flushRemote() {
        while (this.remoteQueue.length > 0) {
            const m = this.remoteQueue.shift();
            this.local.publish(m.topic, m.payload, { sender: m.sender });
        }
    }
}