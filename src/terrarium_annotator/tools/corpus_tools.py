"""Corpus tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from terrarium_annotator.tools.xml_formatter import (
    format_error,
    format_post,
    format_posts,
)

if TYPE_CHECKING:
    from terrarium_annotator.corpus import CorpusReader


class CorpusTools:
    """Corpus tool implementations."""

    def __init__(self, corpus: CorpusReader) -> None:
        """Initialize with corpus reader."""
        self._corpus = corpus

    def read_post(self, post_id: int) -> str:
        """Read a single post, return XML result."""
        post = self._corpus.get_post(post_id)
        if post is None:
            return format_error(f"Post {post_id} not found", code="NOT_FOUND")
        return format_post(post)

    def read_thread_range(
        self,
        thread_id: int,
        *,
        start_post_id: int | None = None,
        end_post_id: int | None = None,
        tag_filter: str | None = None,
    ) -> str:
        """Read posts in range, return XML result."""
        posts = self._corpus.get_posts_range(
            thread_id,
            start_post_id=start_post_id,
            end_post_id=end_post_id,
            tag_filter=tag_filter,
        )
        if not posts:
            return format_error("No posts found in range", code="EMPTY_RANGE")

        return format_posts(posts, thread_id)
