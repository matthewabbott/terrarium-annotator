"""Tests for SnapshotStore."""

import tempfile
from pathlib import Path

import pytest

from terrarium_annotator.context.annotation import AnnotationContext
from terrarium_annotator.context.compactor import CompactionState
from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.storage import (
    GlossaryStore,
    Snapshot,
    SnapshotContext,
    SnapshotEntry,
    SnapshotStore,
)


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def glossary(temp_db: Path) -> GlossaryStore:
    """Create a GlossaryStore with some entries."""
    store = GlossaryStore(temp_db)
    store.create(
        term="Soma",
        definition="The questmaster.",
        tags=["character"],
        post_id=100,
        thread_id=1,
    )
    store.create(
        term="Dawn",
        definition="A character in the story.",
        tags=["character"],
        post_id=150,
        thread_id=1,
    )
    return store


@pytest.fixture
def context() -> AnnotationContext:
    """Create an AnnotationContext for testing."""
    ctx = AnnotationContext(
        system_prompt="You are Terra-annotator.",
    )
    ctx.record_turn("user", "Process this scene.")
    ctx.record_turn("assistant", "I found a new term.")
    return ctx


@pytest.fixture
def compaction_state() -> CompactionState:
    """Create a CompactionState for testing."""
    state = CompactionState()
    state.cumulative_summary = "Previous threads covered..."
    state.thread_summaries = [
        ThreadSummary(
            thread_id=1,
            position=0,
            summary_text="Thread 1 summary",
            entries_created=[1],
            entries_updated=[],
        )
    ]
    state.completed_thread_ids = [1, 2]
    return state


class TestSnapshotDataclasses:
    """Test snapshot dataclasses."""

    def test_snapshot_fields(self):
        snapshot = Snapshot(
            id=1,
            snapshot_type="checkpoint",
            created_at="2024-01-01T00:00:00Z",
            last_post_id=100,
            last_thread_id=5,
            thread_position=3,
            glossary_entry_count=10,
            context_token_count=5000,
            metadata={"key": "value"},
        )
        assert snapshot.id == 1
        assert snapshot.snapshot_type == "checkpoint"
        assert snapshot.glossary_entry_count == 10
        assert snapshot.metadata == {"key": "value"}

    def test_snapshot_context_fields(self):
        ctx = SnapshotContext(
            snapshot_id=1,
            system_prompt="You are an annotator.",
            cumulative_summary="Summary",
            thread_summaries=[{"thread_id": 1}],
            conversation_history=[{"role": "user", "content": "Hi"}],
            current_thread_id=5,
            completed_thread_ids=[1, 2, 3],
        )
        assert ctx.snapshot_id == 1
        assert ctx.system_prompt == "You are an annotator."
        assert len(ctx.thread_summaries) == 1
        assert len(ctx.conversation_history) == 1
        assert ctx.completed_thread_ids == [1, 2, 3]

    def test_snapshot_entry_fields(self):
        entry = SnapshotEntry(
            snapshot_id=1,
            entry_id=42,
            definition_at_snapshot="Original definition",
            status_at_snapshot="tentative",
        )
        assert entry.snapshot_id == 1
        assert entry.entry_id == 42
        assert entry.status_at_snapshot == "tentative"


class TestSnapshotStore:
    """Test SnapshotStore operations."""

    def test_create_snapshot(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should create a snapshot with context and entries."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
            token_count=3000,
            metadata={"run_id": 123},
        )

        assert snapshot_id > 0
        store.close()

    def test_create_captures_all_entries(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should capture all glossary entries in snapshot."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        entries = store.get_entries(snapshot_id)
        assert len(entries) == 2  # Soma and Dawn

        # Check entry contents
        terms = {e.definition_at_snapshot for e in entries}
        assert "The questmaster." in terms
        assert "A character in the story." in terms
        store.close()

    def test_get_snapshot(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should retrieve snapshot by ID."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="manual",
            last_post_id=300,
            last_thread_id=3,
            thread_position=2,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
            token_count=4000,
        )

        snapshot = store.get(snapshot_id)
        assert snapshot is not None
        assert snapshot.id == snapshot_id
        assert snapshot.snapshot_type == "manual"
        assert snapshot.last_post_id == 300
        assert snapshot.last_thread_id == 3
        assert snapshot.thread_position == 2
        assert snapshot.glossary_entry_count == 2
        assert snapshot.context_token_count == 4000
        store.close()

    def test_get_snapshot_not_found(self, temp_db: Path):
        """Should return None for non-existent snapshot."""
        store = SnapshotStore(temp_db)
        assert store.get(999) is None
        store.close()

    def test_get_context(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should retrieve context for snapshot."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        ctx = store.get_context(snapshot_id)
        assert ctx is not None
        assert ctx.snapshot_id == snapshot_id
        assert ctx.system_prompt == "You are Terra-annotator."
        assert ctx.cumulative_summary == "Previous threads covered..."
        assert len(ctx.thread_summaries) == 1
        assert len(ctx.conversation_history) == 2
        store.close()

    def test_get_context_not_found(self, temp_db: Path):
        """Should return None for non-existent context."""
        store = SnapshotStore(temp_db)
        assert store.get_context(999) is None
        store.close()

    def test_get_entries(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should retrieve all entries for snapshot."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        entries = store.get_entries(snapshot_id)
        assert len(entries) == 2
        assert all(e.snapshot_id == snapshot_id for e in entries)
        assert all(e.status_at_snapshot == "tentative" for e in entries)
        store.close()

    def test_list_recent(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should list recent snapshots."""
        store = SnapshotStore(temp_db)

        # Create multiple snapshots
        for i in range(5):
            store.create(
                snapshot_type="checkpoint" if i % 2 == 0 else "manual",
                last_post_id=100 + i,
                last_thread_id=i,
                thread_position=i,
                context=context,
                compaction_state=compaction_state,
                glossary=glossary,
            )

        # List all
        all_snapshots = store.list_recent(limit=10)
        assert len(all_snapshots) == 5

        # List limited
        limited = store.list_recent(limit=3)
        assert len(limited) == 3

        # List by type
        checkpoints = store.list_recent(snapshot_type="checkpoint")
        assert len(checkpoints) == 3
        assert all(s.snapshot_type == "checkpoint" for s in checkpoints)

        manual = store.list_recent(snapshot_type="manual")
        assert len(manual) == 2
        store.close()

    def test_list_by_thread(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should list snapshots for a specific thread."""
        store = SnapshotStore(temp_db)

        # Create snapshots for different threads
        store.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        store.create(
            snapshot_type="checkpoint",
            last_post_id=150,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        thread1_snapshots = store.list_by_thread(1)
        assert len(thread1_snapshots) == 2
        assert all(s.last_thread_id == 1 for s in thread1_snapshots)

        thread2_snapshots = store.list_by_thread(2)
        assert len(thread2_snapshots) == 1
        store.close()

    def test_restore_context(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should restore context and compaction state from snapshot."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        restored_context, restored_compaction = store.restore_context(snapshot_id)

        # Check context
        assert restored_context.system_prompt == "You are Terra-annotator."
        assert len(restored_context.conversation_history) == 2
        assert restored_context.conversation_history[0]["role"] == "user"

        # Check compaction state
        assert restored_compaction.cumulative_summary == "Previous threads covered..."
        assert len(restored_compaction.thread_summaries) == 1
        assert restored_compaction.thread_summaries[0].thread_id == 1
        store.close()

    def test_restore_entries_view(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should return entries as dict keyed by entry_id."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        entries_view = store.restore_entries_view(snapshot_id)
        assert len(entries_view) == 2

        # Find entry by ID (IDs are 1 and 2)
        assert 1 in entries_view
        assert 2 in entries_view
        assert entries_view[1].definition_at_snapshot == "The questmaster."
        store.close()

    def test_delete_snapshot(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should delete snapshot and associated data."""
        store = SnapshotStore(temp_db)

        snapshot_id = store.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        assert store.get(snapshot_id) is not None
        assert store.delete(snapshot_id) is True
        assert store.get(snapshot_id) is None

        # Context and entries should also be deleted (CASCADE)
        assert store.get_context(snapshot_id) is None
        assert store.get_entries(snapshot_id) == []
        store.close()

    def test_delete_nonexistent(self, temp_db: Path):
        """Should return False when deleting non-existent snapshot."""
        store = SnapshotStore(temp_db)
        assert store.delete(999) is False
        store.close()

    def test_count(
        self,
        temp_db: Path,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should return snapshot count."""
        store = SnapshotStore(temp_db)
        assert store.count() == 0

        store.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        assert store.count() == 1

        store.create(
            snapshot_type="manual",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        assert store.count() == 2
        store.close()


class TestSerializationRoundtrip:
    """Test serialization/deserialization roundtrips."""

    def test_annotation_context_roundtrip(self):
        """AnnotationContext should survive to_dict/from_dict."""
        original = AnnotationContext(
            system_prompt="Test prompt",
        )
        original.record_turn("user", "Hello")
        original.record_turn("assistant", "Hi there")

        data = original.to_dict()
        restored = AnnotationContext.from_dict(data)

        assert restored.system_prompt == original.system_prompt
        assert len(restored.conversation_history) == 2
        assert restored.conversation_history[0]["content"] == "Hello"

    def test_compaction_state_roundtrip(self):
        """CompactionState should survive to_dict/from_dict."""
        original = CompactionState()
        original.cumulative_summary = "Summary text"
        original.thread_summaries = [
            ThreadSummary(
                thread_id=5,
                position=0,
                summary_text="Thread 5 summary",
                entries_created=[10, 11],
                entries_updated=[12],
            )
        ]
        original.completed_thread_ids = [1, 2, 3]

        data = original.to_dict()
        restored = CompactionState.from_dict(data)

        assert restored.cumulative_summary == original.cumulative_summary
        assert len(restored.thread_summaries) == 1
        assert restored.thread_summaries[0].thread_id == 5
        assert restored.thread_summaries[0].entries_created == [10, 11]
        assert restored.completed_thread_ids == [1, 2, 3]

    def test_thread_summary_roundtrip(self):
        """ThreadSummary should survive to_dict/from_dict."""
        original = ThreadSummary(
            thread_id=42,
            position=7,
            summary_text="A thread about dragons",
            entries_created=[1, 2, 3],
            entries_updated=[4],
        )

        data = original.to_dict()
        restored = ThreadSummary.from_dict(data)

        assert restored.thread_id == original.thread_id
        assert restored.position == original.position
        assert restored.summary_text == original.summary_text
        assert restored.entries_created == original.entries_created
        assert restored.entries_updated == original.entries_updated
