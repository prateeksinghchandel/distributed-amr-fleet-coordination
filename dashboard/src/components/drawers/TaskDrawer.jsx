import { useTheme } from '../../theme/ThemeContext.jsx';
import Drawer from './Drawer.jsx';
import TaskPanel from '../TaskPanel.jsx';

export default function TaskDrawer({ fleet, onClose, onSelectTask }) {
    const { palette: P } = useTheme();

    return (
        <Drawer
            title="TASKS"
            subtitle={`${fleet.tasksList.length} total · click a task to inspect`}
            onClose={onClose}
            width={500}
        >
            <div style={{ background: P.surface }}>
                <TaskPanel fleet={fleet} onSelectTask={onSelectTask} maxTasks={200} />
            </div>
        </Drawer>
    );
}