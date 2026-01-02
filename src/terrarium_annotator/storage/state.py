"""Run state persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from terrarium_annotator.storage.base import Database, utcnow
from terrarium_annotator.storage.exceptions import DatabaseError
from terrarium_annotator.storage.migrations import get_all_migrations


@dataclass
class RunState:
    """Current run state."""

    last_post_id: int | None
    last_thread_id: int | None
    current_snapshot_id: int | None
    run_started_at: str | None
    run_updated_at: str | None
    total_posts_processed: int
    total_entries_created: int
    total_entries_updated: int


@dataclass
class ThreadState:
    """Per-thread progress state."""

    thread_id: int
    status: str
    summary: str | None
    posts_processed: int
    entries_created: int
    entries_updated: int
    started_at: str | None
    completed_at: str | None


class ProgressTracker:
    """Run state persistence."""

    def __init__(self, db_path: Path | str) -> None:
        """Connect to annotator.db."""
        self._db = Database(db_path)
        self._db.run_migrations(get_all_migrations())

    @property
    def conn(self) -> sqlite3.Connection:
        """Access underlying connection."""
        return self._db.conn

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def get_state(self) -> RunState:
        """Load current run state from singleton row."""
        try:
            cursor = self.conn.execute(
                """
                SELECT last_post_id, last_thread_id, current_snapshot_id,
                       run_started_at, run_updated_at,
                       total_posts_processed, total_entries_created, total_entries_updated
                FROM run_state
                WHERE id = 1
                """
            )
            row = cursor.fetchone()
            return RunState(
                last_post_id=row["last_post_id"],
                last_thread_id=row["last_thread_id"],
                current_snapshot_id=row["current_snapshot_id"],
                run_started_at=row["run_started_at"],
                run_updated_at=row["run_updated_at"],
                total_posts_processed=row["total_posts_processed"] or 0,
                total_entries_created=row["total_entries_created"] or 0,
                total_entries_updated=row["total_entries_updated"] or 0,
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Get state failed: {e}") from e

    def update(
        self,
        *,
        last_post_id: int | None = None,
        last_thread_id: int | None = None,
        current_snapshot_id: int | None = None,
        posts_processed_delta: int = 0,
        entries_created_delta: int = 0,
        entries_updated_delta: int = 0,
    ) -> None:
        """Update run state. Deltas are added to current totals."""
        try:
            updates = ["run_updated_at = ?"]
            params: list[str | int | None] = [utcnow()]

            if last_post_id is not None:
                updates.append("last_post_id = ?")
                params.append(last_post_id)

            if last_thread_id is not None:
                updates.append("last_thread_id = ?")
                params.append(last_thread_id)

            if current_snapshot_id is not None:
                updates.append("current_snapshot_id = ?")
                params.append(current_snapshot_id)

            if posts_processed_delta != 0:
                updates.append("total_posts_processed = total_posts_processed + ?")
                params.append(posts_processed_delta)

            if entries_created_delta != 0:
                updates.append("total_entries_created = total_entries_created + ?")
                params.append(entries_created_delta)

            if entries_updated_delta != 0:
                updates.append("total_entries_updated = total_entries_updated + ?")
                params.append(entries_updated_delta)

            self.conn.execute(
                f"UPDATE run_state SET {', '.join(updates)} WHERE id = 1",
                params,
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Update state failed: {e}") from e

    def start_run(self) -> None:
        """Mark a new run as started."""
        try:
            now = utcnow()
            self.conn.execute(
                "UPDATE run_state SET run_started_at = ?, run_updated_at = ? WHERE id = 1",
                (now, now),
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Start run failed: {e}") from e

    # Thread state methods

    def get_thread_state(self, thread_id: int) -> ThreadState | None:
        """Get state for a specific thread."""
        try:
            cursor = self.conn.execute(
                """
                SELECT thread_id, status, summary, posts_processed,
                       entries_created, entries_updated, started_at, completed_at
                FROM thread_state
                WHERE thread_id = ?
                """,
                (thread_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return ThreadState(
                thread_id=row["thread_id"],
                status=row["status"],
                summary=row["summary"],
                posts_processed=row["posts_processed"] or 0,
                entries_created=row["entries_created"] or 0,
                entries_updated=row["entries_updated"] or 0,
                started_at=row["started_at"],
                completed_at=row["completed_at"],
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Get thread state failed: {e}") from e

    def update_thread_state(
        self,
        thread_id: int,
        *,
        status: str | None = None,
        summary: str | None = None,
        posts_processed_delta: int = 0,
        entries_created_delta: int = 0,
        entries_updated_delta: int = 0,
    ) -> None:
        """Update or create thread state."""
        try:
            existing = self.get_thread_state(thread_id)
            now = utcnow()

            if existing is None:
                # Insert new thread state
                self.conn.execute(
                    """
                    INSERT INTO thread_state (
                        thread_id, status, summary, posts_processed,
                        entries_created, entries_updated, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        status or "pending",
                        summary,
                        posts_processed_delta,
                        entries_created_delta,
                        entries_updated_delta,
                        now if status == "in_progress" else None,
                    ),
                )
            else:
                updates = []
                params: list[str | int | None] = []

                if status is not None:
                    updates.append("status = ?")
                    params.append(status)
                    if status == "in_progress" and existing.started_at is None:
                        updates.append("started_at = ?")
                        params.append(now)
                    elif status == "completed":
                        updates.append("completed_at = ?")
                        params.append(now)

                if summary is not None:
                    updates.append("summary = ?")
                    params.append(summary)

                if posts_processed_delta != 0:
                    updates.append("posts_processed = posts_processed + ?")
                    params.append(posts_processed_delta)

                if entries_created_delta != 0:
                    updates.append("entries_created = entries_created + ?")
                    params.append(entries_created_delta)

                if entries_updated_delta != 0:
                    updates.append("entries_updated = entries_updated + ?")
                    params.append(entries_updated_delta)

                if updates:
                    params.append(thread_id)
                    self.conn.execute(
                        f"UPDATE thread_state SET {', '.join(updates)} WHERE thread_id = ?",
                        params,
                    )
        except sqlite3.Error as e:
            raise DatabaseError(f"Update thread state failed: {e}") from e

    def get_completed_threads(self) -> list[ThreadState]:
        """Get all completed threads."""
        try:
            cursor = self.conn.execute(
                """
                SELECT thread_id, status, summary, posts_processed,
                       entries_created, entries_updated, started_at, completed_at
                FROM thread_state
                WHERE status = 'completed'
                ORDER BY completed_at
                """
            )
            return [
                ThreadState(
                    thread_id=row["thread_id"],
                    status=row["status"],
                    summary=row["summary"],
                    posts_processed=row["posts_processed"] or 0,
                    entries_created=row["entries_created"] or 0,
                    entries_updated=row["entries_updated"] or 0,
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                )
                for row in cursor
            ]
        except sqlite3.Error as e:
            raise DatabaseError(f"Get completed threads failed: {e}") from e
