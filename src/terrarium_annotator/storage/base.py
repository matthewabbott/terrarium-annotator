"""Base database connection and migration runner."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from terrarium_annotator.storage.exceptions import DatabaseError, MigrationError

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from terrarium_annotator.storage.migrations import Migration


def utcnow() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """SQLite connection wrapper with migration support."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazy connection with foreign keys enabled."""
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(
                    self.db_path,
                    detect_types=sqlite3.PARSE_DECLTYPES,
                    isolation_level=None,  # autocommit for explicit transaction control
                )
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error as e:
                raise DatabaseError(f"Failed to connect to {self.db_path}: {e}") from e
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager for explicit transactions."""
        conn = self.conn
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get_schema_version(self) -> int:
        """Get current schema version, 0 if no migrations applied."""
        try:
            cursor = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if cursor.fetchone() is None:
                return 0
            cursor = self.conn.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()
            return row[0] if row[0] is not None else 0
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get schema version: {e}") from e

    def run_migrations(self, migrations: list[Migration]) -> int:
        """
        Apply pending migrations.

        Returns: Number of migrations applied.
        """
        current_version = self.get_schema_version()
        applied = 0

        for migration in migrations:
            if migration.version <= current_version:
                LOGGER.debug(
                    "Skipping migration %d (%s): already applied",
                    migration.version,
                    migration.name,
                )
                continue

            try:
                with self.transaction() as conn:
                    for statement in migration.statements:
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (migration.version, utcnow()),
                    )
                applied += 1
                LOGGER.info(
                    "Applied migration %d: %s", migration.version, migration.name
                )
            except sqlite3.Error as e:
                LOGGER.error(
                    "Migration %d (%s) failed: %s",
                    migration.version,
                    migration.name,
                    e,
                )
                raise MigrationError(
                    f"Migration {migration.version} ({migration.name}) failed: {e}"
                ) from e

        return applied
