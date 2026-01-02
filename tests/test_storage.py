"""Tests for the SQLite storage layer."""

import tempfile
from pathlib import Path

import pytest

from terrarium_annotator.storage import (
    Database,
    DuplicateTermError,
    GlossaryEntry,
    GlossaryStore,
    ProgressTracker,
    Revision,
    RevisionHistory,
    RunState,
    get_all_migrations,
    normalize_term,
)


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


class TestDatabase:
    def test_creates_db_file(self, temp_db: Path):
        temp_db.unlink()  # Remove the empty file
        db = Database(temp_db)
        _ = db.conn  # Trigger connection
        assert temp_db.exists()
        db.close()

    def test_runs_migrations(self, temp_db: Path):
        db = Database(temp_db)
        migrations = get_all_migrations()
        applied = db.run_migrations(migrations)
        assert applied == len(migrations)

        # Running again should apply 0
        applied_again = db.run_migrations(migrations)
        assert applied_again == 0
        db.close()

    def test_schema_version_tracking(self, temp_db: Path):
        db = Database(temp_db)
        assert db.get_schema_version() == 0

        db.run_migrations(get_all_migrations())
        assert db.get_schema_version() == len(get_all_migrations())
        db.close()


class TestNormalizeTerm:
    def test_lowercase(self):
        assert normalize_term("Soma") == "soma"

    def test_strips_whitespace(self):
        assert normalize_term("  Soma  ") == "soma"

    def test_unicode_normalization(self):
        # NFD form should be consistent
        assert normalize_term("café") == normalize_term("café")


class TestGlossaryStore:
    def test_create_and_get(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="Soma",
            definition="The questmaster who runs Banished Quest.",
            tags=["character", "qm"],
            post_id=1,
            thread_id=1,
        )
        assert entry_id > 0

        entry = store.get(entry_id)
        assert entry is not None
        assert entry.term == "Soma"
        assert entry.definition == "The questmaster who runs Banished Quest."
        assert entry.tags == ["character", "qm"]
        assert entry.status == "tentative"
        store.close()

    def test_duplicate_term_raises(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        store.create(
            term="Soma",
            definition="First definition",
            tags=[],
            post_id=1,
            thread_id=1,
        )

        with pytest.raises(DuplicateTermError) as exc_info:
            store.create(
                term="soma",  # Different case, same normalized
                definition="Second definition",
                tags=[],
                post_id=2,
                thread_id=1,
            )
        assert exc_info.value.term == "soma"
        store.close()

    def test_update_entry(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="Soma",
            definition="Original definition",
            tags=["character"],
            post_id=1,
            thread_id=1,
        )

        updated = store.update(
            entry_id,
            definition="Updated definition",
            status="confirmed",
            post_id=10,
            thread_id=2,
        )
        assert updated is True

        entry = store.get(entry_id)
        assert entry.definition == "Updated definition"
        assert entry.status == "confirmed"
        assert entry.last_updated_post_id == 10
        assert entry.last_updated_thread_id == 2
        store.close()

    def test_update_tags(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="Soma",
            definition="Def",
            tags=["old_tag"],
            post_id=1,
            thread_id=1,
        )

        store.update(entry_id, tags=["new_tag", "another"], post_id=2, thread_id=1)

        entry = store.get(entry_id)
        assert entry.tags == ["another", "new_tag"]  # Sorted
        store.close()

    def test_delete_entry(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="Soma",
            definition="Def",
            tags=[],
            post_id=1,
            thread_id=1,
        )

        deleted = store.delete(entry_id, "Test deletion")
        assert deleted is True
        assert store.get(entry_id) is None
        store.close()

    def test_delete_nonexistent(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        deleted = store.delete(9999, "No such entry")
        assert deleted is False
        store.close()

    def test_search_fts(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        store.create(
            term="Soma",
            definition="The questmaster who created Banished Quest.",
            tags=["character"],
            post_id=1,
            thread_id=1,
        )
        store.create(
            term="Mana",
            definition="Magical energy used by characters.",
            tags=["concept"],
            post_id=2,
            thread_id=1,
        )

        # Search in term
        results = store.search("Soma")
        assert len(results) == 1
        assert results[0].term == "Soma"

        # Search in definition
        results = store.search("magical")
        assert len(results) == 1
        assert results[0].term == "Mana"
        store.close()

    def test_search_with_status_filter(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        store.create(
            term="Confirmed Entry",
            definition="This is confirmed.",
            tags=[],
            post_id=1,
            thread_id=1,
            status="confirmed",
        )
        store.create(
            term="Tentative Entry",
            definition="This is tentative.",
            tags=[],
            post_id=2,
            thread_id=1,
            status="tentative",
        )

        results = store.search("entry", status="confirmed")
        assert len(results) == 1
        assert results[0].status == "confirmed"
        store.close()

    def test_search_with_tag_filter(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        store.create(
            term="Character A",
            definition="A character.",
            tags=["character", "npc"],
            post_id=1,
            thread_id=1,
        )
        store.create(
            term="Location B",
            definition="A location.",
            tags=["location"],
            post_id=2,
            thread_id=1,
        )

        results = store.search("character OR location", tags=["character"])
        assert len(results) == 1
        assert results[0].term == "Character A"
        store.close()

    def test_all_entries(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        store.create(term="Beta", definition="B", tags=[], post_id=1, thread_id=1)
        store.create(term="Alpha", definition="A", tags=[], post_id=2, thread_id=1)

        entries = list(store.all_entries())
        assert len(entries) == 2
        assert entries[0].term == "Alpha"  # Sorted by normalized term
        assert entries[1].term == "Beta"
        store.close()

    def test_count(self, temp_db: Path):
        store = GlossaryStore(temp_db)
        assert store.count() == 0

        store.create(term="A", definition="A", tags=[], post_id=1, thread_id=1)
        store.create(term="B", definition="B", tags=[], post_id=2, thread_id=1)

        assert store.count() == 2
        store.close()


class TestRevisionHistory:
    def test_log_and_get_history(self, temp_db: Path):
        # Need to create a glossary entry first (FK constraint)
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="Test", definition="Test def", tags=[], post_id=1, thread_id=1
        )

        history = RevisionHistory(temp_db)
        rev_id = history.log_change(
            entry_id=entry_id,
            field_name="definition",
            old_value="Old def",
            new_value="New def",
            source_post_id=42,
        )
        assert rev_id > 0

        revisions = history.get_history(entry_id)
        assert len(revisions) == 1
        assert revisions[0].field_name == "definition"
        assert revisions[0].old_value == "Old def"
        assert revisions[0].new_value == "New def"
        assert revisions[0].source_post_id == 42
        history.close()
        store.close()

    def test_log_creation(self, temp_db: Path):
        # Create entry first
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="Soma", definition="The QM", tags=["character"],
            post_id=1, thread_id=1
        )

        history = RevisionHistory(temp_db)
        history.log_creation(
            entry_id=entry_id,
            term="Soma",
            definition="The QM",
            tags=["character"],
            status="tentative",
            source_post_id=1,
        )

        revisions = history.get_history(entry_id)
        assert len(revisions) == 4  # term, definition, tags, status

        field_names = {r.field_name for r in revisions}
        assert field_names == {"term", "definition", "tags", "status"}
        history.close()
        store.close()

    def test_log_deletion(self, temp_db: Path):
        # Create entry first
        store = GlossaryStore(temp_db)
        entry_id = store.create(
            term="ToDelete", definition="Will be deleted", tags=[],
            post_id=1, thread_id=1
        )

        history = RevisionHistory(temp_db)
        rev_id = history.log_deletion(entry_id=entry_id, reason="Duplicate entry")

        revisions = history.get_history(entry_id)
        assert len(revisions) == 1
        assert revisions[0].field_name == "deleted"
        assert revisions[0].new_value == "Duplicate entry"
        history.close()
        store.close()


class TestProgressTracker:
    def test_initial_state(self, temp_db: Path):
        tracker = ProgressTracker(temp_db)
        state = tracker.get_state()

        assert state.last_post_id is None
        assert state.last_thread_id is None
        assert state.total_posts_processed == 0
        tracker.close()

    def test_update_state(self, temp_db: Path):
        tracker = ProgressTracker(temp_db)

        tracker.update(
            last_post_id=100,
            last_thread_id=5,
            posts_processed_delta=10,
            entries_created_delta=3,
        )

        state = tracker.get_state()
        assert state.last_post_id == 100
        assert state.last_thread_id == 5
        assert state.total_posts_processed == 10
        assert state.total_entries_created == 3
        tracker.close()

    def test_delta_accumulation(self, temp_db: Path):
        tracker = ProgressTracker(temp_db)

        tracker.update(posts_processed_delta=5)
        tracker.update(posts_processed_delta=3)

        state = tracker.get_state()
        assert state.total_posts_processed == 8
        tracker.close()

    def test_start_run(self, temp_db: Path):
        tracker = ProgressTracker(temp_db)
        tracker.start_run()

        state = tracker.get_state()
        assert state.run_started_at is not None
        tracker.close()

    def test_thread_state(self, temp_db: Path):
        tracker = ProgressTracker(temp_db)

        # Initially no thread state
        assert tracker.get_thread_state(1) is None

        # Create thread state
        tracker.update_thread_state(1, status="in_progress")
        ts = tracker.get_thread_state(1)
        assert ts is not None
        assert ts.status == "in_progress"
        assert ts.started_at is not None

        # Update thread state
        tracker.update_thread_state(1, status="completed", posts_processed_delta=10)
        ts = tracker.get_thread_state(1)
        assert ts.status == "completed"
        assert ts.completed_at is not None
        assert ts.posts_processed == 10
        tracker.close()

    def test_get_completed_threads(self, temp_db: Path):
        tracker = ProgressTracker(temp_db)

        tracker.update_thread_state(1, status="completed")
        tracker.update_thread_state(2, status="in_progress")
        tracker.update_thread_state(3, status="completed")

        completed = tracker.get_completed_threads()
        assert len(completed) == 2
        assert {t.thread_id for t in completed} == {1, 3}
        tracker.close()
