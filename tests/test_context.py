"""Tests for the context layer."""

from unittest.mock import Mock

import pytest

from terrarium_annotator.context import (
    NewAnnotationContext,
    SYSTEM_PROMPT,
    ThreadSummary,
    TokenCounter,
)
from terrarium_annotator.corpus import Scene, StoryPost
from terrarium_annotator.storage import GlossaryEntry


class TestTokenCounter:
    """Token counter tests - no live vLLM needed."""

    def test_heuristic_mode_when_no_client(self):
        """Should use heuristic when no agent_client provided."""
        counter = TokenCounter(agent_client=None, chars_per_token=4.0)

        assert counter.using_fallback is True
        # 11 chars / 4 = 2.75 -> 2 (int truncation)
        assert counter.count("hello world") == 2

    def test_heuristic_minimum_one_token(self):
        """Empty or tiny strings should return at least 1 token."""
        counter = TokenCounter(agent_client=None)

        assert counter.count("") == 1
        assert counter.count("a") == 1

    def test_heuristic_with_custom_ratio(self):
        """Should use custom chars_per_token ratio."""
        counter = TokenCounter(agent_client=None, chars_per_token=2.0)

        # 10 chars / 2 = 5
        assert counter.count("0123456789") == 5

    def test_vllm_success_uses_api(self):
        """Should use vLLM when available and working."""
        mock_client = Mock()
        mock_client.tokenize.return_value = [1, 2, 3, 4, 5]

        counter = TokenCounter(agent_client=mock_client)

        result = counter.count("hello world")

        assert result == 5
        assert counter.using_fallback is False
        mock_client.tokenize.assert_called_once_with("hello world")

    def test_vllm_failure_falls_back(self):
        """Should fall back to heuristic on vLLM error."""
        mock_client = Mock()
        mock_client.tokenize.side_effect = Exception("Connection refused")

        counter = TokenCounter(agent_client=mock_client, chars_per_token=4.0)

        result = counter.count("hello world")

        # Heuristic: 11 / 4 = 2.75 -> 2
        assert result == 2
        assert counter.using_fallback is True

    def test_vllm_fallback_is_sticky(self):
        """Once in fallback mode, should stay in fallback."""
        mock_client = Mock()
        mock_client.tokenize.side_effect = Exception("Connection refused")

        counter = TokenCounter(agent_client=mock_client, chars_per_token=4.0)

        # First call triggers fallback
        counter.count("hello")
        assert counter.using_fallback is True

        # Subsequent call should not try vLLM
        mock_client.tokenize.reset_mock()
        counter.count("world")
        mock_client.tokenize.assert_not_called()

    def test_count_messages_includes_overhead(self):
        """Should add per-message overhead."""
        counter = TokenCounter(agent_client=None, chars_per_token=1.0)

        messages = [
            {"role": "system", "content": "Be helpful."},  # 11 chars
            {"role": "user", "content": "Hi"},  # 2 chars
        ]

        result = counter.count_messages(messages)

        # 11 + 4 overhead + 2 + 4 overhead = 21
        assert result == 21

    def test_count_messages_with_tool_calls(self):
        """Should include tool call overhead."""
        counter = TokenCounter(agent_client=None, chars_per_token=1.0)

        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "search",  # 6 chars
                            "arguments": '{"q":"test"}',  # 12 chars
                        }
                    }
                ],
            }
        ]

        result = counter.count_messages(messages)

        # 0 content + 4 overhead + 6 name + 12 args + 10 tool overhead = 32
        assert result == 32


class TestThreadSummary:
    """ThreadSummary dataclass tests."""

    def test_dataclass_fields(self):
        ts = ThreadSummary(
            thread_id=5,
            position=2,
            summary_text="Thread 5 covered major battles.",
            entries_created=[1, 2, 3],
            entries_updated=[4],
        )

        assert ts.thread_id == 5
        assert ts.position == 2
        assert ts.summary_text == "Thread 5 covered major battles."
        assert len(ts.entries_created) == 3
        assert len(ts.entries_updated) == 1

    def test_default_empty_lists(self):
        ts = ThreadSummary(
            thread_id=1,
            position=0,
            summary_text="Summary",
        )

        assert ts.entries_created == []
        assert ts.entries_updated == []


class TestAnnotationContext:
    """NewAnnotationContext tests."""

    @pytest.fixture
    def sample_scene(self) -> Scene:
        return Scene(
            thread_id=1,
            posts=[
                StoryPost(
                    post_id=100,
                    thread_id=1,
                    body="The warrior drew his sword.",
                    author="QM",
                    created_at=None,
                    tags=["qm_post"],
                ),
            ],
            is_thread_start=True,
            is_thread_end=False,
            scene_index=0,
        )

    @pytest.fixture
    def sample_entry(self) -> GlossaryEntry:
        return GlossaryEntry(
            id=1,
            term="Soma",
            term_normalized="soma",
            definition="The questmaster.",
            status="confirmed",
            tags=["character"],
            first_seen_post_id=1,
            first_seen_thread_id=1,
            last_updated_post_id=1,
            last_updated_thread_id=1,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

    def test_build_messages_includes_system_prompt(self, sample_scene):
        ctx = NewAnnotationContext(system_prompt="Be an annotator.")

        messages = ctx.build_messages(current_scene=sample_scene)

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be an annotator."

    def test_build_messages_includes_scene_posts(self, sample_scene):
        ctx = NewAnnotationContext(system_prompt="Annotate.")

        messages = ctx.build_messages(current_scene=sample_scene)

        user_msg = messages[-1]["content"]
        assert "<story_passages>" in user_msg
        assert 'id="100"' in user_msg
        assert "The warrior drew his sword." in user_msg

    def test_build_messages_includes_entries(self, sample_scene, sample_entry):
        ctx = NewAnnotationContext(system_prompt="Annotate.")

        messages = ctx.build_messages(
            current_scene=sample_scene,
            relevant_entries=[sample_entry],
        )

        user_msg = messages[-1]["content"]
        assert "<known_glossary>" in user_msg
        assert 'name="Soma"' in user_msg
        assert 'tags="character"' in user_msg

    def test_build_messages_includes_cumulative_summary(self, sample_scene):
        ctx = NewAnnotationContext(system_prompt="Annotate.")

        messages = ctx.build_messages(
            current_scene=sample_scene,
            cumulative_summary="Previously: battles were fought.",
        )

        # Should have cumulative summary in a system message
        assert any("<cumulative_summary>" in m.get("content", "") for m in messages)

    def test_build_messages_includes_thread_summaries(self, sample_scene):
        ctx = NewAnnotationContext(system_prompt="Annotate.")
        summaries = [
            ThreadSummary(
                thread_id=1, position=0, summary_text="First thread summary."
            ),
            ThreadSummary(
                thread_id=2, position=1, summary_text="Second thread summary."
            ),
        ]

        messages = ctx.build_messages(
            current_scene=sample_scene,
            thread_summaries=summaries,
        )

        # Should have thread summaries in a system message
        assert any("<thread_summaries>" in m.get("content", "") for m in messages)
        assert any('id="1"' in m.get("content", "") for m in messages)

    def test_record_turn_user(self):
        ctx = NewAnnotationContext(system_prompt="Test")

        ctx.record_turn("user", "Hello")

        assert ctx.conversation_history == [{"role": "user", "content": "Hello"}]

    def test_record_turn_assistant(self):
        ctx = NewAnnotationContext(system_prompt="Test")

        ctx.record_turn("assistant", "Hi there!")

        assert ctx.conversation_history == [
            {"role": "assistant", "content": "Hi there!"}
        ]

    def test_record_turn_tool_with_id(self):
        ctx = NewAnnotationContext(system_prompt="Test")

        ctx.record_turn("tool", "Result", tool_call_id="call_123")

        assert ctx.conversation_history == [
            {"role": "tool", "content": "Result", "tool_call_id": "call_123"}
        ]

    def test_get_history_returns_copy(self):
        ctx = NewAnnotationContext(system_prompt="Test")
        ctx.record_turn("user", "Hi")

        history = ctx.get_history()
        history.append({"role": "fake", "content": "injected"})

        # Original should be unaffected
        assert len(ctx.conversation_history) == 1

    def test_clone_creates_independent_copy(self):
        ctx = NewAnnotationContext(system_prompt="Test", max_turns=5)
        ctx.record_turn("user", "Original")

        clone = ctx.clone()
        clone.record_turn("user", "Cloned only")

        assert len(ctx.conversation_history) == 1
        assert len(clone.conversation_history) == 2
        assert clone.system_prompt == "Test"
        assert clone.max_turns == 5

    def test_clone_deep_copies_history(self):
        ctx = NewAnnotationContext(system_prompt="Test")
        ctx.conversation_history = [
            {"role": "user", "content": "Hi", "metadata": {"key": "value"}}
        ]

        clone = ctx.clone()
        clone.conversation_history[0]["metadata"]["key"] = "modified"

        # Original should be unaffected
        assert ctx.conversation_history[0]["metadata"]["key"] == "value"

    def test_max_turns_limits_history(self, sample_scene):
        ctx = NewAnnotationContext(system_prompt="Test", max_turns=2)

        for i in range(5):
            ctx.record_turn("user", f"Message {i}")
            ctx.record_turn("assistant", f"Response {i}")

        messages = ctx.build_messages(current_scene=sample_scene)

        # Count turns in messages (excluding system messages and final user payload)
        turn_count = sum(
            1
            for m in messages
            if m["role"] in ("user", "assistant")
            and "<story_passages>" not in m.get("content", "")
        )
        assert turn_count == 2

    def test_build_messages_without_scene(self):
        """Should work without a scene (no user message added)."""
        ctx = NewAnnotationContext(system_prompt="Test")
        ctx.record_turn("user", "Previous message")

        messages = ctx.build_messages()

        assert len(messages) == 2  # system + 1 history turn
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Previous message"


class TestSystemPrompt:
    """Test SYSTEM_PROMPT constant."""

    def test_system_prompt_exists(self):
        assert SYSTEM_PROMPT is not None
        assert len(SYSTEM_PROMPT) > 0

    def test_system_prompt_mentions_codex(self):
        assert "codex" in SYSTEM_PROMPT.lower()


class TestLegacyContext:
    """Test backwards compatibility with old context.py API."""

    def test_legacy_context_works_without_system_prompt(self):
        """Legacy AnnotationContext should work without system_prompt arg."""
        from terrarium_annotator.context import AnnotationContext

        ctx = AnnotationContext(max_turns=5)

        assert ctx.max_turns == 5
        assert ctx.system_prompt == SYSTEM_PROMPT

    def test_legacy_build_messages_signature(self):
        """Legacy build_messages should accept (posts, codex_entries)."""
        from terrarium_annotator.context import AnnotationContext

        ctx = AnnotationContext()
        posts = [
            StoryPost(
                post_id=1,
                thread_id=1,
                body="Test post",
                author="QM",
                created_at=None,
                tags=[],
            )
        ]

        messages, payload = ctx.build_messages(posts, [])

        assert isinstance(messages, list)
        assert isinstance(payload, str)
        assert "<story_passages>" in payload

    def test_legacy_record_methods(self):
        """Legacy record_user_turn and record_assistant_turn should work."""
        from terrarium_annotator.context import AnnotationContext

        ctx = AnnotationContext()
        ctx.record_user_turn("Hello")
        ctx.record_assistant_turn("Hi there")

        assert len(ctx.conversation_history) == 2
        assert ctx.conversation_history[0]["role"] == "user"
        assert ctx.conversation_history[1]["role"] == "assistant"

    def test_legacy_summary_property(self):
        """Legacy context should have summary property."""
        from terrarium_annotator.context import AnnotationContext

        ctx = AnnotationContext(summary="Previous context")

        assert ctx.summary == "Previous context"
        ctx.summary = "Updated summary"
        assert ctx.summary == "Updated summary"

    def test_new_annotation_context_requires_system_prompt(self):
        """NewAnnotationContext should require system_prompt."""
        from terrarium_annotator.context import NewAnnotationContext

        # Should work with system_prompt
        ctx = NewAnnotationContext(system_prompt="Test prompt")
        assert ctx.system_prompt == "Test prompt"

        # Should fail without system_prompt
        import pytest

        with pytest.raises(TypeError):
            NewAnnotationContext()  # type: ignore
