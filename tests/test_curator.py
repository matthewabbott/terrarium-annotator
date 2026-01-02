"""Tests for CuratorFork."""

import json
from unittest.mock import Mock, patch

import pytest

from terrarium_annotator.curator import CuratorDecision, CuratorFork, CuratorResult
from terrarium_annotator.storage import GlossaryEntry


def _make_entry(
    entry_id: int,
    term: str,
    status: str = "tentative",
    thread_id: int = 1,
    post_id: int = 100,
) -> GlossaryEntry:
    """Create a mock GlossaryEntry."""
    return GlossaryEntry(
        id=entry_id,
        term=term,
        term_normalized=term.lower(),
        definition=f"Definition of {term}",
        status=status,
        tags=["character"],
        first_seen_post_id=post_id,
        first_seen_thread_id=thread_id,
        last_updated_post_id=post_id,
        last_updated_thread_id=thread_id,
        created_at="2024-01-01",
        updated_at="2024-01-01",
    )


class TestCuratorDecision:
    """CuratorDecision dataclass tests."""

    def test_dataclass_fields(self):
        decision = CuratorDecision(
            entry_id=1,
            entry_term="Soma",
            action="CONFIRM",
            reasoning="Entry is accurate",
        )
        assert decision.entry_id == 1
        assert decision.entry_term == "Soma"
        assert decision.action == "CONFIRM"
        assert decision.target_id is None
        assert decision.revised_definition is None
        assert decision.reasoning == "Entry is accurate"

    def test_merge_decision(self):
        decision = CuratorDecision(
            entry_id=1,
            entry_term="Soma",
            action="MERGE",
            target_id=5,
            reasoning="Duplicate of entry #5",
        )
        assert decision.action == "MERGE"
        assert decision.target_id == 5

    def test_revise_decision(self):
        decision = CuratorDecision(
            entry_id=1,
            entry_term="Soma",
            action="REVISE",
            revised_definition="Updated definition",
            reasoning="Original was incomplete",
        )
        assert decision.action == "REVISE"
        assert decision.revised_definition == "Updated definition"


class TestCuratorResult:
    """CuratorResult dataclass tests."""

    def test_defaults(self):
        result = CuratorResult(thread_id=1)
        assert result.thread_id == 1
        assert result.entries_evaluated == 0
        assert result.confirmed == 0
        assert result.rejected == 0
        assert result.merged == 0
        assert result.revised == 0
        assert result.decisions == []

    def test_with_decisions(self):
        result = CuratorResult(
            thread_id=1,
            entries_evaluated=3,
            confirmed=2,
            rejected=1,
            decisions=[
                CuratorDecision(1, "Term1", "CONFIRM", reasoning="Good"),
                CuratorDecision(2, "Term2", "CONFIRM", reasoning="Good"),
                CuratorDecision(3, "Term3", "REJECT", reasoning="Bad"),
            ],
        )
        assert len(result.decisions) == 3


class TestCuratorFork:
    """CuratorFork tests."""

    @pytest.fixture
    def mock_deps(self):
        return {
            "glossary": Mock(),
            "corpus": Mock(),
            "revisions": Mock(),
            "agent": Mock(),
        }

    def test_run_no_tentative_entries(self, mock_deps):
        """Should return empty result when no tentative entries."""
        mock_deps["glossary"].get_tentative_by_thread.return_value = []

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.thread_id == 1
        assert result.entries_evaluated == 0
        assert result.decisions == []
        mock_deps["agent"].chat.assert_not_called()

    def test_run_confirms_entry(self, mock_deps):
        """Should confirm entry when agent returns CONFIRM."""
        entry = _make_entry(1, "Soma")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "CONFIRM", "reasoning": "Entry is good"}'}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.confirmed == 1
        assert result.rejected == 0
        assert len(result.decisions) == 1
        assert result.decisions[0].action == "CONFIRM"
        mock_deps["glossary"].update.assert_called_once()

    def test_run_rejects_entry(self, mock_deps):
        """Should delete entry when agent returns REJECT."""
        entry = _make_entry(1, "BadEntry")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "REJECT", "reasoning": "Too vague"}'}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.rejected == 1
        assert result.confirmed == 0
        mock_deps["glossary"].delete.assert_called_once_with(1, reason="curator:reject")
        mock_deps["revisions"].log_deletion.assert_called_once()

    def test_run_merges_entries(self, mock_deps):
        """Should merge entry into target when MERGE is returned."""
        entry = _make_entry(1, "Duplicate")
        target = _make_entry(5, "Original", status="confirmed")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = [target]
        mock_deps["glossary"].get.side_effect = lambda id: entry if id == 1 else target
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "MERGE", "target_id": 5, "reasoning": "Duplicate"}'}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.merged == 1
        # Should update target with merged definition
        mock_deps["glossary"].update.assert_called()
        # Should delete source
        mock_deps["glossary"].delete.assert_called_once_with(1, reason="curator:merge")

    def test_run_revises_entry(self, mock_deps):
        """Should update definition when REVISE is returned."""
        entry = _make_entry(1, "Incomplete")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={
                "content": '{"action": "REVISE", "revised_definition": "Better definition", "reasoning": "Original was incomplete"}'
            }
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.revised == 1
        mock_deps["glossary"].update.assert_called_with(
            1,
            definition="Better definition",
            status="confirmed",
            post_id=0,
            thread_id=1,
        )

    def test_parses_json_from_response(self, mock_deps):
        """Should extract JSON from agent response with surrounding text."""
        entry = _make_entry(1, "Term")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        # Response with text before and after JSON
        mock_deps["agent"].chat.return_value = Mock(
            message={
                "content": 'Here is my decision:\n\n{"action": "CONFIRM", "reasoning": "Looks good"}\n\nDone!'
            }
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.confirmed == 1
        assert result.decisions[0].action == "CONFIRM"
        assert result.decisions[0].reasoning == "Looks good"

    def test_defaults_to_confirm_on_invalid_json(self, mock_deps):
        """Should default to CONFIRM when JSON parsing fails."""
        entry = _make_entry(1, "Term")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": "I think this entry is fine, keep it."}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.confirmed == 1
        assert "defaulting to confirm" in result.decisions[0].reasoning.lower()

    def test_defaults_to_confirm_on_agent_error(self, mock_deps):
        """Should default to CONFIRM when agent call fails."""
        entry = _make_entry(1, "Term")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.side_effect = Exception("Connection failed")

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.confirmed == 1
        assert "failed" in result.decisions[0].reasoning.lower()

    def test_logs_decisions_to_revisions(self, mock_deps):
        """Should log each decision to revision history."""
        entry = _make_entry(1, "Term")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "CONFIRM", "reasoning": "Good entry"}'}
        )

        curator = CuratorFork(**mock_deps)
        curator.run(thread_id=1)

        mock_deps["revisions"].log_change.assert_called_once()
        call_kwargs = mock_deps["revisions"].log_change.call_args.kwargs
        assert call_kwargs["entry_id"] == 1
        assert call_kwargs["field_name"] == "curator_decision"
        # Check the logged JSON contains expected fields
        logged = json.loads(call_kwargs["new_value"])
        assert logged["action"] == "CONFIRM"

    def test_fetches_context_posts(self, mock_deps):
        """Should fetch adjacent posts for context."""
        entry = _make_entry(1, "Term", post_id=100)
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "CONFIRM", "reasoning": "Good"}'}
        )

        curator = CuratorFork(**mock_deps, context_posts=5)
        curator.run(thread_id=1)

        mock_deps["corpus"].get_adjacent_posts.assert_called_once_with(
            100, before=5, after=5
        )

    def test_finds_similar_entries(self, mock_deps):
        """Should search for similar entries and filter out self."""
        entry = _make_entry(1, "Soma")
        similar = _make_entry(2, "Soma the Warrior", status="confirmed")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = [entry, similar]  # Includes self
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "CONFIRM", "reasoning": "Good"}'}
        )

        curator = CuratorFork(**mock_deps)
        curator.run(thread_id=1)

        # Should have called search with the term
        mock_deps["glossary"].search.assert_called_with("Soma", limit=5)

    def test_evaluates_multiple_entries(self, mock_deps):
        """Should evaluate all tentative entries in thread."""
        entries = [
            _make_entry(1, "Term1"),
            _make_entry(2, "Term2"),
            _make_entry(3, "Term3"),
        ]
        mock_deps["glossary"].get_tentative_by_thread.return_value = entries
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "CONFIRM", "reasoning": "Good"}'}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.entries_evaluated == 3
        assert result.confirmed == 3
        assert len(result.decisions) == 3
        assert mock_deps["agent"].chat.call_count == 3

    def test_merge_missing_target_confirms_instead(self, mock_deps):
        """Should confirm entry if MERGE target not found."""
        entry = _make_entry(1, "Term")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["glossary"].get.return_value = None  # Target not found
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "MERGE", "target_id": 999, "reasoning": "Merge"}'}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.merged == 1  # Still counts as merge attempt
        # Should have confirmed instead of crashing
        mock_deps["glossary"].update.assert_called()

    def test_revise_missing_definition_confirms_instead(self, mock_deps):
        """Should confirm entry if REVISE has no revised_definition."""
        entry = _make_entry(1, "Term")
        mock_deps["glossary"].get_tentative_by_thread.return_value = [entry]
        mock_deps["glossary"].search.return_value = []
        mock_deps["corpus"].get_adjacent_posts.return_value = []
        mock_deps["agent"].chat.return_value = Mock(
            message={"content": '{"action": "REVISE", "reasoning": "Needs update"}'}
        )

        curator = CuratorFork(**mock_deps)
        result = curator.run(thread_id=1)

        assert result.revised == 1
        # Should have confirmed with existing definition
        mock_deps["glossary"].update.assert_called_with(
            1, status="confirmed", post_id=0, thread_id=1
        )
