"""Data models for the context layer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ThreadSummary:
    """Summary of a completed thread for context injection."""

    thread_id: int
    position: int  # Order in which thread was processed
    summary_text: str
    entries_created: list[int] = field(default_factory=list)
    entries_updated: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict for snapshot storage."""
        return {
            "thread_id": self.thread_id,
            "position": self.position,
            "summary_text": self.summary_text,
            "entries_created": self.entries_created,
            "entries_updated": self.entries_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ThreadSummary:
        """Reconstruct from snapshot data."""
        return cls(
            thread_id=data["thread_id"],
            position=data["position"],
            summary_text=data["summary_text"],
            entries_created=data.get("entries_created", []),
            entries_updated=data.get("entries_updated", []),
        )
