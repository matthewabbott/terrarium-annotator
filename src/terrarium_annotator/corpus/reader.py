"""Read-only access to the Banished Quest corpus."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Iterator

from terrarium_annotator.corpus.models import StoryPost, Thread


class CorpusReader:
    """Read-only access to banished.db."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        tag_filter: str | None = None,
        chunk_size: int = 1,
    ) -> None:
        """
        Connect to corpus database.

        Args:
            db_path: Path to banished.db
            tag_filter: Optional tag to filter posts (for backwards compat)
            chunk_size: Batch size for iter_posts (for backwards compat)
        """
        self.db_path = Path(db_path)
        self._tag_filter = tag_filter
        self._chunk_size = chunk_size
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazy connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get_post(self, post_id: int) -> StoryPost | None:
        """Fetch single post by ID with all tags."""
        cursor = self.conn.execute(
            """
            SELECT p.id as post_id, p.thread_id, p.body, p.name as author, p.time
            FROM post p
            WHERE p.id = ?
            """,
            (post_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        tags = self._get_tags(post_id)
        return StoryPost(
            post_id=row["post_id"],
            thread_id=row["thread_id"],
            body=row["body"] or "",
            author=row["author"],
            created_at=self._parse_unix_timestamp(row["time"]),
            tags=tags,
        )

    def get_posts_range(
        self,
        thread_id: int,
        *,
        start_post_id: int | None = None,
        end_post_id: int | None = None,
        tag_filter: str | None = None,
    ) -> list[StoryPost]:
        """Fetch posts in range within thread, optionally filtered by tag."""
        params: list[int | str] = [thread_id]
        conditions = ["p.thread_id = ?"]

        if start_post_id is not None:
            conditions.append("p.id >= ?")
            params.append(start_post_id)

        if end_post_id is not None:
            conditions.append("p.id <= ?")
            params.append(end_post_id)

        if tag_filter is not None:
            conditions.append("p.id IN (SELECT post_id FROM tag WHERE name = ?)")
            params.append(tag_filter)

        query = f"""
            SELECT p.id as post_id, p.thread_id, p.body, p.name as author, p.time
            FROM post p
            WHERE {" AND ".join(conditions)}
            ORDER BY p.time ASC, p.id ASC
        """

        cursor = self.conn.execute(query, params)
        posts = []
        for row in cursor:
            tags = self._get_tags(row["post_id"])
            posts.append(
                StoryPost(
                    post_id=row["post_id"],
                    thread_id=row["thread_id"],
                    body=row["body"] or "",
                    author=row["author"],
                    created_at=self._parse_unix_timestamp(row["time"]),
                    tags=tags,
                )
            )
        return posts

    def get_adjacent_posts(
        self,
        post_id: int,
        before: int = 2,
        after: int = 2,
    ) -> list[StoryPost]:
        """Fetch posts surrounding a given post for context."""
        # Get the target post to find its thread and time
        target = self.get_post(post_id)
        if target is None:
            return []

        # Fetch posts before (same thread, earlier time/id)
        before_cursor = self.conn.execute(
            """
            SELECT p.id as post_id, p.thread_id, p.body, p.name as author, p.time
            FROM post p
            WHERE p.thread_id = ? AND p.id < ?
            ORDER BY p.time DESC, p.id DESC
            LIMIT ?
            """,
            (target.thread_id, post_id, before),
        )
        before_posts = list(before_cursor)[::-1]  # Reverse to chronological

        # Fetch posts after (same thread, later time/id)
        after_cursor = self.conn.execute(
            """
            SELECT p.id as post_id, p.thread_id, p.body, p.name as author, p.time
            FROM post p
            WHERE p.thread_id = ? AND p.id > ?
            ORDER BY p.time ASC, p.id ASC
            LIMIT ?
            """,
            (target.thread_id, post_id, after),
        )
        after_posts = list(after_cursor)

        # Build result list
        posts = []
        for row in before_posts:
            tags = self._get_tags(row["post_id"])
            posts.append(
                StoryPost(
                    post_id=row["post_id"],
                    thread_id=row["thread_id"],
                    body=row["body"] or "",
                    author=row["author"],
                    created_at=self._parse_unix_timestamp(row["time"]),
                    tags=tags,
                )
            )

        posts.append(target)

        for row in after_posts:
            tags = self._get_tags(row["post_id"])
            posts.append(
                StoryPost(
                    post_id=row["post_id"],
                    thread_id=row["thread_id"],
                    body=row["body"] or "",
                    author=row["author"],
                    created_at=self._parse_unix_timestamp(row["time"]),
                    tags=tags,
                )
            )

        return posts

    def iter_threads(self) -> Iterator[Thread]:
        """Yield all threads in chronological order."""
        # Order by the earliest post time in each thread
        cursor = self.conn.execute(
            """
            SELECT t.id, t.title
            FROM thread t
            ORDER BY (SELECT MIN(p.time) FROM post p WHERE p.thread_id = t.id) ASC
            """
        )
        for row in cursor:
            yield Thread(id=row["id"], title=row["title"])

    def iter_posts_by_thread(
        self,
        thread_id: int,
        *,
        start_after_post_id: int | None = None,
    ) -> Iterator[StoryPost]:
        """Yield all posts in a thread, optionally starting after a given post."""
        params: list[int] = [thread_id]
        condition = "p.thread_id = ?"

        if start_after_post_id is not None:
            condition += " AND p.id > ?"
            params.append(start_after_post_id)

        cursor = self.conn.execute(
            f"""
            SELECT p.id as post_id, p.thread_id, p.body, p.name as author, p.time
            FROM post p
            WHERE {condition}
            ORDER BY p.time ASC, p.id ASC
            """,
            params,
        )

        for row in cursor:
            tags = self._get_tags(row["post_id"])
            yield StoryPost(
                post_id=row["post_id"],
                thread_id=row["thread_id"],
                body=row["body"] or "",
                author=row["author"],
                created_at=self._parse_unix_timestamp(row["time"]),
                tags=tags,
            )

    def iter_all_posts(
        self,
        *,
        start_after_post_id: int | None = None,
        tag_filter: str | None = None,
    ) -> Iterator[StoryPost]:
        """Yield all posts across all threads, optionally filtered."""
        params: list[int | str] = []
        conditions: list[str] = []

        if start_after_post_id is not None:
            conditions.append("p.id > ?")
            params.append(start_after_post_id)

        if tag_filter is not None:
            conditions.append("p.id IN (SELECT post_id FROM tag WHERE name = ?)")
            params.append(tag_filter)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cursor = self.conn.execute(
            f"""
            SELECT p.id as post_id, p.thread_id, p.body, p.name as author, p.time
            FROM post p
            {where_clause}
            ORDER BY p.time ASC, p.id ASC
            """,
            params,
        )

        for row in cursor:
            tags = self._get_tags(row["post_id"])
            yield StoryPost(
                post_id=row["post_id"],
                thread_id=row["thread_id"],
                body=row["body"] or "",
                author=row["author"],
                created_at=self._parse_unix_timestamp(row["time"]),
                tags=tags,
            )

    def iter_posts(
        self,
        start_after_id: int | None = None,
        limit: int | None = None,
    ) -> Generator[list[StoryPost], None, None]:
        """
        Yield batches of posts (backwards compatibility).

        This method exists for compatibility with the old API.
        New code should use iter_all_posts() instead.
        """
        batch: list[StoryPost] = []
        count = 0

        for post in self.iter_all_posts(
            start_after_post_id=start_after_id,
            tag_filter=self._tag_filter,
        ):
            batch.append(post)
            count += 1

            if len(batch) >= self._chunk_size:
                yield batch
                batch = []

            if limit and count >= limit:
                break

        if batch:
            yield batch

    def _get_tags(self, post_id: int) -> list[str]:
        """Fetch all tags for a post."""
        cursor = self.conn.execute(
            "SELECT name FROM tag WHERE post_id = ? ORDER BY name",
            (post_id,),
        )
        return [row["name"] for row in cursor]

    @staticmethod
    def _parse_unix_timestamp(value: int | None) -> datetime | None:
        """Parse Unix timestamp to datetime."""
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError):
            return None
