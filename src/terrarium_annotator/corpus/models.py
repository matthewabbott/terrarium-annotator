"""Data models for corpus access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Thread:
    """A thread from the corpus."""

    id: int
    title: str | None


@dataclass
class StoryPost:
    """A single post from the Banished Quest corpus."""

    post_id: int
    thread_id: int
    body: str
    author: str | None
    created_at: datetime | None
    tags: list[str]

    def has_tag(self, tag: str) -> bool:
        """Check if post has a specific tag."""
        return tag in self.tags


@dataclass
class Scene:
    """A contiguous run of qm_post-tagged posts within a thread."""

    thread_id: int
    posts: list[StoryPost]
    is_thread_start: bool
    is_thread_end: bool
    scene_index: int

    @property
    def first_post_id(self) -> int | None:
        """ID of the first post in the scene."""
        return self.posts[0].post_id if self.posts else None

    @property
    def last_post_id(self) -> int | None:
        """ID of the last post in the scene."""
        return self.posts[-1].post_id if self.posts else None

    @property
    def post_count(self) -> int:
        """Number of posts in the scene."""
        return len(self.posts)


@dataclass
class ThreadContent:
    """All QM posts from a thread for batch processing (F11).

    Unlike Scene which represents a contiguous run of qm_posts,
    ThreadContent contains ALL qm_posts from a thread regardless
    of intervening non-QM posts.
    """

    thread_id: int
    thread_title: str | None
    qm_posts: list[StoryPost]
    thread_position: int  # Ordinal position (1st, 2nd, 3rd thread...)

    @property
    def first_post_id(self) -> int | None:
        """ID of the first QM post in the thread."""
        return self.qm_posts[0].post_id if self.qm_posts else None

    @property
    def last_post_id(self) -> int | None:
        """ID of the last QM post in the thread."""
        return self.qm_posts[-1].post_id if self.qm_posts else None

    @property
    def post_count(self) -> int:
        """Number of QM posts in the thread."""
        return len(self.qm_posts)
