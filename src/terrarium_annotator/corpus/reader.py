"""Read-only access to the quest corpus.

Opens the corpus database in SQLite read-only mode (URI `mode=ro` with
`uri=True` — without the flag the `file:` string is a literal filename).
The thread-ordering resolver is the query verified in
`docs/design/dev-verification.md` (L3): thread start = OP-post time,
falling back to earliest post time for threads without an `op_post` tag.
Thread IDs are opaque and non-chronological (the corpus mixes sources);
never order by `thread.id`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from terrarium_annotator.corpus.models import Batch, Post, Thread

# Verified against banished.db 2026-09-02: no thread has more than one
# distinct OP post; duplicate tag rows are absorbed by the aggregate.
THREAD_ORDER_SQL = """
SELECT t.id,
       t.title,
       COALESCE(MIN(CASE WHEN tg.name = 'op_post' THEN p.time END),
                MIN(p.time)) AS started
FROM thread t
JOIN post p ON p.thread_id = t.id
LEFT JOIN tag tg ON tg.post_id = p.id AND tg.name = 'op_post'
GROUP BY t.id
ORDER BY started ASC
"""

DEFAULT_BATCH_SIZE = 5  # story posts per batch; provisional (plan T8)


class CorpusReader:
    """Streams threads, posts, and batches from a read-only corpus DB."""

    def __init__(self, db_path: Path | str, *, tag: str = "story_post") -> None:
        self.db_path = Path(db_path)
        self.tag = tag
        self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def thread_order(self) -> list[Thread]:
        """All threads in chronological reading order (OP-post time)."""
        rows = self._conn.execute(THREAD_ORDER_SQL)
        return [Thread(id=r[0], title=r[1], started=r[2]) for r in rows]

    def story_posts(self, thread_id: int) -> Iterator[Post]:
        """Posts of `thread_id` carrying the configured tag, time-ordered."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT p.id, p.thread_id, p.time, p.name, p.subject, p.body
            FROM post p
            JOIN tag tg ON tg.post_id = p.id AND tg.name = ?
            WHERE p.thread_id = ?
            ORDER BY p.time ASC, p.id ASC
            """,
            (self.tag, thread_id),
        )
        for r in rows:
            yield Post(
                id=r[0],
                thread_id=r[1],
                time=r[2],
                name=r[3] or "",
                subject=r[4] or "",
                body=r[5] or "",
            )

    def search_posts(self, needle: str, limit: int = 20) -> list[Post]:
        """Corpus-wide substring search over post bodies (researcher tool).

        Searches ALL posts (not just story posts), any thread, including
        beyond the annotator's read position — the researcher works
        corpus-wide by design. Case-insensitive LIKE; a 35MB full scan is
        subsecond at this corpus size.
        """
        rows = self._conn.execute(
            "SELECT id, thread_id, time, name, subject, body FROM post "
            "WHERE body LIKE ? ORDER BY time ASC LIMIT ?",
            (f"%{needle}%", limit),
        )
        return [
            Post(
                id=r[0],
                thread_id=r[1],
                time=r[2],
                name=r[3] or "",
                subject=r[4] or "",
                body=r[5] or "",
            )
            for r in rows
        ]

    def post_body(self, post_id: int) -> str | None:
        """Body of a single post by id, or None if absent. Quote checks."""
        row = self._conn.execute(
            "SELECT body FROM post WHERE id = ?", (post_id,)
        ).fetchone()
        return row[0] if row else None

    def post_thread(self, post_id: int) -> int | None:
        """Thread id for a post — provenance derives blame from evidence."""
        row = self._conn.execute(
            "SELECT thread_id FROM post WHERE id = ?", (post_id,)
        ).fetchone()
        return row[0] if row else None

    def batches(
        self, thread_id: int, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> Iterator[Batch]:
        """Fixed-size batches of story posts; never crosses the thread edge."""
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        chunk: list[Post] = []
        index = 0
        for post in self.story_posts(thread_id):
            chunk.append(post)
            if len(chunk) == batch_size:
                yield Batch(thread_id=thread_id, index=index, posts=tuple(chunk))
                index += 1
                chunk = []
        if chunk:
            yield Batch(thread_id=thread_id, index=index, posts=tuple(chunk))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CorpusReader:  # noqa: PYI034 — Self needs 3.11; floor is 3.10
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
