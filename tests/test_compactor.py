"""Tests for ContextCompactor."""

from unittest.mock import Mock

import pytest

from terrarium_annotator.context.compactor import (
    CompactionResult,
    CompactionState,
    ContextCompactor,
)
from terrarium_annotator.context.models import ThreadSummary


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
        """Should trigger at 80% of budget."""
        compactor = ContextCompactor(
            token_counter=mock_counter,
            summarizer=mock_summarizer,
            context_budget=10000,  # trigger at 8000
        )

        mock_counter.count_messages.return_value = 8500
        assert compactor.should_compact([]) is True

        mock_counter.count_messages.return_value = 7500
        assert compactor.should_compact([]) is False

    def test_should_compact_at_boundary(self, mock_counter, mock_summarizer):
        """Should trigger exactly at 80%."""
        compactor = ContextCompactor(
            token_counter=mock_counter,
            summarizer=mock_summarizer,
            context_budget=10000,
        )

        mock_counter.count_messages.return_value = 8000
        assert compactor.should_compact([]) is False  # Equal to trigger, not above

        mock_counter.count_messages.return_value = 8001
        assert compactor.should_compact([]) is True

    def test_tier1_summarizes_oldest_thread(self, mock_counter, mock_summarizer):
        """Tier 1: Should summarize oldest completed thread."""
        # Start above trigger, end below target after one iteration
        mock_counter.count_messages.side_effect = [9000, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(completed_thread_ids=[1, 2, 3])
        messages, result = compactor.compact([], state)

        assert result.threads_summarized == 1
        assert 1 not in state.completed_thread_ids
        assert len(state.thread_summaries) == 1
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

    def test_tier2_merges_summaries(self, mock_counter, mock_summarizer):
        """Tier 2: Should merge old summaries."""
        mock_counter.count_messages.side_effect = [9000, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(
            completed_thread_ids=[1],  # Only 1, so tier 1 skipped
            thread_summaries=[
                ThreadSummary(1, 0, "Summary 1"),
                ThreadSummary(2, 1, "Summary 2"),
                ThreadSummary(3, 2, "Summary 3"),
                ThreadSummary(4, 3, "Summary 4"),
            ],  # 4 summaries > 3 threshold
        )
        messages, result = compactor.compact([], state)

        assert result.summaries_merged == 2
        assert len(state.thread_summaries) == 2
        assert state.cumulative_summary != ""

    def test_tier2_preserves_recent_summaries(self, mock_counter, mock_summarizer):
        """Tier 2: Should preserve 3 most recent summaries."""
        mock_counter.count_messages.return_value = 9000  # Always above target

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(
            completed_thread_ids=[1],
            thread_summaries=[
                ThreadSummary(1, 0, "Summary 1"),
                ThreadSummary(2, 1, "Summary 2"),
                ThreadSummary(3, 2, "Summary 3"),
            ],  # Exactly 3
        )
        messages, result = compactor.compact([], state)

        # No merging since <= 3 summaries
        assert result.summaries_merged == 0

    def test_tier3_trims_thinking(self, mock_counter, mock_summarizer):
        """Tier 3: Should trim thinking blocks from old messages."""
        # First call above target, second below
        mock_counter.count_messages.side_effect = [9000, 6000]

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
        """Tier 4: Should truncate old long responses."""
        mock_counter.count_messages.side_effect = [9000, 6000]

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
        """Result should track counts from all tiers."""
        # Multiple iterations needed
        mock_counter.count_messages.side_effect = [9000, 8500, 8000, 6000]

        compactor = ContextCompactor(
            mock_counter, mock_summarizer, context_budget=10000
        )

        state = CompactionState(
            completed_thread_ids=[1, 2, 3],
            thread_summaries=[
                ThreadSummary(10, 0, "S1"),
                ThreadSummary(11, 1, "S2"),
                ThreadSummary(12, 2, "S3"),
                ThreadSummary(13, 3, "S4"),
            ],
        )
        messages = [
            {"role": "assistant", "content": "<thinking>T</thinking>R"},
        ]

        result_messages, result = compactor.compact(messages, state)

        # Should have done multiple operations
        assert result.initial_tokens == 9000
        assert result.final_tokens == 6000
        assert result.target_reached is True

    def test_custom_thresholds(self, mock_counter, mock_summarizer):
        """Should respect custom trigger and target ratios."""
        compactor = ContextCompactor(
            mock_counter,
            mock_summarizer,
            context_budget=10000,
            trigger_ratio=0.50,  # Trigger at 5000
            target_ratio=0.30,  # Target 3000
        )

        mock_counter.count_messages.return_value = 5500
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
            threads_summarized=2,
            summaries_merged=1,
            turns_trimmed=3,
            responses_truncated=1,
            target_reached=True,
        )

        assert result.initial_tokens == 10000
        assert result.final_tokens == 7000
        assert result.threads_summarized == 2
        assert result.summaries_merged == 1
        assert result.turns_trimmed == 3
        assert result.responses_truncated == 1
        assert result.target_reached is True
