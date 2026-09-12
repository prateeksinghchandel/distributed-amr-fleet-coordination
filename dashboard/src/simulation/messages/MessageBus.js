const WILDCARD = '*';

export class MessageBus {
    constructor() {
        this.subscribers = [];
        this.buffer = [];
        this.seq = 0;
        this.maxBuffered = 5000;
        this.nowFn = () => 0;
    }

    get transportKind() {
        return 'in-memory';
    }

    setClock(fn) {
        this.nowFn = fn;
    }

    subscribe(pattern, handler) {
        const entry = { pattern, handler };
        this.subscribers.push(entry);
        return () => {
            this.subscribers = this.subscribers.filter((s) => s !== entry);
        };
    }

    publish(topic, payload, meta = {}) {
        const delay = typeof meta.delay === 'number' ? meta.delay : 0;
        const due = this.nowFn() + delay;
        this.buffer.push({ due, seq: this.seq++, topic, payload, sender: meta.sender || null });
        if (this.buffer.length > this.maxBuffered) {
            this.buffer.splice(0, this.buffer.length - this.maxBuffered);
        }
    }

    deliverDue(now) {
        if (this.buffer.length === 0) return;
        const due = [];
        const rest = [];
        for (const m of this.buffer) {
            if (m.due <= now) due.push(m);
            else rest.push(m);
        }
        this.buffer = rest;
        due.sort((a, b) => a.due - b.due || a.seq - b.seq);
        for (const m of due) this.dispatch(m);
    }

    dispatch(msg) {
        for (const s of this.subscribers) {
            if (topicMatches(s.pattern, msg.topic)) {
                s.handler({
                    topic: msg.topic,
                    payload: msg.payload,
                    sender: msg.sender,
                    timestamp: msg.due,
                });
            }
        }
    }

    pendingCount() {
        return this.buffer.length;
    }
}

function topicMatches(pattern, topic) {
    if (pattern === WILDCARD || pattern === topic) return true;
    const p = pattern.split('/');
    const t = topic.split('/');
    if (p.length !== t.length) return false;
    for (let i = 0; i < p.length; i++) {
        if (p[i] !== WILDCARD && p[i] !== t[i]) return false;
    }
    return true;
}