import React from 'react';
import WarehousePanel from './WarehousePanel.jsx';
import TaskPanel from './TaskPanel.jsx';
import AuctionPanel from './AuctionPanel.jsx';

export default function Sidebar({ fleet }) {
    return (
        <div style={{
            width: 260,
            minWidth: 260,
            background: '#16213e',
            color: '#e0e0e0',
            display: 'flex',
            flexDirection: 'column',
            overflowY: 'auto',
            borderRight: '2px solid #0f3460',
            fontFamily: 'monospace'
        }}>
            <div style={{ padding: 12, borderBottom: '1px solid #0f3460' }}>
                <h2 style={{ margin: 0, fontSize: 14, color: '#e94560' }}>AMR FLEET</h2>
                <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>
                    {fleet.warehouse ? `${fleet.warehouse.width} × ${fleet.warehouse.height} m` : 'awaiting world state'}
                    {fleet.warehouse && ` · ${fleet.robotsList.length} robots · ${fleet.tasksList.length} tasks`}
                </div>
            </div>
            <WarehousePanel fleet={fleet} />
            <AuctionPanel fleet={fleet} />
            <TaskPanel fleet={fleet} />
        </div>
    );
}