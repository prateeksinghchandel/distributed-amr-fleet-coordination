"""``python -m rl`` — start the RL training server (see ``rl.server``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rl.server import main

if __name__ == "__main__":
    main()