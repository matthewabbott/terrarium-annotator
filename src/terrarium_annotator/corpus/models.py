"""Data models for the corpus layer.

The corpus (`banished.db`) is immutable and shared; these are plain
read-only records. Post bodies are raw forum markup (BBCode/HTML) —
rendering/cleanup is a downstream concern, not the reader's.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thread:
    """A quest thread in chronological reading order."""

    id: int
    title: str
    started: int  # unix time of the OP post (or earliest post; see resolver)


@dataclass(frozen=True)
class Post:
    """A single forum post."""

    id: int
    thread_id: int
    time: int
    name: str
    subject: str
    body: str


@dataclass(frozen=True)
class Batch:
    """A fixed-size run of story posts within one thread.

    Deliberately size-based, not gap-based (see docs/plan T8). Never
    crosses a thread boundary.
    """

    thread_id: int
    index: int
    posts: tuple[Post, ...]

    @property
    def text(self) -> str:
        """Post bodies joined for prompt assembly."""
        return "\n\n".join(p.body for p in self.posts)
