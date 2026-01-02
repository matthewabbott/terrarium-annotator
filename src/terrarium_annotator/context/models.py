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
