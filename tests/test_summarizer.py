"""Tests for ThreadSummarizer."""

from unittest.mock import Mock, patch

import pytest

from terrarium_annotator.context.summarizer import SummaryResult, ThreadSummarizer
from terrarium_annotator.storage import GlossaryEntry


def _make_entry(
    entry_id: int,
    term: str,
    first_thread: int = 1,
    last_thread: int = 1,
) -> GlossaryEntry:
    """Create a mock GlossaryEntry."""
    return GlossaryEntry(
        id=entry_id,
        term=term,
        term_normalized=term.lower(),
        definition=f"Definition of {term}",
        status="confirmed",
        tags=["character"],
        first_seen_post_id=1,
        first_seen_thread_id=first_thread,
        last_updated_post_id=1,
        last_updated_thread_id=last_thread,
        created_at="2024-01-01",
        updated_at="2024-01-01",
    )


class TestThreadSummarizer:
    """ThreadSummarizer tests."""

    @pytest.fixture
    def mock_agent(self):
        agent = Mock()
        agent.chat.return_value = Mock(
            message={"content": "Thread 1 summary: Key events happened."}
        )
        return agent

    @pytest.fixture
    def mock_glossary(self):
        glossary = Mock()
        glossary.get_by_thread.return_value = []
        return glossary

    def test_summarize_thread_calls_agent(self, mock_agent, mock_glossary):
        """Should call agent with summary prompt."""
        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )

        result = summarizer.summarize_thread(1, [])

        mock_agent.chat.assert_called_once()
        assert result.thread_id == 1
        assert "summary" in result.summary_text.lower()

    def test_fallback_on_empty_response(self, mock_agent, mock_glossary):
        """Should use heuristic when agent returns empty."""
        mock_agent.chat.return_value = Mock(message={"content": ""})
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(1, "Soma")],  # created
            [],  # updated
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])

        assert "Thread 1" in result.summary_text
        assert "Soma" in result.summary_text

    def test_fallback_on_agent_error(self, mock_agent, mock_glossary):
        """Should use heuristic when agent raises exception."""
        mock_agent.chat.side_effect = Exception("Connection refused")
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(1, "Soma")],
            [],
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])

        assert "Thread 1" in result.summary_text
        assert "Soma" in result.summary_text

    def test_includes_entry_ids(self, mock_agent, mock_glossary):
        """Should include created/updated entry IDs."""
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(1, "Soma"), _make_entry(2, "Chiron")],  # created
            [_make_entry(3, "Apollo", last_thread=1)],  # updated
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])

        assert result.entries_created == [1, 2]
        assert result.entries_updated == [3]

    def test_filters_out_created_from_updated(self, mock_agent, mock_glossary):
        """Updated list should not include entries also in created list."""
        # Entry 1 is in both created and updated (same thread)
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(1, "Soma", first_thread=1)],  # created
            [_make_entry(1, "Soma", last_thread=1)],  # same entry in updated
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])

        assert result.entries_created == [1]
        assert result.entries_updated == []  # Filtered out

    def test_builds_correct_messages(self, mock_agent, mock_glossary):
        """Should build messages with prompt and conversation."""
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(1, "Soma")],
            [],
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        summarizer.summarize_thread(
            1,
            [
                {"role": "user", "content": "Some context"},
                {"role": "assistant", "content": "Response"},
            ],
        )

        call_args = mock_agent.chat.call_args
        messages = call_args.kwargs["messages"]

        # Should have system, conversation turns, and user request
        assert messages[0]["role"] == "system"
        assert "thread" in messages[0]["content"].lower()
        assert any(m["content"] == "Some context" for m in messages if m["role"] == "user")
        assert messages[-1]["role"] == "user"
        assert "summary" in messages[-1]["content"].lower()

    def test_token_count_estimation(self, mock_agent, mock_glossary):
        """Should estimate token count from summary length."""
        mock_agent.chat.return_value = Mock(
            message={"content": "A" * 100}  # 100 chars
        )

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
            chars_per_token=4.0,
        )
        result = summarizer.summarize_thread(1, [])

        assert result.token_count == 25  # 100 / 4

    def test_to_thread_summary(self, mock_agent, mock_glossary):
        """Should convert SummaryResult to ThreadSummary."""
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(1, "Soma")],
            [],
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])
        thread_summary = summarizer.to_thread_summary(result, position=5)

        assert thread_summary.thread_id == 1
        assert thread_summary.position == 5
        assert thread_summary.summary_text == result.summary_text
        assert thread_summary.entries_created == result.entries_created

    def test_heuristic_summary_no_entries(self, mock_agent, mock_glossary):
        """Heuristic summary should handle no entries."""
        mock_agent.chat.return_value = Mock(message={"content": ""})
        mock_glossary.get_by_thread.return_value = []

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])

        assert "Thread 1" in result.summary_text
        assert "No glossary changes" in result.summary_text

    def test_heuristic_summary_many_entries(self, mock_agent, mock_glossary):
        """Heuristic summary should truncate long entry lists."""
        mock_agent.chat.return_value = Mock(message={"content": ""})
        mock_glossary.get_by_thread.side_effect = [
            [_make_entry(i, f"Term{i}") for i in range(10)],  # 10 created
            [],
        ]

        summarizer = ThreadSummarizer(
            agent_client=mock_agent,
            glossary=mock_glossary,
        )
        result = summarizer.summarize_thread(1, [])

        assert "Term0" in result.summary_text
        assert "+5 more" in result.summary_text


class TestSummaryResult:
    """SummaryResult dataclass tests."""

    def test_dataclass_fields(self):
        result = SummaryResult(
            thread_id=5,
            summary_text="Thread 5 summary.",
            entries_created=[1, 2, 3],
            entries_updated=[4],
            token_count=50,
        )

        assert result.thread_id == 5
        assert result.summary_text == "Thread 5 summary."
        assert result.entries_created == [1, 2, 3]
        assert result.entries_updated == [4]
        assert result.token_count == 50
