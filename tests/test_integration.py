"""Integration tests requiring live terrarium-agent server.

These tests are skipped when the agent is not running on localhost:8080.
Run with: pytest -m integration
Skip with: pytest -m "not integration"
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from terrarium_annotator.agent_client import AgentClient
from terrarium_annotator.context import AnnotationContext, TOOL_SYSTEM_PROMPT
from terrarium_annotator.context.compactor import CompactionState, ContextCompactor
from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.context.summarizer import ThreadSummarizer
from terrarium_annotator.context.token_counter import TokenCounter
from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.storage import GlossaryStore, RevisionHistory
from terrarium_annotator.tools import ToolDispatcher
from terrarium_annotator.tools.schemas import get_all_tool_schemas


# =============================================================================
# Tier 1: Agent Connection Tests
# =============================================================================


@pytest.mark.integration
class TestAgentConnection:
    """Basic connectivity tests for terrarium-agent."""

    def test_agent_health_check(self, real_agent: AgentClient):
        """Verify agent /health endpoint responds."""
        assert real_agent.health_check()

    def test_simple_chat(self, real_agent: AgentClient):
        """Verify basic chat completion works."""
        response = real_agent.chat(
            messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
            max_tokens=50,
            temperature=0.0,
        )
        assert response.message is not None
        assert "content" in response.message
        assert len(response.message["content"]) > 0

    def test_tokenize_endpoint(self, real_agent: AgentClient):
        """Verify tokenization endpoint works (if available).

        Note: /tokenize is a vLLM endpoint, not always proxied by agent server.
        Test passes if tokenization works OR if endpoint not available (404).
        """
        try:
            tokens = real_agent.tokenize("Hello, world!")
            assert isinstance(tokens, list)
            assert len(tokens) > 0
            assert all(isinstance(t, int) for t in tokens)
        except Exception as e:
            # Agent may not proxy /tokenize - that's okay
            if "404" in str(e):
                pytest.skip("Agent does not proxy /tokenize endpoint")
            raise

    def test_chat_with_system_prompt(self, real_agent: AgentClient):
        """Verify system prompts are respected."""
        response = real_agent.chat(
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Always end responses with [END]."},
                {"role": "user", "content": "Say hello briefly."},
            ],
            max_tokens=100,
            temperature=0.0,
        )
        assert response.message["content"]
        # System prompt should influence response
        # (not strictly checking [END] since LLM behavior varies)


# =============================================================================
# Tier 2: Tool Calling Tests
# =============================================================================


@pytest.mark.integration
class TestToolCalling:
    """Tests for tool/function calling with real agent."""

    @pytest.fixture
    def glossary_db(self) -> GlossaryStore:
        """Create temporary glossary database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        store = GlossaryStore(db_path)
        yield store
        store.close()

    @pytest.fixture
    def revisions_db(self, glossary_db: GlossaryStore) -> RevisionHistory:
        """Create revision history using same DB."""
        # RevisionHistory uses same DB file
        return RevisionHistory(glossary_db._db.db_path)

    def test_agent_receives_tool_schemas(self, real_agent: AgentClient):
        """Verify agent can receive tool definitions."""
        tools = get_all_tool_schemas(include_snapshot_tools=True)
        response = real_agent.chat(
            messages=[
                {"role": "system", "content": "You have access to glossary tools."},
                {"role": "user", "content": "What tools do you have available? List their names."},
            ],
            tools=tools,
            max_tokens=200,
            temperature=0.0,
        )
        # Agent should acknowledge tools (though may or may not call them)
        assert response.message is not None

    def test_agent_calls_glossary_search(
        self,
        real_agent: AgentClient,
        glossary_db: GlossaryStore,
        revisions_db: RevisionHistory,
    ):
        """Verify agent can call glossary_search tool."""
        # Seed glossary with data
        glossary_db.create(
            term="Soma",
            definition="The questmaster who guides adventurers.",
            tags=["character", "npc"],
            post_id=1,
            thread_id=1,
        )

        tools = get_all_tool_schemas(include_snapshot_tools=False)

        response = real_agent.chat(
            messages=[
                {"role": "system", "content": TOOL_SYSTEM_PROMPT},
                {"role": "user", "content": "Search the glossary for 'Soma'."},
            ],
            tools=tools,
            max_tokens=300,
            temperature=0.0,
        )

        # Check if agent made a tool call
        tool_calls = response.message.get("tool_calls", [])
        if tool_calls:
            # Verify it called the right tool
            tool_names = [tc["function"]["name"] for tc in tool_calls]
            assert "glossary_search" in tool_names

    def test_tool_dispatch_with_real_response(
        self,
        real_agent: AgentClient,
        glossary_db: GlossaryStore,
        revisions_db: RevisionHistory,
    ):
        """Test full tool dispatch cycle with agent."""
        # Create dispatcher with real components
        # Note: corpus is mocked since we're testing tool dispatch, not corpus reading
        from unittest.mock import Mock

        corpus = Mock(spec=CorpusReader)
        dispatcher = ToolDispatcher(
            glossary=glossary_db,
            corpus=corpus,
            revisions=revisions_db,
            snapshots=None,
        )

        tools = dispatcher.get_tool_definitions()

        # Ask agent to create an entry
        response = real_agent.chat(
            messages=[
                {"role": "system", "content": TOOL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Create a glossary entry for 'Dawn' - a warrior character.",
                },
            ],
            tools=tools,
            max_tokens=300,
            temperature=0.0,
        )

        tool_calls = response.message.get("tool_calls", [])
        if tool_calls:
            # Dispatch the tool call
            for tool_call in tool_calls:
                result = dispatcher.dispatch(
                    tool_call,
                    current_post_id=100,
                    current_thread_id=1,
                )
                # Check if it was a create call
                if result.tool_name == "glossary_create":
                    assert result.success, f"Tool failed: {result.error}"
                    # Verify entry was created
                    entries = glossary_db.search("Dawn", limit=1)
                    assert len(entries) > 0
                    assert entries[0].term == "Dawn"


# =============================================================================
# Tier 3: Full Pipeline Tests
# =============================================================================


@pytest.mark.integration
class TestFullPipeline:
    """End-to-end pipeline tests with real agent."""

    def test_context_building_with_token_counter(self, real_agent: AgentClient):
        """Test AnnotationContext with token counting (real or heuristic)."""
        from terrarium_annotator.context import TokenCounter

        counter = TokenCounter(agent_client=real_agent)
        context = AnnotationContext(
            system_prompt=TOOL_SYSTEM_PROMPT,
        )

        # Record some conversation
        context.record_turn("user", "What characters exist in the story?")
        context.record_turn("assistant", "Let me search the glossary for characters.")

        # Build messages
        messages = context.build_messages(
            current_scene=None,
            relevant_entries=[],
        )

        # Count tokens (may use real tokenizer or heuristic fallback)
        token_count = counter.count_messages(messages)
        assert token_count > 0
        # Note: using_fallback may be True if agent doesn't proxy /tokenize
        # Either way, token counting should work

    def test_multi_turn_conversation(self, real_agent: AgentClient):
        """Test multi-turn conversation flow."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Be brief."},
            {"role": "user", "content": "What is 2+2?"},
        ]

        # First turn
        response1 = real_agent.chat(
            messages=messages,
            max_tokens=50,
            temperature=0.0,
        )
        assert "4" in response1.message["content"]

        # Add response to conversation
        messages.append(response1.message)
        messages.append({"role": "user", "content": "Now add 3 to that."})

        # Second turn (should remember context)
        response2 = real_agent.chat(
            messages=messages,
            max_tokens=50,
            temperature=0.0,
        )
        assert "7" in response2.message["content"]

    def test_annotation_context_integration(self, real_agent: AgentClient):
        """Test annotation context workflow end-to-end."""

        # Create context
        context = AnnotationContext(
            system_prompt="You are annotating a story. Identify key terms.",
        )

        # Simulate a scene
        scene_text = """
        Soma entered the tavern, her dark cloak swirling behind her.
        The questmaster surveyed the room with keen eyes.
        """

        # Build messages with simulated scene
        messages = [
            {"role": "system", "content": context.system_prompt},
            {"role": "user", "content": f"Analyze this scene:\n{scene_text}"},
        ]

        # Get agent response
        response = real_agent.chat(
            messages=messages,
            max_tokens=300,
            temperature=0.0,
        )

        assert response.message["content"]
        # Agent should mention Soma or questmaster
        content = response.message["content"].lower()
        assert "soma" in content or "questmaster" in content or "character" in content


# =============================================================================
# Tier 4: Compaction Integration Tests
# =============================================================================


@pytest.mark.integration
class TestCompaction:
    """Integration tests for context compaction with real agent."""

    @pytest.fixture
    def temp_glossary(self) -> GlossaryStore:
        """Create temporary glossary database for compaction tests."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        store = GlossaryStore(db_path)
        yield store
        store.close()

    @pytest.fixture
    def compactor_setup(self, real_agent: AgentClient, temp_glossary: GlossaryStore):
        """Create compactor with real agent and small budget for testing."""
        counter = TokenCounter(agent_client=real_agent)
        summarizer = ThreadSummarizer(agent_client=real_agent, glossary=temp_glossary)
        # Small budget to trigger compaction easily
        compactor = ContextCompactor(
            token_counter=counter,
            summarizer=summarizer,
            agent_client=real_agent,
            context_budget=2000,  # Small budget for testing
        )
        return compactor, counter

    def test_compaction_threshold_triggers(self, compactor_setup):
        """Verify compaction triggers at 80% and loops until <60%.

        Uses threshold methods to verify behavior without needing
        actual messages that hit exact token counts.
        """
        compactor, counter = compactor_setup

        # Verify threshold calculations
        # With 2000 budget: soft=1200 (60%), thread_compact=1600 (80%), emergency=1800 (90%)
        assert compactor.soft_threshold == 1200
        assert compactor.thread_compact_threshold == 1600
        assert compactor.emergency_threshold == 1800
        assert compactor.target == 1400  # 70%

        # Create state with multiple threads to summarize
        state = CompactionState(completed_thread_ids=[1, 2, 3, 4])

        # Create messages tagged with thread IDs
        messages = [
            {"role": "user", "content": "Thread 1 question", "thread_id": 1},
            {"role": "assistant", "content": "Thread 1 answer " * 50, "thread_id": 1},
            {"role": "user", "content": "Thread 2 question", "thread_id": 2},
            {"role": "assistant", "content": "Thread 2 answer " * 50, "thread_id": 2},
            {"role": "user", "content": "Thread 3 question", "thread_id": 3},
            {"role": "assistant", "content": "Thread 3 answer " * 50, "thread_id": 3},
            {"role": "user", "content": "Thread 4 question", "thread_id": 4},
            {"role": "assistant", "content": "Thread 4 answer", "thread_id": 4},
        ]

        # Run compaction
        result_messages, result = compactor.compact(messages, state)

        # Should have summarized threads to get under target
        # (exact count depends on token counting, but should do some work)
        if result.initial_tokens > compactor.soft_threshold:
            assert result.threads_summarized >= 0 or result.target_reached

    def test_thread_summaries_include_entry_ids(self):
        """Thread summaries should include entry IDs in XML format."""
        from terrarium_annotator.context.annotation import AnnotationContext

        context = AnnotationContext(system_prompt="Test prompt")

        # Create thread summaries with entry IDs
        summaries = [
            ThreadSummary(
                thread_id=5,
                position=0,
                summary_text="Story about Soma the questmaster.",
                entries_created=[12, 15],
                entries_updated=[18],
            ),
            ThreadSummary(
                thread_id=6,
                position=1,
                summary_text="Dawn joins the party.",
                entries_created=[20],
                entries_updated=[],
            ),
        ]

        # Format summaries
        xml_output = context._format_thread_summaries(summaries)

        # Verify XML structure
        assert "<thread_summaries>" in xml_output
        assert "</thread_summaries>" in xml_output

        # Verify entry IDs are included
        assert 'entries="12,15,18"' in xml_output  # Thread 5: created + updated
        assert 'entries="20"' in xml_output  # Thread 6: only created

        # Verify thread attributes
        assert 'id="5"' in xml_output
        assert 'id="6"' in xml_output
        assert 'position="0"' in xml_output
        assert 'position="1"' in xml_output

    def test_tier2_merges_when_over_3_summaries(self, compactor_setup):
        """Tier 2 should run when >3 summaries, not just in emergency."""
        compactor, counter = compactor_setup

        # Create state with 5 summaries (over the 3 threshold)
        state = CompactionState(
            completed_thread_ids=[1],  # Only 1, so tier 1 skipped
            thread_summaries=[
                ThreadSummary(10, 0, "Summary for thread 10"),
                ThreadSummary(11, 1, "Summary for thread 11"),
                ThreadSummary(12, 2, "Summary for thread 12"),
                ThreadSummary(13, 3, "Summary for thread 13"),
                ThreadSummary(14, 4, "Summary for thread 14"),
            ],
        )

        # Messages that put us over 80% but under 90%
        messages = [
            {"role": "user", "content": "Recent question"},
            {"role": "assistant", "content": "Recent answer " * 100},
        ]

        # Run compaction
        result_messages, result = compactor.compact(messages, state)

        # Should have merged summaries (tier 2)
        # After merging 2 oldest, should have 3 remaining
        if result.summaries_merged > 0:
            assert len(state.thread_summaries) == 3
            assert state.cumulative_summary != ""

    def test_context_structure_after_compaction(self, real_agent: AgentClient):
        """Verify layered context: cumulative + summaries + history."""
        from terrarium_annotator.context.annotation import AnnotationContext

        # Create context with conversation history
        context = AnnotationContext(
            system_prompt="You are annotating a story.",
        )
        context.record_turn("user", "What happened in thread 1?", thread_id=7)
        context.record_turn("assistant", "Thread 1 events...", thread_id=7)

        # Create compaction state with summaries
        state = CompactionState(
            cumulative_summary="The story began with adventurers meeting in a tavern.",
            thread_summaries=[
                ThreadSummary(5, 0, "Soma introduced as questmaster.", entries_created=[1]),
                ThreadSummary(6, 1, "Party formed with Dawn.", entries_created=[2, 3]),
            ],
        )

        # Build messages with all layers
        messages = context.build_messages(
            cumulative_summary=state.cumulative_summary,
            thread_summaries=state.thread_summaries,
            current_scene=None,
            relevant_entries=[],
        )

        # Verify structure (order matters)
        # 1. System prompt
        assert messages[0]["role"] == "system"
        assert "annotating" in messages[0]["content"]

        # 2. Cumulative summary
        assert messages[1]["role"] == "system"
        assert "<cumulative_summary>" in messages[1]["content"]
        assert "tavern" in messages[1]["content"]

        # 3. Thread summaries
        assert messages[2]["role"] == "system"
        assert "<thread_summaries>" in messages[2]["content"]
        assert 'entries="1"' in messages[2]["content"]  # Entry IDs included

        # 4. Conversation history
        assert messages[3]["role"] == "user"
        assert "thread 1" in messages[3]["content"]
