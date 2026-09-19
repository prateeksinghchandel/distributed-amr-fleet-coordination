"""Persistent JSON state for the Fleet Manager.

Two small files under backend/state/:
    amrs.json      - configured AMR fleet (id, x, y)
    settings.json  - manager settings (coordinator preset / task count / auction mode)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from common.auction import AuctionMode

RESERVED_NAMES = {"zenohd", "bridge", "coordinator", "server"}


class AmrStore:
    def __init__(self, path: Path, preset: str, layout_roster: list, log=None):
        self.path = Path(path)
        self.log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self.amrs: dict[str, dict] = {}
        self._load_or_seed(preset, layout_roster)

    def _load_or_seed(self, preset: str, layout_roster: list) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for entry in data.get("amrs", []):
                    if entry.get("id"):
                        self.amrs[entry["id"]] = entry
                self.log(f"Loaded {len(self.amrs)} AMR(s) from {self.path}")
                return
            except (OSError, ValueError) as exc:
                self.log(f"WARNING: could not read {self.path} ({exc}) — reseeding from preset")
        for entry in layout_roster:
            rid = entry.get("id")
            if rid:
                self.amrs[rid] = {"id": rid, "x": float(entry.get("x", 0)), "y": float(entry.get("y", 0))}
        self.save()
        self.log(f"Seeded {len(self.amrs)} AMR(s) from preset '{preset}'")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"amrs": list(self.amrs.values())}, indent=2))
        tmp.replace(self.path)

    def list(self) -> list:
        with self._lock:
            return [dict(e) for e in self.amrs.values()]

    def get(self, amr_id: str) -> Optional[dict]:
        with self._lock:
            entry = self.amrs.get(amr_id)
            return dict(entry) if entry else None

    def add(self, entry: dict) -> dict:
        amr_id = entry["id"]
        if amr_id in RESERVED_NAMES:
            raise ValueError(f"'{amr_id}' is a reserved component name")
        if amr_id in self.amrs:
            raise ValueError(f"AMR '{amr_id}' already exists")
        with self._lock:
            self.amrs[amr_id] = dict(entry)
            self.save()
        return dict(entry)

    def remove(self, amr_id: str) -> bool:
        with self._lock:
            existed = self.amrs.pop(amr_id, None) is not None
            if existed:
                self.save()
        return existed


class SettingsStore:
    def __init__(self, path: Path, default_preset: str = "MICRO_FULFILLMENT", default_tasks: int = 1,
                 default_auction_mode: str = AuctionMode.SERVER_AUCTION):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.preset = default_preset
        self.tasks = default_tasks
        self.auction_mode = AuctionMode.normalize(default_auction_mode)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.preset = data.get("preset", default_preset)
                self.tasks = int(data.get("tasks", default_tasks))
                self.auction_mode = AuctionMode.normalize(data.get("auctionMode", self.auction_mode))
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"preset": self.preset, "tasks": self.tasks, "auctionMode": self.auction_mode},
            indent=2,
        ))
        tmp.replace(self.path)

    def set(self, preset: Optional[str] = None, tasks: Optional[int] = None,
            auction_mode: Optional[str] = None) -> dict:
        if preset:
            self.preset = preset
        if tasks is not None:
            self.tasks = max(0, int(tasks))
        if auction_mode is not None:
            self.auction_mode = AuctionMode.normalize(auction_mode)
        self.save()
        return self.to_dict()

    def to_dict(self) -> dict:
        return {"preset": self.preset, "tasks": self.tasks, "auctionMode": self.auction_mode}