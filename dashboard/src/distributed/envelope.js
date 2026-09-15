/**
 * envelope.js — wire-format helpers for the distributed fleet.
 *
 * The Python backend (common/models.py -> ZenohEnvelope) publishes every
 * message as `{ origin, type, payload }` where `type` is the topic string.
 * The legacy JS transport (ZenohTransport.js) uses `{ topic, payload,
 * sender, origin }`. The dashboard must consume the Python fleet, so it
 * parses the Python shape first and tolerates the JS shape for robustness.
 *
 * We must NOT duplicate the payload schemas themselves — those live in the
 * messages each side already publishes; this module only unwraps the outer
 * envelope to `{ topic, payload, sender, origin }`.
 */

export function decodeEnvelope(raw) {
    let text;
    if (typeof raw === 'string') {
        text = raw;
    } else if (raw instanceof Uint8Array) {
        text = new TextDecoder().decode(raw);
    } else if (raw && typeof raw.to_bytes === 'function') {
        text = new TextDecoder().decode(raw.to_bytes());
    } else if (raw && typeof raw.toString === 'function') {
        text = raw.toString();
    } else {
        return null;
    }

    let data;
    try {
        data = JSON.parse(text);
    } catch {
        return null;
    }
    if (!data || typeof data !== 'object') return null;

    // Python ZenohEnvelope: { origin, type, payload }
    // JS ZenohTransport:     { topic, payload, sender, origin }
    const topic = typeof data.type === 'string' && data.type
        ? data.type
        : (typeof data.topic === 'string' ? data.topic : null);
    if (!topic) return null;

    return {
        topic,
        payload: data.payload && typeof data.payload === 'object' ? data.payload : {},
        sender: typeof data.sender === 'string' ? data.sender : (typeof data.origin === 'string' ? data.origin : null),
        origin: typeof data.origin === 'string' ? data.origin : null,
    };
}

export function encodeEnvelope(topic, payload, origin = 'dashboard', sender = null) {
    return JSON.stringify({
        origin: sender || origin,
        type: topic,
        payload: payload || {},
    });
}