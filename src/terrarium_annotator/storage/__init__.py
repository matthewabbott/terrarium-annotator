"""SQLite-backed storage layer for annotator state."""

from terrarium_annotator.storage.base import Database, utcnow
from terrarium_annotator.storage.exceptions import (
    DatabaseError,
    DuplicateTermError,
    EntryNotFoundError,
    MigrationError,
    SnapshotNotFoundError,
    StorageError,
)
from terrarium_annotator.storage.glossary import (
    GlossaryEntry,
    GlossaryStore,
    normalize_term,
)
from terrarium_annotator.storage.migrations import Migration, get_all_migrations
from terrarium_annotator.storage.revisions import Revision, RevisionHistory
from terrarium_annotator.storage.state import ProgressTracker, RunState, ThreadState

__all__ = [
    # Base
    "Database",
    "utcnow",
    # Exceptions
    "DatabaseError",
    "DuplicateTermError",
    "EntryNotFoundError",
    "MigrationError",
    "SnapshotNotFoundError",
    "StorageError",
    # Glossary
    "GlossaryEntry",
    "GlossaryStore",
    "normalize_term",
    # Migrations
    "Migration",
    "get_all_migrations",
    # Revisions
    "Revision",
    "RevisionHistory",
    # State
    "ProgressTracker",
    "RunState",
    "ThreadState",
]
