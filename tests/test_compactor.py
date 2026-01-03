"""Tests for ContextCompactor."""

from unittest.mock import Mock

import pytest

from terrarium_annotator.context.compactor import (
    CompactionResult,
    CompactionState,
    ContextCompactor,
)
from terrarium_annotator.context.models import ChunkSummary, ThreadSummary


class TestContextCompactor:
    """ContextCompactor tests."""

    @pytest.fixture
    def mock_counter(self):
        counter = Mock()
        counter.count_messages.return_value = 10000
        return counter

    @pytest.fixture
    def mock_summarizer(self):
        summarizer = Mock()
        summarizer.summarize_thread.return_value = Mock(
            thread_id=1,
            summary_text="Thread 1 summary.",
            entries_created=[],
            entries_updated=[],
            token_count=50,
        )
        summarizer.to_thread_summary.return_value = ThreadSummary(
            thread_id=1,
            position=0,
            summary_text="Thread 1 summary.",
        )
        return summarizer

    def test_should_compact_above_trigger(self, mock_counter, mock_summarizer):
        """Rolling compaction triggers at 80% of budget."""
        compactor = ContextCompactor(
            token_counter=mock_counter,
            summarizer=mock_summarizer,
            context_budget=10000,  # rolling at 8000, emergency at 9000
        )

        # Above 80% - should trigger rolling compaction
        mock_counter.count_messages.return_value = 8500
        assert compactor.should_compact([]) is True

        # Below 80% - no compaction
        mock_counter.count_messages.return_value = 7500
        assert compactor.should_compact([]) is False

    def test_should_compact_at_boundary(self, mock_counter, mock_summarizer):
        """Thread compact at 80%, should_compact also at 80%."""
        compactor = ContextCompactor(
            token_counter=mock_counter,
            summarizer=mock_summarizer,
            context_budget=10000,
        )

        # Thread compact triggers at 80%
        mock_counter.count_messages.return_value = 8000
        assert compactor.should_compact_thread([]) is False

        mock_counter.count_messages.return_value = 8001
        assert compactor.should_compact_thread([]) is True

        # should_compact() now uses same 80% threshold for rolling compaction
        mock_counter.count_messages.return_value = 8000
        assert compactor.should_compact([]) is False

        mock_counter.count_messages.return_value = 8001
        assert compactor.should_compact([]) is True

        # Emergency compact (tiers 2-4) triggers at 90%
        mock_counter.count_messages.return_value = 9000
        assert compactor.should_emergency_compact([]) is False

        mock_counter.count_messages.return_value = 9001
        assert compactor.should_emergency_compact([]) is True

    def test_tier1_summarizes_oldest_thread(self, mock_counter, mock_summarizer):
        """Tier 1: Should summarize oldest completed thread and merge into cumulative."""
        # Start above trigger, end below target after one iteration
        mock_counter.count_messages.side_effect = [9000, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(completed_thread_ids=[1, 2, 3])
        messages, result = compactor.compact([], state)

        assert result.threads_summarized == 1
        assert result.summaries_merged == 1  # Immediately merged into cumulative
        assert 1 not in state.completed_thread_ids
        # Thread summaries are now merged into cumulative, not kept separately
        assert state.cumulative_summary != ""
        mock_summarizer.summarize_thread.assert_called_once()

    def test_tier1_preserves_last_thread(self, mock_counter, mock_summarizer):
        """Tier 1: Should not summarize if only one thread remains."""
        mock_counter.count_messages.return_value = 9000  # Always above target

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(completed_thread_ids=[1])  # Only 1
        messages, result = compactor.compact([], state)

        assert result.threads_summarized == 0
        mock_summarizer.summarize_thread.assert_not_called()

    def test_tier1_merges_multiple_threads(self, mock_counter, mock_summarizer):
        """Tier 1: Should summarize and merge multiple completed threads."""
        # Need multiple iterations to summarize multiple threads
        mock_counter.count_messages.side_effect = [9100, 8500, 8000, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(
            completed_thread_ids=[1, 2, 3, 4],  # 4 threads to process
        )
        messages, result = compactor.compact([], state)

        # Should summarize threads until below soft threshold
        assert result.threads_summarized >= 1
        assert result.summaries_merged >= 1  # Each summarized thread is merged
        assert state.cumulative_summary != ""

    def test_tier1_with_only_one_thread_skips(self, mock_counter, mock_summarizer):
        """Tier 1: Should skip when only one completed thread (need 2+)."""
        mock_counter.count_messages.return_value = 9000  # Always above target

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(
            completed_thread_ids=[1],  # Only 1, so tier 1 skipped
            thread_summaries=[
                ThreadSummary(1, 0, "Summary 1"),
                ThreadSummary(2, 1, "Summary 2"),
                ThreadSummary(3, 2, "Summary 3"),
            ],  # Legacy summaries
        )
        messages, result = compactor.compact([], state)

        # No summarization since only 1 completed thread
        assert result.threads_summarized == 0
        assert result.summaries_merged == 0

    def test_tier3_trims_thinking(self, mock_counter, mock_summarizer):
        """Tier 3: Should trim thinking blocks from old messages (emergency only)."""
        # Need >9000 to trigger emergency mode for tier 3
        mock_counter.count_messages.side_effect = [9100, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        # Need more than preserve_recent (4) messages so old ones get trimmed
        messages = [
            {"role": "assistant", "content": "<thinking>Internal thoughts</thinking>Response"},
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"},
            {"role": "assistant", "content": "Answer 2"},
            {"role": "user", "content": "Question 3"},
            {"role": "assistant", "content": "Recent response"},
            {"role": "user", "content": "Recent question"},
        ]

        state = CompactionState(completed_thread_ids=[1])
        result_messages, result = compactor.compact(messages, state)

        assert result.turns_trimmed >= 1
        # First message should have thinking removed
        assert "<thinking>" not in result_messages[0]["content"]
        assert "Response" in result_messages[0]["content"]

    def test_tier3_preserves_recent_thinking(self, mock_counter, mock_summarizer):
        """Tier 3: Should preserve thinking in recent messages."""
        mock_counter.count_messages.return_value = 9000

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        # Only recent messages with thinking
        messages = [
            {"role": "assistant", "content": "<thinking>Thoughts</thinking>Response"},
        ]

        state = CompactionState(completed_thread_ids=[1])
        result_messages, result = compactor.compact(messages, state)

        # Should preserve since it's recent
        assert "<thinking>" in result_messages[0]["content"]

    def test_tier4_truncates_responses(self, mock_counter, mock_summarizer):
        """Tier 4: Should truncate old long responses (emergency only)."""
        # Need >9000 to trigger emergency mode for tier 4
        mock_counter.count_messages.side_effect = [9100, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        long_content = "A" * 1000  # > 500 chars
        messages = [
            {"role": "assistant", "content": long_content},  # Old
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Q3"},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Q4"},
            {"role": "assistant", "content": "Recent"},  # Within max_age
        ]

        state = CompactionState(completed_thread_ids=[1])
        result_messages, result = compactor.compact(messages, state)

        assert result.responses_truncated >= 1
        assert len(result_messages[0]["content"]) < len(long_content)
        assert "[truncated]" in result_messages[0]["content"]

    def test_tier4_preserves_recent_responses(self, mock_counter, mock_summarizer):
        """Tier 4: Should preserve recent long responses."""
        mock_counter.count_messages.return_value = 9000

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        long_content = "A" * 1000
        messages = [
            {"role": "assistant", "content": long_content},  # Only message, counts as recent
        ]

        state = CompactionState(completed_thread_ids=[1])
        result_messages, result = compactor.compact(messages, state)

        # Should not truncate since it's within max_age
        assert result_messages[0]["content"] == long_content

    def test_tier4_skips_already_truncated(self, mock_counter, mock_summarizer):
        """Tier 4: Should not re-truncate already-truncated messages."""
        # This prevents the doom loop where truncated messages (515 chars)
        # are still > max_len (500) and get re-counted each iteration
        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        # Message that's already been truncated
        already_truncated = "A" * 500 + "... [truncated]"
        messages = [
            {"role": "assistant", "content": already_truncated},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Q3"},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Q4"},
            {"role": "assistant", "content": "Recent"},
        ]

        result_messages, count = compactor._truncate_responses(messages, max_age=8, max_len=500)

        # Should NOT count already-truncated message
        assert count == 0
        # Content should be unchanged
        assert result_messages[0]["content"] == already_truncated

    def test_stops_when_target_reached(self, mock_counter, mock_summarizer):
        """Should stop compacting once target is reached."""
        # Returns above target, then below after first tier
        mock_counter.count_messages.side_effect = [9000, 6500]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(completed_thread_ids=[1, 2, 3, 4, 5])
        messages, result = compactor.compact([], state)

        # Should stop after first summarization
        assert result.target_reached is True
        assert result.threads_summarized == 1

    def test_stops_when_no_options(self, mock_counter, mock_summarizer):
        """Should stop and warn when no compaction options remain."""
        mock_counter.count_messages.return_value = 9000  # Always above target

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState()  # No threads, no summaries
        messages, result = compactor.compact([], state)

        assert result.target_reached is False
        assert result.initial_tokens == 9000
        assert result.final_tokens == 9000

    def test_result_tracks_all_operations(self, mock_counter, mock_summarizer):
        """Result should track counts from all tiers (emergency mode)."""
        # Multiple iterations needed - each tier needs token count check
        mock_counter.count_messages.side_effect = [9100, 8000, 7500, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(
            completed_thread_ids=[1, 2, 3, 4],  # Need multiple threads to summarize
        )
        messages = [
            {"role": "assistant", "content": "<thinking>T</thinking>R"},
        ]

        result_messages, result = compactor.compact(messages, state)

        # Should have done multiple thread summarizations
        assert result.initial_tokens == 9100
        assert result.threads_summarized >= 1
        assert result.summaries_merged >= 1  # Each thread is merged into cumulative

    def test_custom_thresholds(self, mock_counter, mock_summarizer):
        """Should respect custom threshold ratios."""
        compactor = ContextCompactor(
            mock_counter,
            mock_summarizer,
            context_budget=10000,
            thread_compact_ratio=0.50,  # Thread compact at 5000
            emergency_ratio=0.70,  # Emergency at 7000
            target_ratio=0.30,  # Target 3000
        )

        # Thread compact at 50%
        mock_counter.count_messages.return_value = 5500
        assert compactor.should_compact_thread([]) is True

        mock_counter.count_messages.return_value = 4500
        assert compactor.should_compact_thread([]) is False

        # Emergency at 70%
        mock_counter.count_messages.return_value = 7500
        assert compactor.should_compact([]) is True

        mock_counter.count_messages.return_value = 4500
        assert compactor.should_compact([]) is False

    def test_messages_not_mutated(self, mock_counter, mock_summarizer):
        """Original messages list should not be mutated."""
        mock_counter.count_messages.side_effect = [9000, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        original = [{"role": "assistant", "content": "Original"}]
        state = CompactionState(completed_thread_ids=[1, 2])

        result_messages, _ = compactor.compact(original, state)

        # Original should be unchanged
        assert original[0]["content"] == "Original"


class TestCompactionState:
    """CompactionState dataclass tests."""

    def test_defaults(self):
        state = CompactionState()
        assert state.cumulative_summary == ""
        assert state.thread_summaries == []
        assert state.completed_thread_ids == []

    def test_mutable(self):
        state = CompactionState()
        state.completed_thread_ids.append(1)
        state.thread_summaries.append(ThreadSummary(1, 0, "Summary"))
        state.cumulative_summary = "Updated"

        assert state.completed_thread_ids == [1]
        assert len(state.thread_summaries) == 1
        assert state.cumulative_summary == "Updated"


class TestCompactionResult:
    """CompactionResult dataclass tests."""

    def test_fields(self):
        result = CompactionResult(
            initial_tokens=10000,
            final_tokens=7000,
            chunks_summarized=1,
            threads_summarized=2,
            summaries_merged=1,
            turns_trimmed=3,
            responses_truncated=1,
            target_reached=True,
        )

        assert result.initial_tokens == 10000
        assert result.final_tokens == 7000
        assert result.chunks_summarized == 1
        assert result.threads_summarized == 2
        assert result.summaries_merged == 1
        assert result.turns_trimmed == 3
        assert result.responses_truncated == 1
        assert result.target_reached is True


class TestChunkSummary:
    """ChunkSummary dataclass tests."""

    def test_fields(self):
        cs = ChunkSummary(
            thread_id=5,
            chunk_index=2,
            first_scene_index=20,
            last_scene_index=29,
            summary_text="Chunk summary text",
            entries_created=[1, 2],
            entries_updated=[3],
        )

        assert cs.thread_id == 5
        assert cs.chunk_index == 2
        assert cs.first_scene_index == 20
        assert cs.last_scene_index == 29
        assert cs.summary_text == "Chunk summary text"
        assert cs.entries_created == [1, 2]
        assert cs.entries_updated == [3]

    def test_serialization(self):
        cs = ChunkSummary(
            thread_id=5,
            chunk_index=0,
            first_scene_index=0,
            last_scene_index=9,
            summary_text="Summary",
        )
        data = cs.to_dict()

        restored = ChunkSummary.from_dict(data)
        assert restored.thread_id == cs.thread_id
        assert restored.chunk_index == cs.chunk_index
        assert restored.first_scene_index == cs.first_scene_index
        assert restored.last_scene_index == cs.last_scene_index
        assert restored.summary_text == cs.summary_text


class TestCompactionStateChunks:
    """CompactionState chunk tracking tests."""

    def test_start_new_thread_resets_chunk_state(self):
        state = CompactionState()
        state.current_thread_id = 1
        state.current_scene_index = 50
        state.chunk_summaries = [ChunkSummary(1, 0, 0, 9, "Old summary")]
        state.summarized_chunk_indices = [0, 1, 2]

        state.start_new_thread(2)

        assert state.current_thread_id == 2
        assert state.current_scene_index == 0
        assert state.chunk_summaries == []
        assert state.summarized_chunk_indices == []

    def test_advance_scene(self):
        state = CompactionState()
        state.current_scene_index = 5

        state.advance_scene()

        assert state.current_scene_index == 6

    def test_get_completed_chunk_count(self):
        state = CompactionState()
        state.current_scene_index = 25  # 2 complete chunks (0-9, 10-19)

        assert state.get_completed_chunk_count(10) == 2

        state.current_scene_index = 30  # 3 complete chunks
        assert state.get_completed_chunk_count(10) == 3

        state.current_scene_index = 9  # 0 complete chunks (9 < 10)
        assert state.get_completed_chunk_count(10) == 0

    def test_get_unsummarized_chunks(self):
        state = CompactionState()
        state.current_scene_index = 35  # 3 complete chunks
        state.summarized_chunk_indices = [0]  # Only chunk 0 summarized

        unsummarized = state.get_unsummarized_chunks(10)

        assert len(unsummarized) == 2
        assert unsummarized[0] == (1, 10, 19)  # chunk 1
        assert unsummarized[1] == (2, 20, 29)  # chunk 2

    def test_chunk_serialization(self):
        state = CompactionState()
        state.cumulative_summary = "Cumulative"
        state.chunk_summaries = [ChunkSummary(1, 0, 0, 9, "Chunk summary")]
        state.current_thread_id = 5
        state.current_scene_index = 15
        state.summarized_chunk_indices = [0]

        data = state.to_dict()
        restored = CompactionState.from_dict(data)

        assert restored.cumulative_summary == "Cumulative"
        assert len(restored.chunk_summaries) == 1
        assert restored.chunk_summaries[0].summary_text == "Chunk summary"
        assert restored.current_thread_id == 5
        assert restored.current_scene_index == 15
        assert restored.summarized_chunk_indices == [0]


class TestTier05ChunkCompaction:
    """Tier 0.5 chunk compaction tests."""

    @pytest.fixture
    def mock_counter(self):
        counter = Mock()
        counter.count_messages.return_value = 10000
        return counter

    @pytest.fixture
    def mock_summarizer(self):
        summarizer = Mock()
        summarizer.summarize_chunk.return_value = ChunkSummary(
            thread_id=1,
            chunk_index=0,
            first_scene_index=0,
            last_scene_index=9,
            summary_text="Chunk 0 summary",
        )
        return summarizer

    def test_tier05_summarizes_oldest_chunk(self, mock_counter, mock_summarizer):
        """Tier 0.5: Should summarize oldest completed chunk."""
        mock_counter.count_messages.side_effect = [9000, 6000]

        compactor = ContextCompactor(
            mock_counter,
            mock_summarizer,
            context_budget=10000,
            scenes_per_chunk=10,
            preserve_recent_chunks=2,
        )

        # Set up state with completed chunks
        state = CompactionState()
        state.current_thread_id = 1
        state.current_scene_index = 35  # 3 complete chunks (0-9, 10-19, 20-29)
        # Keep last 2 chunks intact, so chunk 0 can be summarized

        # Add messages with scene_index tags
        messages = [
            {"role": "user", "content": "Scene 0", "thread_id": 1, "scene_index": 0},
            {"role": "assistant", "content": "Response 0", "thread_id": 1, "scene_index": 0},
            {"role": "user", "content": "Scene 10", "thread_id": 1, "scene_index": 10},
            {"role": "assistant", "content": "Response 10", "thread_id": 1, "scene_index": 10},
        ]

        result_messages, result = compactor.compact(messages, state)

        assert result.chunks_summarized == 1
        assert result.highest_tier == 0.5
        assert 0 in state.summarized_chunk_indices
        assert len(state.chunk_summaries) == 1
        mock_summarizer.summarize_chunk.assert_called_once()

    def test_tier05_preserves_recent_chunks(self, mock_counter, mock_summarizer):
        """Tier 0.5: Should preserve recent chunks."""
        mock_counter.count_messages.return_value = 9000

        compactor = ContextCompactor(
            mock_counter,
            mock_summarizer,
            context_budget=10000,
            scenes_per_chunk=10,
            preserve_recent_chunks=2,
        )

        # Only 2 complete chunks - same as preserve_recent_chunks, so no summarization
        state = CompactionState()
        state.current_thread_id = 1
        state.current_scene_index = 25  # 2 complete chunks

        result_messages, result = compactor.compact([], state)

        assert result.chunks_summarized == 0
        mock_summarizer.summarize_chunk.assert_not_called()

    def test_tier05_removes_chunk_turns(self, mock_counter, mock_summarizer):
        """Tier 0.5: Should remove chunk turns after summarization."""
        mock_counter.count_messages.side_effect = [9000, 6000]

        compactor = ContextCompactor(
            mock_counter,
            mock_summarizer,
            context_budget=10000,
            scenes_per_chunk=10,
            preserve_recent_chunks=2,
        )

        state = CompactionState()
        state.current_thread_id = 1
        state.current_scene_index = 35  # 3 complete chunks

        # Messages from chunk 0 (scenes 0-9) should be removed
        messages = [
            {"role": "user", "content": "Scene 5", "thread_id": 1, "scene_index": 5},
            {"role": "assistant", "content": "Response 5", "thread_id": 1, "scene_index": 5},
            {"role": "user", "content": "Scene 25", "thread_id": 1, "scene_index": 25},
            {"role": "assistant", "content": "Response 25", "thread_id": 1, "scene_index": 25},
        ]

        result_messages, result = compactor.compact(messages, state)

        # Messages from chunk 0 should be removed
        assert len(result_messages) == 2
        assert all(m.get("scene_index", 0) >= 10 for m in result_messages)
