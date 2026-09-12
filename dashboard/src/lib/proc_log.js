export function makeLogger(tag) {
    return function log(message) {
        const line = `[${tag}] ${new Date().toISOString()} ${String(message)}`;
        console.log(line);
        return line;
    };
}

export function makeErrorLogger(tag) {
    return function logError(message) {
        const line = `[${tag}] ${new Date().toISOString()} ${String(message)}`;
        console.error(line);
        return line;
    };
}