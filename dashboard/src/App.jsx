import { ThemeProvider } from './theme/ThemeContext.jsx';
import Dashboard from './components/Dashboard.jsx';

export default function App() {
    return (
        <ThemeProvider>
            <Dashboard />
        </ThemeProvider>
    );
}