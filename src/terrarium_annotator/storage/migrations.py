"""Schema migrations for annotator.db."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Migration:
    """A single schema migration."""

    version: int
    name: str
    statements: list[str]


# Migration 001: Initial schema with all core tables
MIGRATION_001_INITIAL = Migration(
    version=1,
    name="initial_schema",
    statements=[
        # Schema version tracking
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """,
        # Glossary entry table
        """
        CREATE TABLE glossary_entry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            term_normalized TEXT NOT NULL,
            definition TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'tentative'
                CHECK (status IN ('confirmed', 'tentative')),
            first_seen_post_id INTEGER NOT NULL,
            first_seen_thread_id INTEGER NOT NULL,
            last_updated_post_id INTEGER NOT NULL,
            last_updated_thread_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(term_normalized)
        )
        """,
        "CREATE INDEX idx_glossary_term ON glossary_entry(term_normalized)",
        "CREATE INDEX idx_glossary_status ON glossary_entry(status)",
        "CREATE INDEX idx_glossary_updated ON glossary_entry(updated_at)",
        # Glossary tags
        """
        CREATE TABLE glossary_tag (
            entry_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (entry_id, tag),
            FOREIGN KEY (entry_id) REFERENCES glossary_entry(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX idx_tag ON glossary_tag(tag)",
        # Revision history
        """
        CREATE TABLE revision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            source_post_id INTEGER,
            FOREIGN KEY (entry_id) REFERENCES glossary_entry(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX idx_revision_entry ON revision(entry_id, changed_at)",
        # Snapshot tables
        """
        CREATE TABLE snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_type TEXT NOT NULL
                CHECK (snapshot_type IN ('checkpoint', 'curator_fork', 'manual')),
            created_at TEXT NOT NULL,
            last_post_id INTEGER NOT NULL,
            last_thread_id INTEGER NOT NULL,
            thread_position INTEGER NOT NULL,
            glossary_entry_count INTEGER NOT NULL,
            context_token_count INTEGER,
            metadata TEXT
        )
        """,
        "CREATE INDEX idx_snapshot_created ON snapshot(created_at)",
        "CREATE INDEX idx_snapshot_thread ON snapshot(last_thread_id)",
        "CREATE INDEX idx_snapshot_type ON snapshot(snapshot_type)",
        """
        CREATE TABLE snapshot_context (
            snapshot_id INTEGER PRIMARY KEY,
            system_prompt TEXT NOT NULL,
            cumulative_summary TEXT,
            thread_summaries TEXT NOT NULL,
            conversation_history TEXT NOT NULL,
            current_thread_id INTEGER,
            FOREIGN KEY (snapshot_id) REFERENCES snapshot(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE snapshot_entry (
            snapshot_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL,
            definition_at_snapshot TEXT NOT NULL,
            status_at_snapshot TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, entry_id),
            FOREIGN KEY (snapshot_id) REFERENCES snapshot(id) ON DELETE CASCADE,
            FOREIGN KEY (entry_id) REFERENCES glossary_entry(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX idx_snapshot_entry_entry ON snapshot_entry(entry_id)",
        # Run state (singleton)
        """
        CREATE TABLE run_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_post_id INTEGER,
            last_thread_id INTEGER,
            current_snapshot_id INTEGER,
            run_started_at TEXT,
            run_updated_at TEXT,
            total_posts_processed INTEGER DEFAULT 0,
            total_entries_created INTEGER DEFAULT 0,
            total_entries_updated INTEGER DEFAULT 0
        )
        """,
        "INSERT INTO run_state (id) VALUES (1)",
        # Thread state
        """
        CREATE TABLE thread_state (
            thread_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'in_progress', 'completed')),
            summary TEXT,
            posts_processed INTEGER DEFAULT 0,
            entries_created INTEGER DEFAULT 0,
            entries_updated INTEGER DEFAULT 0,
            started_at TEXT,
            completed_at TEXT
        )
        """,
        "CREATE INDEX idx_thread_status ON thread_state(status)",
    ],
)

# Migration 002: FTS5 for full-text search
MIGRATION_002_FTS = Migration(
    version=2,
    name="add_fts5",
    statements=[
        # FTS5 virtual table
        """
        CREATE VIRTUAL TABLE glossary_fts USING fts5(
            term,
            definition,
            content='glossary_entry',
            content_rowid='id'
        )
        """,
        # Sync triggers
        """
        CREATE TRIGGER glossary_fts_insert AFTER INSERT ON glossary_entry BEGIN
            INSERT INTO glossary_fts(rowid, term, definition)
            VALUES (NEW.id, NEW.term, NEW.definition);
        END
        """,
        """
        CREATE TRIGGER glossary_fts_update AFTER UPDATE ON glossary_entry BEGIN
            INSERT INTO glossary_fts(glossary_fts, rowid, term, definition)
            VALUES ('delete', OLD.id, OLD.term, OLD.definition);
            INSERT INTO glossary_fts(rowid, term, definition)
            VALUES (NEW.id, NEW.term, NEW.definition);
        END
        """,
        """
        CREATE TRIGGER glossary_fts_delete AFTER DELETE ON glossary_entry BEGIN
            INSERT INTO glossary_fts(glossary_fts, rowid, term, definition)
            VALUES ('delete', OLD.id, OLD.term, OLD.definition);
        END
        """,
    ],
)

# Add snapshot foreign key index (missed in initial)
MIGRATION_003_SNAPSHOT_FK = Migration(
    version=3,
    name="add_revision_snapshot_index",
    statements=[
        "CREATE INDEX idx_revision_snapshot ON revision(snapshot_id)",
    ],
)

# All migrations in order
ALL_MIGRATIONS: list[Migration] = [
    MIGRATION_001_INITIAL,
    MIGRATION_002_FTS,
    MIGRATION_003_SNAPSHOT_FK,
]


def get_all_migrations() -> list[Migration]:
    """Return all migrations in version order."""
    return ALL_MIGRATIONS
