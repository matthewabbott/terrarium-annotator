"""Persistence helpers for tracking corpus progress."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class ProgressTracker:
    """Stores the last processed post ID so runs can resume."""

    def __init__(self, path: Path = Path("data/state.json")) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[int]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text())
            return data.get("last_post_id")
        except json.JSONDecodeError:
            return None

    def save(self, last_post_id: int) -> None:
        payload = {"last_post_id": last_post_id}
        self.path.write_text(json.dumps(payload, indent=2))
