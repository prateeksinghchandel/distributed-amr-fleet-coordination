import Drawer from './Drawer.jsx';
import AuctionPanel from '../AuctionPanel.jsx';

export default function AuctionDrawer({ fleet, onClose, onSelectTask }) {
    return (
        <Drawer
            title="AUCTIONS"
            subtitle={`${fleet.auctions.length} settled · ${fleet.liveBids.size} live`}
            onClose={onClose}
            width={460}
        >
            <AuctionPanel fleet={fleet} onSelectTask={onSelectTask} maxAuctions={200} />
        </Drawer>
    );
}