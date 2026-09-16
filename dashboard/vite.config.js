import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import wasm from 'vite-plugin-wasm'

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
export default defineConfig({
    plugins: [react(), wasm(), silenceZenohSourcemapWarnings()],
})