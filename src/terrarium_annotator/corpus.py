"""Iterators and models for the Banished Quest corpus."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator, Iterable, Optional


@dataclass
class StoryPost:
    """A single post pulled from the Banished Quest database."""

    post_id: int
    body: str
    author: Optional[str]
    created_at: Optional[datetime]
    tag: Optional[str]


class CorpusReader:
    """Thin helper that yields posts tagged as story content."""

    def __init__(
        self,
        db_path: Path,
        tag_filter: str = "story_post",
        chunk_size: int = 1,
    ) -> None:
        self.db_path = Path(db_path)
        self.tag_filter = tag_filter
        self.chunk_size = chunk_size

    def iter_posts(
        self,
        start_after_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Generator[list[StoryPost], None, None]:
        """Yield lists of StoryPost objects ordered by time."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        params: list = [self.tag_filter]
        where_clause = ""
        if start_after_id is not None:
            where_clause = "AND post.id > ?"
            params.append(start_after_id)

        query = f"""
            SELECT post.id as post_id,
                   post.body as body,
                   post.name as author,
                   post.time as created_at,
                   tag.name as tag
            FROM post
            LEFT JOIN tag ON tag.post_id = post.id
            WHERE tag.name = ? {where_clause}
            ORDER BY post.time ASC, post.id ASC
        """

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)

        batch: list[StoryPost] = []
        count = 0
        for row in cursor:
            count += 1
            post = StoryPost(
                post_id=row["post_id"],
                body=row["body"] or "",
                author=row["author"],
                created_at=self._parse_timestamp(row["created_at"]),
                tag=row["tag"],
            )
            batch.append(post)
            if len(batch) == self.chunk_size:
                yield batch
                batch = []
            if limit and count >= limit:
                break

        if batch:
            yield batch

        conn.close()

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
