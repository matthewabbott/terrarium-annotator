"""High-level orchestration for the annotator harness."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from terrarium_annotator.agent_client import AgentClient, AgentClientError
from terrarium_annotator.context import AnnotationContext, TOOL_SYSTEM_PROMPT
from terrarium_annotator.corpus import CorpusReader, SceneBatcher
from terrarium_annotator.storage import GlossaryStore, ProgressTracker, RevisionHistory
from terrarium_annotator.tools import ToolDispatcher

if TYPE_CHECKING:
    from terrarium_annotator.corpus import Scene
    from terrarium_annotator.storage import GlossaryEntry, RunState

LOGGER = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    """Configuration for the annotation runner."""

    corpus_db_path: Path
    annotator_db_path: Path
    agent_url: str = "http://localhost:8080"
    temperature: float = 0.4
    max_tokens: int = 1024
    timeout: int = 120
    resume: bool = True
    max_tool_rounds: int = 10


@dataclass
class ToolStats:
    """Statistics from tool loop execution."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    tool_calls: int = 0
    rounds: int = 0


@dataclass
class RunResult:
    """Result of an annotation run."""

    scenes_processed: int
    posts_processed: int
    entries_created: int
    entries_updated: int
    entries_deleted: int
    tool_calls_total: int
    run_duration_seconds: float
    final_state: RunState = field(default=None)  # type: ignore[assignment]


class AnnotationRunner:
    """Iterates the corpus and drives the LLM-based annotator."""

    def __init__(self, config: RunnerConfig) -> None:
        self.config = config

        # Storage components (F0)
        self.glossary = GlossaryStore(config.annotator_db_path)
        self.revisions = RevisionHistory(config.annotator_db_path)
        self.progress = ProgressTracker(config.annotator_db_path)

        # Corpus components (F1)
        self.corpus = CorpusReader(config.corpus_db_path)
        self.batcher = SceneBatcher(self.corpus)

        # Context (F2)
        self.context = AnnotationContext(
            system_prompt=TOOL_SYSTEM_PROMPT,
            max_turns=12,
        )

        # Tools (F3)
        self.dispatcher = ToolDispatcher(
            glossary=self.glossary,
            corpus=self.corpus,
            revisions=self.revisions,
        )

        # Agent client
        self.agent = AgentClient(
            base_url=config.agent_url,
            timeout=config.timeout,
        )

    def close(self) -> None:
        """Close all database connections."""
        self.glossary.close()
        self.revisions.close()
        self.progress.close()
        self.corpus.close()

    def run(self, limit: int | None = None) -> RunResult:
        """
        Execute annotation loop.

        Args:
            limit: Maximum scenes to process (None = all).

        Returns:
            RunResult with stats and final state.
        """
        start_time = time.time()

        # Get checkpoint for resume
        state = self.progress.get_state()
        start_after_post_id = state.last_post_id if self.config.resume else None

        LOGGER.info(
            "Starting annotation run (resume=%s, start_after=%s)",
            self.config.resume,
            start_after_post_id,
        )

        # Mark run started
        self.progress.start_run()

        # Aggregate stats
        scenes_processed = 0
        total_posts = 0
        total_created = 0
        total_updated = 0
        total_deleted = 0
        total_tool_calls = 0
        current_thread_id: int | None = None

        # Scene iteration
        for scene in self.batcher.iter_scenes(start_after_post_id=start_after_post_id):
            # Thread boundary detection
            if current_thread_id != scene.thread_id:
                if current_thread_id is not None:
                    self.progress.update_thread_state(
                        current_thread_id, status="completed"
                    )
                    LOGGER.info("Thread %d completed", current_thread_id)

                current_thread_id = scene.thread_id
                self.progress.update_thread_state(
                    current_thread_id, status="in_progress"
                )
                LOGGER.info("Starting thread %d", current_thread_id)

            LOGGER.info(
                "Processing scene %d-%d (thread %d, %d posts)",
                scene.first_post_id,
                scene.last_post_id,
                scene.thread_id,
                scene.post_count,
            )

            # Search for relevant glossary entries
            relevant_entries = self._search_relevant_entries(scene)

            # Build messages
            messages = self.context.build_messages(
                current_scene=scene,
                relevant_entries=relevant_entries,
            )

            # Execute tool loop
            try:
                tool_stats = self._run_tool_loop(messages, scene)
            except AgentClientError as e:
                LOGGER.error("Agent call failed, stopping run: %s", e)
                break

            # Update progress
            self.progress.update(
                last_post_id=scene.last_post_id,
                last_thread_id=scene.thread_id,
                posts_processed_delta=scene.post_count,
                entries_created_delta=tool_stats.created,
                entries_updated_delta=tool_stats.updated,
            )

            # Aggregate stats
            scenes_processed += 1
            total_posts += scene.post_count
            total_created += tool_stats.created
            total_updated += tool_stats.updated
            total_deleted += tool_stats.deleted
            total_tool_calls += tool_stats.tool_calls

            LOGGER.info(
                "Scene complete: %d tool calls, +%d created, +%d updated",
                tool_stats.tool_calls,
                tool_stats.created,
                tool_stats.updated,
            )

            # Check limit
            if limit is not None and scenes_processed >= limit:
                LOGGER.info("Reached scene limit (%d)", limit)
                break

        # Mark final thread if any
        if current_thread_id is not None:
            # Only mark completed if we processed all scenes in the thread
            # For now, leave as in_progress since we might resume
            pass

        elapsed = time.time() - start_time
        final_state = self.progress.get_state()

        LOGGER.info(
            "Run complete: %d scenes, %d posts, %d entries created, %.1fs",
            scenes_processed,
            total_posts,
            total_created,
            elapsed,
        )

        return RunResult(
            scenes_processed=scenes_processed,
            posts_processed=total_posts,
            entries_created=total_created,
            entries_updated=total_updated,
            entries_deleted=total_deleted,
            tool_calls_total=total_tool_calls,
            run_duration_seconds=elapsed,
            final_state=final_state,
        )

    def _search_relevant_entries(self, scene: Scene) -> list[GlossaryEntry]:
        """Search glossary for entries relevant to the scene content."""
        # Use first post body as search query (truncated)
        if not scene.posts:
            return []

        # Combine scene text for search
        scene_text = " ".join(post.body or "" for post in scene.posts[:3])
        query = scene_text[:200]  # Truncate for FTS query

        if not query.strip():
            return []

        try:
            return self.glossary.search(query, limit=10)
        except Exception as e:
            LOGGER.warning("Glossary search failed: %s", e)
            return []

    def _run_tool_loop(
        self,
        messages: list[dict],
        scene: Scene,
    ) -> ToolStats:
        """
        Execute agent with tool calling until completion.

        Args:
            messages: Initial message list for agent.
            scene: Current scene being processed.

        Returns:
            ToolStats with counts of operations performed.
        """
        stats = ToolStats()
        tools = self.dispatcher.get_tool_definitions()
        current_messages = list(messages)  # Copy to preserve original

        for round_num in range(self.config.max_tool_rounds):
            stats.rounds = round_num + 1

            # Call agent
            response = self.agent.chat(
                messages=current_messages,
                tools=tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            message = response.message

            # Check for tool calls
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # No tool calls - agent is done
                content = message.get("content", "")
                if content:
                    self.context.record_turn("assistant", content)
                LOGGER.debug("Tool loop complete after %d rounds", stats.rounds)
                break

            # Record assistant message with tool calls
            assistant_content = message.get("content") or ""
            assistant_turn = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
            }
            current_messages.append(assistant_turn)

            # Dispatch each tool call
            for tool_call in tool_calls:
                stats.tool_calls += 1

                result = self.dispatcher.dispatch(
                    tool_call,
                    current_post_id=scene.last_post_id or 0,
                    current_thread_id=scene.thread_id,
                )

                # Track glossary modifications
                if result.success:
                    if result.tool_name == "glossary_create":
                        stats.created += 1
                    elif result.tool_name == "glossary_update":
                        stats.updated += 1
                    elif result.tool_name == "glossary_delete":
                        stats.deleted += 1

                # Add tool result to messages
                tool_message = {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": result.result,
                }
                current_messages.append(tool_message)

        else:
            # Exhausted max_tool_rounds
            LOGGER.warning(
                "Max tool rounds (%d) exceeded for scene %d-%d",
                self.config.max_tool_rounds,
                scene.first_post_id,
                scene.last_post_id,
            )

        return stats
