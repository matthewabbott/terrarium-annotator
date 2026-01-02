"""SQLite-backed glossary store with FTS5 search."""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from terrarium_annotator.storage.base import Database, utcnow
from terrarium_annotator.storage.exceptions import (
    DatabaseError,
    DuplicateTermError,
)
from terrarium_annotator.storage.migrations import get_all_migrations


@dataclass
class GlossaryEntry:
    """A glossary entry with all metadata."""

    id: int | None
    term: str
    term_normalized: str
    definition: str
    status: str
    tags: list[str]
    first_seen_post_id: int
    first_seen_thread_id: int
    last_updated_post_id: int
    last_updated_thread_id: int
    created_at: str
    updated_at: str


def normalize_term(term: str) -> str:
    """Normalize term for deduplication: lowercase, NFD, strip."""
    return unicodedata.normalize("NFD", term.lower().strip())


class GlossaryStore:
    """SQLite-backed glossary with FTS5 search."""

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

    def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        status: Literal["confirmed", "tentative", "all"] = "all",
        limit: int = 10,
    ) -> list[GlossaryEntry]:
        """
        Full-text search on terms and definitions.

        Returns: Matching entries, ordered by relevance.
        Raises: DatabaseError on connection issues.
        """
        try:
            # Build FTS query with optional filters
            params: list[str | int] = []

            # FTS5 match
            fts_query = f'"{query}"' if " " not in query else query
            sql = """
                SELECT e.id, e.term, e.term_normalized, e.definition, e.status,
                       e.first_seen_post_id, e.first_seen_thread_id,
                       e.last_updated_post_id, e.last_updated_thread_id,
                       e.created_at, e.updated_at,
                       bm25(glossary_fts) as rank
                FROM glossary_fts f
                JOIN glossary_entry e ON f.rowid = e.id
                WHERE glossary_fts MATCH ?
            """
            params.append(fts_query)

            if status != "all":
                sql += " AND e.status = ?"
                params.append(status)

            if tags:
                # Entries must have ALL specified tags
                sql += """
                    AND e.id IN (
                        SELECT entry_id FROM glossary_tag
                        WHERE tag IN ({})
                        GROUP BY entry_id
                        HAVING COUNT(DISTINCT tag) = ?
                    )
                """.format(",".join("?" * len(tags)))
                params.extend(tags)
                params.append(len(tags))

            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)

            cursor = self.conn.execute(sql, params)
            rows = cursor.fetchall()

            entries = []
            for row in rows:
                entry_tags = self._get_tags(row["id"])
                entries.append(
                    GlossaryEntry(
                        id=row["id"],
                        term=row["term"],
                        term_normalized=row["term_normalized"],
                        definition=row["definition"],
                        status=row["status"],
                        tags=entry_tags,
                        first_seen_post_id=row["first_seen_post_id"],
                        first_seen_thread_id=row["first_seen_thread_id"],
                        last_updated_post_id=row["last_updated_post_id"],
                        last_updated_thread_id=row["last_updated_thread_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return entries

        except sqlite3.Error as e:
            raise DatabaseError(f"Search failed: {e}") from e

    def get(self, entry_id: int) -> GlossaryEntry | None:
        """Fetch single entry by ID. Returns None if not found."""
        try:
            cursor = self.conn.execute(
                """
                SELECT id, term, term_normalized, definition, status,
                       first_seen_post_id, first_seen_thread_id,
                       last_updated_post_id, last_updated_thread_id,
                       created_at, updated_at
                FROM glossary_entry
                WHERE id = ?
                """,
                (entry_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            return GlossaryEntry(
                id=row["id"],
                term=row["term"],
                term_normalized=row["term_normalized"],
                definition=row["definition"],
                status=row["status"],
                tags=self._get_tags(entry_id),
                first_seen_post_id=row["first_seen_post_id"],
                first_seen_thread_id=row["first_seen_thread_id"],
                last_updated_post_id=row["last_updated_post_id"],
                last_updated_thread_id=row["last_updated_thread_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except sqlite3.Error as e:
            raise DatabaseError(f"Get entry {entry_id} failed: {e}") from e

    def create(
        self,
        term: str,
        definition: str,
        tags: list[str],
        *,
        post_id: int,
        thread_id: int,
        status: str = "tentative",
    ) -> int:
        """
        Insert new entry.

        Returns: New entry ID.
        Raises: DuplicateTermError if term_normalized exists.
        """
        normalized = normalize_term(term)
        now = utcnow()

        try:
            # Check for existing
            cursor = self.conn.execute(
                "SELECT id FROM glossary_entry WHERE term_normalized = ?",
                (normalized,),
            )
            existing = cursor.fetchone()
            if existing:
                raise DuplicateTermError(term, existing["id"])

            with self._db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO glossary_entry (
                        term, term_normalized, definition, status,
                        first_seen_post_id, first_seen_thread_id,
                        last_updated_post_id, last_updated_thread_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        term,
                        normalized,
                        definition,
                        status,
                        post_id,
                        thread_id,
                        post_id,
                        thread_id,
                        now,
                        now,
                    ),
                )
                entry_id = cursor.lastrowid

                # Insert tags
                for tag in tags:
                    conn.execute(
                        "INSERT INTO glossary_tag (entry_id, tag) VALUES (?, ?)",
                        (entry_id, tag),
                    )

            return entry_id

        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                cursor = self.conn.execute(
                    "SELECT id FROM glossary_entry WHERE term_normalized = ?",
                    (normalized,),
                )
                existing = cursor.fetchone()
                raise DuplicateTermError(
                    term, existing["id"] if existing else -1
                ) from e
            raise DatabaseError(f"Create entry failed: {e}") from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Create entry failed: {e}") from e

    def update(
        self,
        entry_id: int,
        *,
        term: str | None = None,
        definition: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        post_id: int,
        thread_id: int,
    ) -> bool:
        """
        Update entry fields. Only provided fields modified.

        Returns: True if updated, False if entry not found.
        Note: Caller should log to revision table before calling.
        """
        # Check entry exists
        existing = self.get(entry_id)
        if existing is None:
            return False

        now = utcnow()

        try:
            with self._db.transaction() as conn:
                # Build dynamic UPDATE
                updates = [
                    "last_updated_post_id = ?",
                    "last_updated_thread_id = ?",
                    "updated_at = ?",
                ]
                params: list[str | int] = [post_id, thread_id, now]

                if term is not None:
                    updates.append("term = ?")
                    updates.append("term_normalized = ?")
                    params.append(term)
                    params.append(normalize_term(term))

                if definition is not None:
                    updates.append("definition = ?")
                    params.append(definition)

                if status is not None:
                    updates.append("status = ?")
                    params.append(status)

                params.append(entry_id)

                conn.execute(
                    f"UPDATE glossary_entry SET {', '.join(updates)} WHERE id = ?",
                    params,
                )

                # Update tags if provided
                if tags is not None:
                    conn.execute(
                        "DELETE FROM glossary_tag WHERE entry_id = ?", (entry_id,)
                    )
                    for tag in tags:
                        conn.execute(
                            "INSERT INTO glossary_tag (entry_id, tag) VALUES (?, ?)",
                            (entry_id, tag),
                        )

            return True

        except sqlite3.Error as e:
            raise DatabaseError(f"Update entry {entry_id} failed: {e}") from e

    def delete(self, entry_id: int, reason: str) -> bool:
        """
        Delete entry.

        Returns: True if deleted, False if not found.
        Note: Caller should log reason to revision table before calling.
        """
        try:
            cursor = self.conn.execute(
                "DELETE FROM glossary_entry WHERE id = ?",
                (entry_id,),
            )
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise DatabaseError(f"Delete entry {entry_id} failed: {e}") from e

    def all_entries(self) -> Iterator[GlossaryEntry]:
        """Yield all entries. Use for export operations."""
        try:
            cursor = self.conn.execute(
                """
                SELECT id, term, term_normalized, definition, status,
                       first_seen_post_id, first_seen_thread_id,
                       last_updated_post_id, last_updated_thread_id,
                       created_at, updated_at
                FROM glossary_entry
                ORDER BY term_normalized
                """
            )
            for row in cursor:
                yield GlossaryEntry(
                    id=row["id"],
                    term=row["term"],
                    term_normalized=row["term_normalized"],
                    definition=row["definition"],
                    status=row["status"],
                    tags=self._get_tags(row["id"]),
                    first_seen_post_id=row["first_seen_post_id"],
                    first_seen_thread_id=row["first_seen_thread_id"],
                    last_updated_post_id=row["last_updated_post_id"],
                    last_updated_thread_id=row["last_updated_thread_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
        except sqlite3.Error as e:
            raise DatabaseError(f"all_entries failed: {e}") from e

    def count(self) -> int:
        """Return total number of entries."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM glossary_entry")
        return cursor.fetchone()[0]

    def get_by_thread(
        self,
        thread_id: int,
        field: Literal["first_seen_thread_id", "last_updated_thread_id"] = "first_seen_thread_id",
    ) -> list[GlossaryEntry]:
        """
        Get entries associated with a thread.

        Args:
            thread_id: Thread ID to query.
            field: Which field to match - entries created in or last updated in thread.

        Returns:
            List of matching entries.
        """
        if field not in ("first_seen_thread_id", "last_updated_thread_id"):
            raise ValueError(f"Invalid field: {field}")

        try:
            cursor = self.conn.execute(
                f"""
                SELECT id, term, term_normalized, definition, status,
                       first_seen_post_id, first_seen_thread_id,
                       last_updated_post_id, last_updated_thread_id,
                       created_at, updated_at
                FROM glossary_entry
                WHERE {field} = ?
                ORDER BY term_normalized
                """,
                (thread_id,),
            )
            entries = []
            for row in cursor:
                entries.append(
                    GlossaryEntry(
                        id=row["id"],
                        term=row["term"],
                        term_normalized=row["term_normalized"],
                        definition=row["definition"],
                        status=row["status"],
                        tags=self._get_tags(row["id"]),
                        first_seen_post_id=row["first_seen_post_id"],
                        first_seen_thread_id=row["first_seen_thread_id"],
                        last_updated_post_id=row["last_updated_post_id"],
                        last_updated_thread_id=row["last_updated_thread_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return entries
        except sqlite3.Error as e:
            raise DatabaseError(f"get_by_thread failed: {e}") from e

    def get_tentative_by_thread(self, thread_id: int) -> list[GlossaryEntry]:
        """
        Get tentative entries created in a specific thread.

        Args:
            thread_id: Thread ID to query.

        Returns:
            List of tentative entries first seen in this thread.
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT id, term, term_normalized, definition, status,
                       first_seen_post_id, first_seen_thread_id,
                       last_updated_post_id, last_updated_thread_id,
                       created_at, updated_at
                FROM glossary_entry
                WHERE first_seen_thread_id = ? AND status = 'tentative'
                ORDER BY term_normalized
                """,
                (thread_id,),
            )
            entries = []
            for row in cursor:
                entries.append(
                    GlossaryEntry(
                        id=row["id"],
                        term=row["term"],
                        term_normalized=row["term_normalized"],
                        definition=row["definition"],
                        status=row["status"],
                        tags=self._get_tags(row["id"]),
                        first_seen_post_id=row["first_seen_post_id"],
                        first_seen_thread_id=row["first_seen_thread_id"],
                        last_updated_post_id=row["last_updated_post_id"],
                        last_updated_thread_id=row["last_updated_thread_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return entries
        except sqlite3.Error as e:
            raise DatabaseError(f"get_tentative_by_thread failed: {e}") from e

    def _get_tags(self, entry_id: int) -> list[str]:
        """Fetch tags for an entry."""
        cursor = self.conn.execute(
            "SELECT tag FROM glossary_tag WHERE entry_id = ? ORDER BY tag",
            (entry_id,),
        )
        return [row["tag"] for row in cursor]
