"""Tests for SnapshotTools and dispatcher integration."""

import tempfile
from pathlib import Path

import pytest

from terrarium_annotator.context.annotation import AnnotationContext
from terrarium_annotator.context.compactor import CompactionState
from terrarium_annotator.storage import GlossaryStore, SnapshotStore
from terrarium_annotator.tools.snapshot_tools import SnapshotTools, SummonState


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
def snapshots(temp_db: Path) -> SnapshotStore:
    """Create a SnapshotStore."""
    return SnapshotStore(temp_db)


@pytest.fixture
def context() -> AnnotationContext:
    """Create an AnnotationContext for testing."""
    ctx = AnnotationContext(
        system_prompt="You are Terra-annotator.",
        max_turns=12,
    )
    ctx.record_turn("user", "Process this scene.")
    ctx.record_turn("assistant", "I found a new term.")
    return ctx


@pytest.fixture
def compaction_state() -> CompactionState:
    """Create a CompactionState for testing."""
    return CompactionState()


@pytest.fixture
def tools(snapshots: SnapshotStore, glossary: GlossaryStore) -> SnapshotTools:
    """Create SnapshotTools for testing."""
    return SnapshotTools(snapshots, glossary)


class TestSnapshotToolsListSnapshots:
    """Test list_snapshots tool."""

    def test_list_empty(self, tools: SnapshotTools):
        """Should return empty list XML when no snapshots."""
        result = tools.list_snapshots()
        assert '<snapshots count="0">' in result
        assert "</snapshots>" in result

    def test_list_with_snapshots(
        self,
        tools: SnapshotTools,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should list snapshots with details."""
        # Create some snapshots
        snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        snapshots.create(
            snapshot_type="manual",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        result = tools.list_snapshots()
        assert '<snapshots count="2">' in result
        assert 'type="checkpoint"' in result
        assert 'type="manual"' in result

    def test_list_with_type_filter(
        self,
        tools: SnapshotTools,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should filter by type."""
        snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        snapshots.create(
            snapshot_type="manual",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        result = tools.list_snapshots(snapshot_type="checkpoint")
        assert '<snapshots count="1">' in result
        assert 'type="checkpoint"' in result
        assert 'type="manual"' not in result


class TestSnapshotToolsSummon:
    """Test summon workflow."""

    def test_summon_not_found(self, tools: SnapshotTools):
        """Should return error for non-existent snapshot."""
        result = tools.summon_snapshot(999)
        assert "<error" in result
        assert 'code="NOT_FOUND"' in result

    def test_summon_success(
        self,
        tools: SnapshotTools,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should summon snapshot successfully."""
        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        result = tools.summon_snapshot(snapshot_id)
        assert "<summon_active" in result
        assert f'snapshot_id="{snapshot_id}"' in result
        assert 'type="checkpoint"' in result
        assert '<entries count="2">' in result
        assert tools.has_active_summon is True

    def test_summon_already_active(
        self,
        tools: SnapshotTools,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should error when trying to summon while already active."""
        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        tools.summon_snapshot(snapshot_id)
        result = tools.summon_snapshot(snapshot_id)
        assert "<error" in result
        assert 'code="SUMMON_ACTIVE"' in result

    def test_summon_continue(
        self,
        tools: SnapshotTools,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should continue summon conversation."""
        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        tools.summon_snapshot(snapshot_id)

        result = tools.summon_continue("What entries exist?")
        assert "<success" in result
        assert "Continuing summon conversation" in result

        # Check conversation recorded
        summon = tools.get_active_summon()
        assert summon is not None
        assert len(summon.conversation) == 1
        assert summon.conversation[0]["content"] == "What entries exist?"

    def test_summon_continue_no_active(self, tools: SnapshotTools):
        """Should error when continuing with no active summon."""
        result = tools.summon_continue("Hello?")
        assert "<error" in result
        assert 'code="NO_SUMMON"' in result

    def test_summon_dismiss(
        self,
        tools: SnapshotTools,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should dismiss active summon."""
        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )
        tools.summon_snapshot(snapshot_id)
        tools.summon_continue("Message 1")
        tools.summon_continue("Message 2")

        result = tools.summon_dismiss()
        assert "<success" in result
        assert f"snapshot {snapshot_id}" in result
        assert "2 turns" in result
        assert tools.has_active_summon is False

    def test_summon_dismiss_no_active(self, tools: SnapshotTools):
        """Should error when dismissing with no active summon."""
        result = tools.summon_dismiss()
        assert "<error" in result
        assert 'code="NO_SUMMON"' in result


class TestSummonState:
    """Test SummonState dataclass."""

    def test_summon_state_fields(self, context: AnnotationContext, compaction_state: CompactionState):
        """Should hold summon session data."""
        state = SummonState(
            snapshot_id=42,
            context=context,
            compaction_state=compaction_state,
            entry_states={},
        )
        assert state.snapshot_id == 42
        assert state.context is context
        assert state.conversation == []

        state.conversation.append({"role": "user", "content": "Test"})
        assert len(state.conversation) == 1


class TestDispatcherIntegration:
    """Test dispatcher integration with snapshot tools."""

    def test_blocks_write_during_summon(
        self,
        temp_db: Path,
        context: AnnotationContext,
        compaction_state: CompactionState,
    ):
        """Should block write tools during active summon."""
        from unittest.mock import Mock

        from terrarium_annotator.tools.dispatcher import ToolDispatcher

        glossary = GlossaryStore(temp_db)
        glossary.create(
            term="Test",
            definition="Test entry",
            tags=[],
            post_id=1,
            thread_id=1,
        )

        snapshots = SnapshotStore(temp_db)
        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=100,
            last_thread_id=1,
            thread_position=0,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
        )

        corpus = Mock()
        revisions = Mock()

        dispatcher = ToolDispatcher(
            glossary=glossary,
            corpus=corpus,
            revisions=revisions,
            snapshots=snapshots,
        )

        # Summon the snapshot
        dispatcher.dispatch(
            {
                "id": "call1",
                "function": {
                    "name": "summon_snapshot",
                    "arguments": f'{{"snapshot_id": {snapshot_id}}}',
                },
            },
            current_post_id=100,
            current_thread_id=1,
        )

        assert dispatcher.has_active_summon is True

        # Try to create - should be blocked
        result = dispatcher.dispatch(
            {
                "id": "call2",
                "function": {
                    "name": "glossary_create",
                    "arguments": '{"term": "New", "definition": "Def", "tags": []}',
                },
            },
            current_post_id=100,
            current_thread_id=1,
        )

        assert result.success is False
        assert "SUMMON_READ_ONLY" in result.result

        # Dismiss
        dispatcher.dispatch(
            {
                "id": "call3",
                "function": {
                    "name": "summon_dismiss",
                    "arguments": "{}",
                },
            },
            current_post_id=100,
            current_thread_id=1,
        )

        assert dispatcher.has_active_summon is False

        glossary.close()
        snapshots.close()

    def test_includes_snapshot_tools_when_available(self, temp_db: Path):
        """Should include snapshot tool schemas when snapshots provided."""
        from unittest.mock import Mock

        from terrarium_annotator.tools.dispatcher import ToolDispatcher

        glossary = GlossaryStore(temp_db)
        snapshots = SnapshotStore(temp_db)
        corpus = Mock()
        revisions = Mock()

        dispatcher = ToolDispatcher(
            glossary=glossary,
            corpus=corpus,
            revisions=revisions,
            snapshots=snapshots,
        )

        schemas = dispatcher.get_tool_definitions()
        tool_names = {s["function"]["name"] for s in schemas}

        assert "list_snapshots" in tool_names
        assert "summon_snapshot" in tool_names
        assert "summon_continue" in tool_names
        assert "summon_dismiss" in tool_names

        glossary.close()
        snapshots.close()

    def test_excludes_snapshot_tools_when_not_available(self, temp_db: Path):
        """Should exclude snapshot tool schemas when snapshots not provided."""
        from unittest.mock import Mock

        from terrarium_annotator.tools.dispatcher import ToolDispatcher

        glossary = GlossaryStore(temp_db)
        corpus = Mock()
        revisions = Mock()

        dispatcher = ToolDispatcher(
            glossary=glossary,
            corpus=corpus,
            revisions=revisions,
            snapshots=None,
        )

        schemas = dispatcher.get_tool_definitions()
        tool_names = {s["function"]["name"] for s in schemas}

        assert "list_snapshots" not in tool_names
        assert "summon_snapshot" not in tool_names

        glossary.close()
