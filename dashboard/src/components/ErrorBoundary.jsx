import { Component } from 'react';

const btn = (variant) => ({
    padding: '6px 14px',
    fontSize: 12,
    fontFamily: 'inherit',
    cursor: 'pointer',
    borderRadius: 4,
    border: '1px solid',
    borderColor: variant === 'primary' ? '#e94560' : '#2a3a66',
    background: variant === 'primary' ? '#e94560' : '#16213e',
    color: variant === 'primary' ? '#ffffff' : '#e8ecf8',
});

/**
 * Top-level safety net. The dashboard has no other error boundary, so a single
 * render throw (e.g. one bad message from a backend) unmounts the whole tree
 * into a blank black page. This catches it and offers TRY AGAIN / RELOAD.
 * It styles itself with fixed dark-theme colors because it must render even
 * when the crash wrecked the theme providers.
 */
export default class ErrorBoundary extends Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true };
    }

    componentDidCatch(error) {
        this.setState({ error });
    }

    reset = () => this.setState({ hasError: false, error: null });

    render() {
        if (!this.state.hasError) return this.props.children;
        const err = this.state.error;
        const isDev = Boolean(import.meta.env && import.meta.env.DEV);
        return (
            <div style={{
                width: '100vw',
                minHeight: '100vh',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: '#0a0a1a',
                color: '#e8ecf8',
                fontFamily: 'monospace',
                padding: 24,
                boxSizing: 'border-box',
            }}>
                <div style={{ maxWidth: 720, width: '100%', background: '#16213e', border: '1px solid #2a3a66', borderRadius: 6, padding: '16px 20px' }}>
                    <div style={{ fontSize: 20, fontWeight: 'bold', letterSpacing: 1, color: '#ff5c74' }}>
                        DASHBOARD CRASHED
                    </div>
                    <div style={{ fontSize: 11, color: '#8a8a9a', marginTop: 6, lineHeight: 1.6 }}>
                        A component threw while rendering — usually one malformed message from a backend. You can
                        try again, or reload the app.
                    </div>
                    {err && (
                        <pre style={{
                            background: '#0f3460',
                            border: '1px solid #2a3a66',
                            borderRadius: 4,
                            padding: 10,
                            marginTop: 10,
                            fontSize: 11,
                            color: '#ffb703',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                            overflowY: 'auto',
                            maxHeight: 220,
                        }}>
                            {String(err.message || err)}
                            {isDev && err.stack ? `\n\n${err.stack}` : ''}
                        </pre>
                    )}
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                        <button onClick={this.reset} style={btn('primary')}>
                            TRY AGAIN
                        </button>
                        <button onClick={() => window.location.reload()} style={btn('default')}>
                            RELOAD APP
                        </button>
                    </div>
                </div>
            </div>
        );
    }
}