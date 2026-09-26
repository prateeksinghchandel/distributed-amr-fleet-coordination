import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import wasm from 'vite-plugin-wasm'
import { spawn } from 'node:child_process'
import path from 'node:path'
import fs from 'node:fs'

// zenoh-ts ships wasm-bindgen sourcemaps whose `sources` point at a `src/`
// tree that isn't published to npm, so every file warns on startup
// ("Sourcemap for ... points to missing source files"). It is a dev-time
// logging nuisance only, so we drop those lines from the Vite logger here.
function silenceZenohSourcemapWarnings() {
    return {
        name: 'silence-zenoh-sourcemap-warnings',
        configResolved(config) {
            const logger = config.logger
            const keep = (original) => (msg, options) => {
                if (
                    typeof msg === 'string' &&
                    msg.includes('Sourcemap for') &&
                    msg.includes('@eclipse-zenoh/zenoh-ts')
                ) {
                    return
                }
                return original(msg, options)
            }
            logger.warn = keep(logger.warn)
            logger.warnOnce = keep(logger.warnOnce)
        },
    }
}

// https://vite.dev/config/
const RL_LAUNCH_ARGS = ['--league', '--pool-size', '4', '--pool-every', '3000']

// Lets the Self-Play tab of the RL studio launch (and stop) the Python
// training server with --league from the browser. Browsers cannot spawn
// processes, so the dev/preview server does it for us. The middleware is
// deliberately NOT under /rl/* (the Vite proxy forwards that whole prefix to
// the Python server on :8370); it lives under /_studio/* instead.
//
//   GET  /_studio/rl-launcher -> {alive, pid, logTail, args}
//   POST /_studio/rl-launch   -> spawn or attach; {alive, alreadyRunning}
//   POST /_studio/rl-stop     -> kill the spawned child
//
// Launching reuses scripts/run-rl-server.mjs so venv resolution and
// PYTHONPATH handling are identical to `npm run rl-server`.
function rlStudioLauncher() {
    const children = new Map()
    const logPath = path.resolve(__dirname, '..', '.rl-server.log.txt')
    const runner = path.resolve(__dirname, 'scripts', 'run-rl-server.mjs')

    const fileExists = (p) => {
        try {
            fs.accessSync(p, fs.constants.F_OK)
            return true
        } catch {
            return false
        }
    }

    const alreadyRunning = async () => {
        try {
            const res = await fetch('http://127.0.0.1:8370/rl/status', { signal: AbortSignal.timeout(1200) })
            return res.ok
        } catch {
            return false
        }
    }

    const logTail = (n = 8) => {
        try {
            const lines = fs.readFileSync(logPath, 'utf8').split('\n')
            return lines.slice(-n).join('\n')
        } catch {
            return ''
        }
    }

    const launch = async () => {
        if (children.has('rl-server') && children.get('rl-server').exitCode === null) {
            return { alive: true, alreadyRunning: false }
        }
        if (await alreadyRunning()) {
            return { alive: true, alreadyRunning: true }
        }
        if (!fileExists(runner)) {
            return { error: `runner not found: ${runner}` }
        }
        const out = fs.openSync(logPath, 'a')
        const err = fs.openSync(logPath, 'a')
        const child = spawn(process.execPath, [runner, ...RL_LAUNCH_ARGS], {
            cwd: __dirname,
            detached: false,
            stdio: ['ignore', out, err],
        })
        children.set('rl-server', child)
        child.on('exit', () => {
            try {
                fs.closeSync(out)
                fs.closeSync(err)
            } catch { /** already closed */ }
            if (children.get('rl-server') === child) children.delete('rl-server')
        })
        return { alive: true, pid: child.pid }
    }

    const stop = () => {
        const child = children.get('rl-server')
        if (child && child.exitCode === null) {
            child.kill('SIGTERM')
            return { stopping: true, pid: child.pid }
        }
        return { stopping: false, pid: null }
    }

    const middleware = (req, res, next) => {
        const url = req.url.split('?')[0]
        const json = (code, body) => {
            res.statusCode = code
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify(body))
        }
        if (req.method === 'GET' && url === '/_studio/rl-launcher') {
            const child = children.get('rl-server')
            const alive = Boolean(child && child.exitCode === null)
            return json(200, { alive, pid: alive ? child.pid : null, logTail: logTail(), args: RL_LAUNCH_ARGS })
        }
        if (req.method === 'POST' && url === '/_studio/rl-launch') {
            launch().then((body) => json(body.error ? 500 : 200, body))
            return
        }
        if (req.method === 'POST' && url === '/_studio/rl-stop') {
            return json(200, stop())
        }
        return next()
    }

    return {
        name: 'rl-studio-launcher',
        logPath,
        configurePreviewServer(server) {
            server.middlewares.use(middleware)
        },
        configureServer(server) {
            server.middlewares.use(middleware)
            server.httpServer?.once('close', () => {
                for (const child of children.values()) {
                    if (child.exitCode === null) child.kill('SIGTERM')
                }
            })
        },
    }
}

export default defineConfig({
    plugins: [react(), wasm(), silenceZenohSourcemapWarnings(), rlStudioLauncher()],
    server: {
        // Proxy Fleet Manager API (/api/* -> local Fleet Manager HTTP service)
        proxy: {
            '/api': 'http://127.0.0.1:8270',
            // RL training backend (/rl/* -> aiohttp server). ws:true is
            // required so the WebSocket event stream (see useRlConnection)
            // is upgraded by the dev proxy too.
            '/rl': {
                target: 'http://127.0.0.1:8370',
                changeOrigin: true,
                ws: true,
            },
        },
    },
})