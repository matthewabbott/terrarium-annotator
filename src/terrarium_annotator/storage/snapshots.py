"""SQLite-backed snapshot store for context checkpointing."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from terrarium_annotator.storage.base import Database, utcnow
from terrarium_annotator.storage.exceptions import DatabaseError
from terrarium_annotator.storage.migrations import get_all_migrations

if TYPE_CHECKING:
    from terrarium_annotator.context.annotation import AnnotationContext
    from terrarium_annotator.context.compactor import CompactionState
    from terrarium_annotator.storage.glossary import GlossaryStore


@dataclass
class Snapshot:
    """A point-in-time capture of annotation state."""

    id: int | None
    snapshot_type: Literal["checkpoint", "curator_fork", "manual"]
    created_at: str
    last_post_id: int
    last_thread_id: int
    thread_position: int
    glossary_entry_count: int
    context_token_count: int | None
    metadata: dict | None


@dataclass
class SnapshotContext:
    """Context state at snapshot time."""

    snapshot_id: int
    system_prompt: str
    cumulative_summary: str | None
    thread_summaries: list[dict]  # Serialized ThreadSummary dicts
    conversation_history: list[dict]
    current_thread_id: int | None
    completed_thread_ids: list[int]  # For proper Tier 1 compaction on resume


@dataclass
class SnapshotEntry:
    """Entry state at snapshot time."""

    snapshot_id: int
    entry_id: int
    definition_at_snapshot: str
    status_at_snapshot: str


class SnapshotStore:
    """SQLite-backed store for snapshots with context and entry capture."""

    def __init__(self, db_path: Path | str) -> None:
        """Connect to or create annotator.db. Runs migrations if needed."""
        self._db = Database(db_path)
        self._db.run_migrations(get_all_migrations())

    @property
    def conn(self) -> sqlite3.Connection:
        """Access underlying connection."""
        return self._db.conn

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def create(
        self,
        *,
        snapshot_type: Literal["checkpoint", "curator_fork", "manual"],
        last_post_id: int,
        last_thread_id: int,
        thread_position: int,
        context: AnnotationContext,
        compaction_state: CompactionState,
        glossary: GlossaryStore,
        token_count: int | None = None,
        metadata: dict | None = None,
    ) -> int:
        """
        Create snapshot with full entry capture.

        Args:
            snapshot_type: Type of snapshot.
            last_post_id: Last processed post ID.
            last_thread_id: Last processed thread ID.
            thread_position: Ordinal position in processing.
            context: AnnotationContext to serialize.
            compaction_state: CompactionState to serialize.
            glossary: GlossaryStore to capture entries from.
            token_count: Optional token count of context.
            metadata: Optional JSON metadata.

        Returns:
            New snapshot ID.
        """
        now = utcnow()
        entry_count = glossary.count()

        try:
            with self._db.transaction() as conn:
                # Insert snapshot record
                cursor = conn.execute(
                    """
                    INSERT INTO snapshot (
                        snapshot_type, created_at, last_post_id, last_thread_id,
                        thread_position, glossary_entry_count, context_token_count,
                        metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_type,
                        now,
                        last_post_id,
                        last_thread_id,
                        thread_position,
                        entry_count,
                        token_count,
                        json.dumps(metadata) if metadata else None,
                    ),
                )
                snapshot_id = cursor.lastrowid

                # Insert context
                compaction_dict = compaction_state.to_dict()
                context_dict = context.to_dict()
                conn.execute(
                    """
                    INSERT INTO snapshot_context (
                        snapshot_id, system_prompt, cumulative_summary,
                        thread_summaries, conversation_history, current_thread_id,
                        completed_thread_ids
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        context_dict["system_prompt"],
                        compaction_dict.get("cumulative_summary") or None,
                        json.dumps(compaction_dict.get("thread_summaries", [])),
                        json.dumps(context_dict.get("conversation_history", [])),
                        last_thread_id,
                        json.dumps(compaction_dict.get("completed_thread_ids", [])),
                    ),
                )

                # Capture all glossary entries
                for entry in glossary.all_entries():
                    conn.execute(
                        """
                        INSERT INTO snapshot_entry (
                            snapshot_id, entry_id, definition_at_snapshot,
                            status_at_snapshot
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            entry.id,
                            entry.definition,
                            entry.status,
                        ),
                    )

            return snapshot_id

        except sqlite3.Error as e:
            raise DatabaseError(f"Create snapshot failed: {e}") from e

    def get(self, snapshot_id: int) -> Snapshot | None:
        """Fetch snapshot by ID. Returns None if not found."""
        try:
            cursor = self.conn.execute(
                """
                SELECT id, snapshot_type, created_at, last_post_id, last_thread_id,
                       thread_position, glossary_entry_count, context_token_count,
                       metadata
                FROM snapshot
                WHERE id = ?
                """,
                (snapshot_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            return Snapshot(
                id=row["id"],
                snapshot_type=row["snapshot_type"],
                created_at=row["created_at"],
                last_post_id=row["last_post_id"],
                last_thread_id=row["last_thread_id"],
                thread_position=row["thread_position"],
                glossary_entry_count=row["glossary_entry_count"],
                context_token_count=row["context_token_count"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Get snapshot {snapshot_id} failed: {e}") from e

    def get_context(self, snapshot_id: int) -> SnapshotContext | None:
        """Fetch context for a snapshot. Returns None if not found."""
        try:
            cursor = self.conn.execute(
                """
                SELECT snapshot_id, system_prompt, cumulative_summary,
                       thread_summaries, conversation_history, current_thread_id,
                       completed_thread_ids
                FROM snapshot_context
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            return SnapshotContext(
                snapshot_id=row["snapshot_id"],
                system_prompt=row["system_prompt"],
                cumulative_summary=row["cumulative_summary"],
                thread_summaries=json.loads(row["thread_summaries"]),
                conversation_history=json.loads(row["conversation_history"]),
                current_thread_id=row["current_thread_id"],
                completed_thread_ids=json.loads(row["completed_thread_ids"] or "[]"),
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Get snapshot context {snapshot_id} failed: {e}") from e

    def get_entries(self, snapshot_id: int) -> list[SnapshotEntry]:
        """Fetch all entry states for a snapshot."""
        try:
            cursor = self.conn.execute(
                """
                SELECT snapshot_id, entry_id, definition_at_snapshot, status_at_snapshot
                FROM snapshot_entry
                WHERE snapshot_id = ?
                ORDER BY entry_id
                """,
                (snapshot_id,),
            )
            return [
                SnapshotEntry(
                    snapshot_id=row["snapshot_id"],
                    entry_id=row["entry_id"],
                    definition_at_snapshot=row["definition_at_snapshot"],
                    status_at_snapshot=row["status_at_snapshot"],
                )
                for row in cursor
            ]
        except sqlite3.Error as e:
            raise DatabaseError(f"Get snapshot entries {snapshot_id} failed: {e}") from e

    def list_recent(
        self,
        limit: int = 20,
        snapshot_type: str | None = None,
    ) -> list[Snapshot]:
        """List recent snapshots, newest first."""
        try:
            if snapshot_type:
                cursor = self.conn.execute(
                    """
                    SELECT id, snapshot_type, created_at, last_post_id, last_thread_id,
                           thread_position, glossary_entry_count, context_token_count,
                           metadata
                    FROM snapshot
                    WHERE snapshot_type = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (snapshot_type, limit),
                )
            else:
                cursor = self.conn.execute(
                    """
                    SELECT id, snapshot_type, created_at, last_post_id, last_thread_id,
                           thread_position, glossary_entry_count, context_token_count,
                           metadata
                    FROM snapshot
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

            return [
                Snapshot(
                    id=row["id"],
                    snapshot_type=row["snapshot_type"],
                    created_at=row["created_at"],
                    last_post_id=row["last_post_id"],
                    last_thread_id=row["last_thread_id"],
                    thread_position=row["thread_position"],
                    glossary_entry_count=row["glossary_entry_count"],
                    context_token_count=row["context_token_count"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                )
                for row in cursor
            ]
        except sqlite3.Error as e:
            raise DatabaseError(f"List snapshots failed: {e}") from e

    def list_by_thread(self, thread_id: int) -> list[Snapshot]:
        """List snapshots for a specific thread."""
        try:
            cursor = self.conn.execute(
                """
                SELECT id, snapshot_type, created_at, last_post_id, last_thread_id,
                       thread_position, glossary_entry_count, context_token_count,
                       metadata
                FROM snapshot
                WHERE last_thread_id = ?
                ORDER BY created_at DESC
                """,
                (thread_id,),
            )
            return [
                Snapshot(
                    id=row["id"],
                    snapshot_type=row["snapshot_type"],
                    created_at=row["created_at"],
                    last_post_id=row["last_post_id"],
                    last_thread_id=row["last_thread_id"],
                    thread_position=row["thread_position"],
                    glossary_entry_count=row["glossary_entry_count"],
                    context_token_count=row["context_token_count"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                )
                for row in cursor
            ]
        except sqlite3.Error as e:
            raise DatabaseError(f"List snapshots by thread failed: {e}") from e

    def restore_context(
        self, snapshot_id: int
    ) -> tuple[AnnotationContext, CompactionState]:
        """
        Reconstruct context and compaction state from snapshot.

        Args:
            snapshot_id: Snapshot to restore from.

        Returns:
            Tuple of (AnnotationContext, CompactionState).

        Raises:
            DatabaseError: If snapshot not found or restore fails.
        """
        # Import here to avoid circular imports
        from terrarium_annotator.context.annotation import AnnotationContext
        from terrarium_annotator.context.compactor import CompactionState

        ctx_data = self.get_context(snapshot_id)
        if ctx_data is None:
            raise DatabaseError(f"Snapshot {snapshot_id} not found")

        # Reconstruct AnnotationContext
        context = AnnotationContext.from_dict({
            "system_prompt": ctx_data.system_prompt,
            "conversation_history": ctx_data.conversation_history,
        })

        # Reconstruct CompactionState
        compaction = CompactionState.from_dict({
            "cumulative_summary": ctx_data.cumulative_summary or "",
            "thread_summaries": ctx_data.thread_summaries,
            "completed_thread_ids": ctx_data.completed_thread_ids,
        })

        return context, compaction

    def restore_entries_view(self, snapshot_id: int) -> dict[int, SnapshotEntry]:
        """
        Return entry states as a dict keyed by entry_id for lookup.

        Useful for comparing historical state to current state.
        """
        entries = self.get_entries(snapshot_id)
        return {e.entry_id: e for e in entries}

    def delete(self, snapshot_id: int) -> bool:
        """
        Delete a snapshot and all associated data.

        Returns: True if deleted, False if not found.
        """
        try:
            cursor = self.conn.execute(
                "DELETE FROM snapshot WHERE id = ?",
                (snapshot_id,),
            )
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise DatabaseError(f"Delete snapshot {snapshot_id} failed: {e}") from e

    def count(self) -> int:
        """Return total number of snapshots."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM snapshot")
        return cursor.fetchone()[0]

    def count_by_type(self) -> dict[str, int]:
        """Return counts grouped by snapshot type."""
        cursor = self.conn.execute(
            "SELECT snapshot_type, COUNT(*) as cnt FROM snapshot GROUP BY snapshot_type"
        )
        return {row["snapshot_type"]: row["cnt"] for row in cursor}
