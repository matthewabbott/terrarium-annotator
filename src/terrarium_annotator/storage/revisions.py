"""Audit log for glossary changes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from terrarium_annotator.storage.base import Database, utcnow
from terrarium_annotator.storage.exceptions import DatabaseError
from terrarium_annotator.storage.migrations import get_all_migrations


@dataclass
class Revision:
    """A single change record."""

    id: int
    entry_id: int
    snapshot_id: int | None
    field_name: str
    old_value: str | None
    new_value: str
    changed_at: str
    source_post_id: int | None


class RevisionHistory:
    """Audit log for glossary changes."""

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

    def log_change(
        self,
        entry_id: int,
        field_name: str,
        old_value: str | None,
        new_value: str,
        *,
        source_post_id: int | None = None,
        snapshot_id: int | None = None,
    ) -> int:
        """
        Record a change to a glossary entry.

        Returns: Revision ID.
        """
        try:
            cursor = self.conn.execute(
                """
                INSERT INTO revision (
                    entry_id, snapshot_id, field_name, old_value, new_value,
                    changed_at, source_post_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    snapshot_id,
                    field_name,
                    old_value,
                    new_value,
                    utcnow(),
                    source_post_id,
                ),
            )
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Log change failed: {e}") from e

    def get_history(
        self,
        entry_id: int,
        *,
        limit: int = 50,
    ) -> list[Revision]:
        """Get change history for an entry, newest first."""
        try:
            cursor = self.conn.execute(
                """
                SELECT id, entry_id, snapshot_id, field_name, old_value, new_value,
                       changed_at, source_post_id
                FROM revision
                WHERE entry_id = ?
                ORDER BY changed_at DESC
                LIMIT ?
                """,
                (entry_id, limit),
            )
            return [
                Revision(
                    id=row["id"],
                    entry_id=row["entry_id"],
                    snapshot_id=row["snapshot_id"],
                    field_name=row["field_name"],
                    old_value=row["old_value"],
                    new_value=row["new_value"],
                    changed_at=row["changed_at"],
                    source_post_id=row["source_post_id"],
                )
                for row in cursor
            ]
        except sqlite3.Error as e:
            raise DatabaseError(f"Get history failed: {e}") from e

    def log_creation(
        self,
        entry_id: int,
        term: str,
        definition: str,
        tags: list[str],
        status: str,
        *,
        source_post_id: int | None = None,
    ) -> None:
        """Log a new entry creation (convenience method)."""
        import json

        self.log_change(entry_id, "term", None, term, source_post_id=source_post_id)
        self.log_change(
            entry_id, "definition", None, definition, source_post_id=source_post_id
        )
        self.log_change(
            entry_id, "tags", None, json.dumps(tags), source_post_id=source_post_id
        )
        self.log_change(entry_id, "status", None, status, source_post_id=source_post_id)

    def log_deletion(
        self,
        entry_id: int,
        reason: str,
        *,
        source_post_id: int | None = None,
        snapshot_id: int | None = None,
    ) -> int:
        """
        Log an entry deletion.

        Returns: Revision ID.
        """
        return self.log_change(
            entry_id,
            "deleted",
            None,
            reason,
            source_post_id=source_post_id,
            snapshot_id=snapshot_id,
        )
