"""Storage-specific exceptions."""


class StorageError(Exception):
    """Base exception for storage operations."""


class DatabaseError(StorageError):
    """Database connection or query failure."""


class MigrationError(StorageError):
    """Schema migration failure."""


class DuplicateTermError(StorageError):
    """Attempt to create entry with existing normalized term."""

    def __init__(self, term: str, existing_id: int) -> None:
        self.term = term
        self.existing_id = existing_id
        super().__init__(f"Term '{term}' already exists (id={existing_id})")


class SnapshotNotFoundError(StorageError):
    """Requested snapshot ID does not exist."""

    def __init__(self, snapshot_id: int) -> None:
        self.snapshot_id = snapshot_id
        super().__init__(f"Snapshot {snapshot_id} not found")


class EntryNotFoundError(StorageError):
    """Requested glossary entry ID does not exist."""

    def __init__(self, entry_id: int) -> None:
        self.entry_id = entry_id
        super().__init__(f"Glossary entry {entry_id} not found")
