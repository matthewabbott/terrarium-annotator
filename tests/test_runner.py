"""Tests for the annotation runner."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from terrarium_annotator.runner import (
    AnnotationRunner,
    RunnerConfig,
    RunResult,
    ToolStats,
)


class TestRunnerConfig:
    """Tests for RunnerConfig dataclass."""

    def test_config_required_fields(self, tmp_path):
        """Config requires corpus and annotator db paths."""
        config = RunnerConfig(
            corpus_db_path=tmp_path / "corpus.db",
            annotator_db_path=tmp_path / "annotator.db",
        )
        assert config.corpus_db_path == tmp_path / "corpus.db"
        assert config.annotator_db_path == tmp_path / "annotator.db"

    def test_config_defaults(self, tmp_path):
        """Config has sensible defaults."""
        config = RunnerConfig(
            corpus_db_path=tmp_path / "corpus.db",
            annotator_db_path=tmp_path / "annotator.db",
        )
        assert config.agent_url == "http://localhost:8080"
        assert config.temperature == 0.4
        assert config.max_tokens == 1024
        assert config.timeout == 120
        assert config.resume is True
        assert config.max_tool_rounds == 10

    def test_config_custom_values(self, tmp_path):
        """Config accepts custom values."""
        config = RunnerConfig(
            corpus_db_path=tmp_path / "corpus.db",
            annotator_db_path=tmp_path / "annotator.db",
            agent_url="http://custom:9000",
            temperature=0.7,
            max_tokens=2048,
            timeout=60,
            resume=False,
            max_tool_rounds=5,
        )
        assert config.agent_url == "http://custom:9000"
        assert config.temperature == 0.7
        assert config.max_tokens == 2048
        assert config.timeout == 60
        assert config.resume is False
        assert config.max_tool_rounds == 5


class TestToolStats:
    """Tests for ToolStats dataclass."""

    def test_stats_defaults(self):
        """ToolStats has zero defaults."""
        stats = ToolStats()
        assert stats.created == 0
        assert stats.updated == 0
        assert stats.deleted == 0
        assert stats.tool_calls == 0
        assert stats.rounds == 0

    def test_stats_mutable(self):
        """ToolStats fields are mutable."""
        stats = ToolStats()
        stats.created = 5
        stats.updated = 3
        stats.tool_calls = 10
        assert stats.created == 5
        assert stats.updated == 3
        assert stats.tool_calls == 10


class TestRunResult:
    """Tests for RunResult dataclass."""

    def test_result_fields(self):
        """RunResult contains expected fields."""
        result = RunResult(
            scenes_processed=10,
            posts_processed=50,
            entries_created=15,
            entries_updated=5,
            entries_deleted=2,
            tool_calls_total=30,
            run_duration_seconds=120.5,
        )
        assert result.scenes_processed == 10
        assert result.posts_processed == 50
        assert result.entries_created == 15
        assert result.entries_updated == 5
        assert result.entries_deleted == 2
        assert result.tool_calls_total == 30
        assert result.run_duration_seconds == 120.5


class TestAnnotationRunner:
    """Tests for AnnotationRunner class."""

    @pytest.fixture
    def mock_components(self, tmp_path):
        """Create mocked component dependencies."""
        with patch("terrarium_annotator.runner.GlossaryStore") as mock_glossary, \
             patch("terrarium_annotator.runner.RevisionHistory") as mock_revisions, \
             patch("terrarium_annotator.runner.ProgressTracker") as mock_progress, \
             patch("terrarium_annotator.runner.CorpusReader") as mock_corpus, \
             patch("terrarium_annotator.runner.SceneBatcher") as mock_batcher, \
             patch("terrarium_annotator.runner.AnnotationContext") as mock_context, \
             patch("terrarium_annotator.runner.ToolDispatcher") as mock_dispatcher, \
             patch("terrarium_annotator.runner.AgentClient") as mock_agent:

            # Configure mock returns
            mock_progress.return_value.get_state.return_value = Mock(
                last_post_id=None,
                last_thread_id=None,
            )
            mock_batcher.return_value.iter_scenes.return_value = iter([])
            mock_context.return_value.build_messages.return_value = []
            mock_dispatcher.return_value.get_tool_definitions.return_value = []

            config = RunnerConfig(
                corpus_db_path=tmp_path / "corpus.db",
                annotator_db_path=tmp_path / "annotator.db",
            )

            yield {
                "config": config,
                "glossary": mock_glossary,
                "revisions": mock_revisions,
                "progress": mock_progress,
                "corpus": mock_corpus,
                "batcher": mock_batcher,
                "context": mock_context,
                "dispatcher": mock_dispatcher,
                "agent": mock_agent,
            }

    def test_init_creates_components(self, mock_components):
        """Runner initializes all required components."""
        config = mock_components["config"]
        runner = AnnotationRunner(config)

        # Verify storage components created
        mock_components["glossary"].assert_called_once_with(config.annotator_db_path)
        mock_components["revisions"].assert_called_once_with(config.annotator_db_path)
        mock_components["progress"].assert_called_once_with(config.annotator_db_path)

        # Verify corpus components created
        mock_components["corpus"].assert_called_once_with(config.corpus_db_path)
        mock_components["batcher"].assert_called_once()

        # Verify agent client created
        mock_components["agent"].assert_called_once()

    def test_run_empty_corpus(self, mock_components):
        """Run completes with no scenes to process."""
        runner = AnnotationRunner(mock_components["config"])
        result = runner.run()

        assert result.scenes_processed == 0
        assert result.posts_processed == 0
        assert result.entries_created == 0

    def test_run_calls_start_run(self, mock_components):
        """Run calls progress.start_run()."""
        runner = AnnotationRunner(mock_components["config"])
        runner.run()

        runner.progress.start_run.assert_called_once()

    def test_run_respects_limit(self, mock_components):
        """Run stops after processing limit scenes."""
        # Create mock scenes
        mock_scene = Mock()
        mock_scene.thread_id = 1
        mock_scene.first_post_id = 1
        mock_scene.last_post_id = 5
        mock_scene.post_count = 5
        mock_scene.posts = []

        # Return 10 scenes
        mock_components["batcher"].return_value.iter_scenes.return_value = iter(
            [mock_scene] * 10
        )

        # Mock agent to return no tool calls
        mock_components["agent"].return_value.chat.return_value = Mock(
            message={"content": "Done", "tool_calls": []},
            inference_duration_seconds=0.0,
        )

        runner = AnnotationRunner(mock_components["config"])
        result = runner.run(limit=3)

        assert result.scenes_processed == 3

    def test_run_resumes_from_checkpoint(self, mock_components):
        """Run starts from last_post_id when resume=True."""
        mock_components["progress"].return_value.get_state.return_value = Mock(
            last_post_id=100,
            last_thread_id=1,
        )

        runner = AnnotationRunner(mock_components["config"])
        runner.run()

        # Verify batcher was called with start_after_post_id
        runner.batcher.iter_scenes.assert_called_once_with(start_after_post_id=100)

    def test_run_ignores_checkpoint_when_no_resume(self, mock_components):
        """Run starts from beginning when resume=False."""
        mock_components["progress"].return_value.get_state.return_value = Mock(
            last_post_id=100,
            last_thread_id=1,
        )
        mock_components["config"].resume = False

        runner = AnnotationRunner(mock_components["config"])
        runner.run()

        # Verify batcher was called with start_after_post_id=None
        runner.batcher.iter_scenes.assert_called_once_with(start_after_post_id=None)


class TestToolLoop:
    """Tests for _run_tool_loop method."""

    @pytest.fixture
    def runner_with_mocks(self, tmp_path):
        """Create runner with mocked dependencies."""
        with patch("terrarium_annotator.runner.GlossaryStore"), \
             patch("terrarium_annotator.runner.RevisionHistory"), \
             patch("terrarium_annotator.runner.ProgressTracker") as mock_progress, \
             patch("terrarium_annotator.runner.CorpusReader"), \
             patch("terrarium_annotator.runner.SceneBatcher"), \
             patch("terrarium_annotator.runner.AnnotationContext") as mock_context, \
             patch("terrarium_annotator.runner.ToolDispatcher") as mock_dispatcher, \
             patch("terrarium_annotator.runner.AgentClient") as mock_agent:

            mock_progress.return_value.get_state.return_value = Mock(
                last_post_id=None,
                last_thread_id=None,
            )
            mock_dispatcher.return_value.get_tool_definitions.return_value = []

            config = RunnerConfig(
                corpus_db_path=tmp_path / "corpus.db",
                annotator_db_path=tmp_path / "annotator.db",
                max_tool_rounds=5,
            )

            runner = AnnotationRunner(config)

            yield {
                "runner": runner,
                "agent": mock_agent.return_value,
                "dispatcher": mock_dispatcher.return_value,
                "context": mock_context.return_value,
            }

    def test_no_tool_calls_exits_immediately(self, runner_with_mocks):
        """Loop exits when agent returns no tool_calls."""
        runner = runner_with_mocks["runner"]
        agent = runner_with_mocks["agent"]

        agent.chat.return_value = Mock(
            message={"content": "All done!", "tool_calls": []},
            inference_duration_seconds=0.0,
        )

        mock_scene = Mock(thread_id=1, last_post_id=10)
        stats = runner._run_tool_loop([], mock_scene, scene_index=0)

        assert stats.rounds == 1
        assert stats.tool_calls == 0
        agent.chat.assert_called_once()

    def test_single_tool_call_round(self, runner_with_mocks):
        """Loop handles single round of tool calls."""
        runner = runner_with_mocks["runner"]
        agent = runner_with_mocks["agent"]
        dispatcher = runner_with_mocks["dispatcher"]

        # First call returns tool call, second returns done
        agent.chat.side_effect = [
            Mock(message={
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "glossary_create", "arguments": "{}"},
                }],
            }, inference_duration_seconds=0.0),
            Mock(message={"content": "Done", "tool_calls": []}, inference_duration_seconds=0.0),
        ]

        dispatcher.dispatch.return_value = Mock(
            tool_name="glossary_create",
            call_id="call_1",
            success=True,
            result="<success/>",
        )

        mock_scene = Mock(thread_id=1, last_post_id=10)
        stats = runner._run_tool_loop([], mock_scene, scene_index=0)

        assert stats.rounds == 2
        assert stats.tool_calls == 1
        assert stats.created == 1

    def test_multiple_tool_calls_same_round(self, runner_with_mocks):
        """Loop dispatches multiple tool calls in one response."""
        runner = runner_with_mocks["runner"]
        agent = runner_with_mocks["agent"]
        dispatcher = runner_with_mocks["dispatcher"]

        # Return 3 tool calls, then done
        agent.chat.side_effect = [
            Mock(message={
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "glossary_search", "arguments": "{}"}},
                    {"id": "call_2", "function": {"name": "glossary_create", "arguments": "{}"}},
                    {"id": "call_3", "function": {"name": "glossary_update", "arguments": "{}"}},
                ],
            }, inference_duration_seconds=0.0),
            Mock(message={"content": "Done", "tool_calls": []}, inference_duration_seconds=0.0),
        ]

        dispatcher.dispatch.side_effect = [
            Mock(tool_name="glossary_search", call_id="call_1", success=True, result="<results/>"),
            Mock(tool_name="glossary_create", call_id="call_2", success=True, result="<success/>"),
            Mock(tool_name="glossary_update", call_id="call_3", success=True, result="<success/>"),
        ]

        mock_scene = Mock(thread_id=1, last_post_id=10)
        stats = runner._run_tool_loop([], mock_scene, scene_index=0)

        assert stats.tool_calls == 3
        assert stats.created == 1
        assert stats.updated == 1

    def test_max_rounds_limit(self, runner_with_mocks):
        """Loop exits after max_tool_rounds even if agent continues."""
        runner = runner_with_mocks["runner"]
        agent = runner_with_mocks["agent"]
        dispatcher = runner_with_mocks["dispatcher"]

        # Always return tool calls (never done)
        agent.chat.return_value = Mock(
            message={
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "glossary_search", "arguments": "{}"}}],
            },
            inference_duration_seconds=0.0,
        )

        dispatcher.dispatch.return_value = Mock(
            tool_name="glossary_search",
            call_id="call_1",
            success=True,
            result="<results/>",
        )

        mock_scene = Mock(thread_id=1, last_post_id=10, first_post_id=1)
        stats = runner._run_tool_loop([], mock_scene, scene_index=0)

        # Should have hit max_tool_rounds (5)
        assert stats.rounds == 5
        assert agent.chat.call_count == 5

    def test_tool_error_continues_loop(self, runner_with_mocks):
        """Loop continues when tool returns error (not fatal)."""
        runner = runner_with_mocks["runner"]
        agent = runner_with_mocks["agent"]
        dispatcher = runner_with_mocks["dispatcher"]

        # First call returns tool call with error, second returns done
        agent.chat.side_effect = [
            Mock(message={
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "glossary_update", "arguments": "{}"}}],
            }, inference_duration_seconds=0.0),
            Mock(message={"content": "Done", "tool_calls": []}, inference_duration_seconds=0.0),
        ]

        # Tool returns error
        dispatcher.dispatch.return_value = Mock(
            tool_name="glossary_update",
            call_id="call_1",
            success=False,  # Error!
            result="<error>Not found</error>",
        )

        mock_scene = Mock(thread_id=1, last_post_id=10)
        stats = runner._run_tool_loop([], mock_scene, scene_index=0)

        # Should complete despite error
        assert stats.rounds == 2
        assert stats.tool_calls == 1
        assert stats.updated == 0  # Not counted because failed

    def test_tracks_glossary_operations(self, runner_with_mocks):
        """Loop correctly tracks create/update/delete counts."""
        runner = runner_with_mocks["runner"]
        agent = runner_with_mocks["agent"]
        dispatcher = runner_with_mocks["dispatcher"]

        # Return various operations
        agent.chat.side_effect = [
            Mock(message={
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "glossary_create", "arguments": "{}"}},
                    {"id": "call_2", "function": {"name": "glossary_create", "arguments": "{}"}},
                    {"id": "call_3", "function": {"name": "glossary_update", "arguments": "{}"}},
                    {"id": "call_4", "function": {"name": "glossary_delete", "arguments": "{}"}},
                ],
            }, inference_duration_seconds=0.0),
            Mock(message={"content": "Done", "tool_calls": []}, inference_duration_seconds=0.0),
        ]

        dispatcher.dispatch.side_effect = [
            Mock(tool_name="glossary_create", call_id="call_1", success=True, result=""),
            Mock(tool_name="glossary_create", call_id="call_2", success=True, result=""),
            Mock(tool_name="glossary_update", call_id="call_3", success=True, result=""),
            Mock(tool_name="glossary_delete", call_id="call_4", success=True, result=""),
        ]

        mock_scene = Mock(thread_id=1, last_post_id=10)
        stats = runner._run_tool_loop([], mock_scene, scene_index=0)

        assert stats.created == 2
        assert stats.updated == 1
        assert stats.deleted == 1
        assert stats.tool_calls == 4


class TestFromSnapshot:
    """Tests for resuming from snapshot (F9)."""

    def test_config_from_snapshot_id(self, tmp_path):
        """RunnerConfig accepts from_snapshot_id."""
        config = RunnerConfig(
            corpus_db_path=tmp_path / "corpus.db",
            annotator_db_path=tmp_path / "annotator.db",
            from_snapshot_id=42,
        )
        assert config.from_snapshot_id == 42

    def test_config_from_snapshot_id_default(self, tmp_path):
        """from_snapshot_id defaults to None."""
        config = RunnerConfig(
            corpus_db_path=tmp_path / "corpus.db",
            annotator_db_path=tmp_path / "annotator.db",
        )
        assert config.from_snapshot_id is None

    def test_runner_from_snapshot_not_found(self, tmp_path):
        """Runner raises error when snapshot not found."""
        # Create actual test databases so initialization works
        import tempfile
        from terrarium_annotator.storage import GlossaryStore, SnapshotStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            annotator_db = Path(f.name)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            corpus_db = Path(f.name)

        # Initialize storage (creates tables)
        GlossaryStore(annotator_db).close()
        SnapshotStore(annotator_db).close()

        config = RunnerConfig(
            corpus_db_path=corpus_db,
            annotator_db_path=annotator_db,
            from_snapshot_id=9999,  # Non-existent
        )

        with pytest.raises(ValueError, match="Snapshot 9999 not found"):
            AnnotationRunner(config)

    def test_runner_from_snapshot_restores_context(self, tmp_path):
        """Runner restores context and compaction state from snapshot."""
        import tempfile
        from terrarium_annotator.storage import GlossaryStore, SnapshotStore
        from terrarium_annotator.context import AnnotationContext, CompactionState

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            annotator_db = Path(f.name)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            corpus_db = Path(f.name)

        # Create a snapshot to restore from
        glossary = GlossaryStore(annotator_db)
        snapshots = SnapshotStore(annotator_db)

        context = AnnotationContext(system_prompt="Test prompt")
        context.record_turn("user", "Test message")

        compaction_state = CompactionState(
            cumulative_summary="The story so far...",
            completed_thread_ids=[1, 2, 3],
        )

        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=500,
            last_thread_id=5,
            thread_position=3,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
            token_count=10000,
        )

        glossary.close()
        snapshots.close()

        # Create runner with from_snapshot_id
        config = RunnerConfig(
            corpus_db_path=corpus_db,
            annotator_db_path=annotator_db,
            from_snapshot_id=snapshot_id,
        )

        runner = AnnotationRunner(config)

        try:
            # Verify context was restored
            assert len(runner.context.conversation_history) > 0
            # Verify compaction state was restored
            assert runner.compaction_state.cumulative_summary == "The story so far..."
            assert runner.compaction_state.completed_thread_ids == [1, 2, 3]
            # Verify resume position was set
            assert runner._resume_from_snapshot_post_id == 500
            assert runner._thread_position == 3
        finally:
            runner.close()
