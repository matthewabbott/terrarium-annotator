"""High-level orchestration for the annotator harness."""

from __future__ import annotations

import logging
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from terrarium_annotator.agent_client import AgentClient, AgentClientError
from terrarium_annotator.context import (
    AnnotationContext,
    CompactionState,
    ContextCompactor,
    ThreadSummarizer,
    TokenCounter,
    TOOL_SYSTEM_PROMPT,
)
from terrarium_annotator.corpus import CorpusReader, SceneBatcher
from terrarium_annotator.curator import CuratorFork
from terrarium_annotator.storage import (
    GlossaryStore,
    ProgressTracker,
    RevisionHistory,
    SnapshotStore,
)
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
    # Context settings
    # Compaction settings (F5)
    context_budget: int = 98304  # ~98K tokens for long-context annotation
    thread_compact_ratio: float = 0.80  # Summarize thread at thread boundary
    emergency_ratio: float = 0.90  # Emergency compact mid-scene
    target_ratio: float = 0.70
    # Curator settings (F6)
    enable_curator: bool = True
    # Snapshot settings (F7)
    enable_snapshots: bool = True


@dataclass
class ToolStats:
    """Statistics from tool loop execution."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    tool_calls: int = 0
    rounds: int = 0
    inference_time: float = 0.0


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

        # Snapshots (F7)
        self.snapshots: SnapshotStore | None = None
        if config.enable_snapshots:
            self.snapshots = SnapshotStore(config.annotator_db_path)
        self._thread_position = 0  # Track thread ordinal for snapshots

        # Corpus components (F1)
        self.corpus = CorpusReader(config.corpus_db_path)
        self.batcher = SceneBatcher(self.corpus)

        # Context (F2)
        self.context = AnnotationContext(
            system_prompt=TOOL_SYSTEM_PROMPT,
        )

        # Tools (F3)
        self.dispatcher = ToolDispatcher(
            glossary=self.glossary,
            corpus=self.corpus,
            revisions=self.revisions,
            snapshots=self.snapshots,
        )

        # Agent client
        self.agent = AgentClient(
            base_url=config.agent_url,
            timeout=config.timeout,
        )

        # Compaction (F5)
        self.token_counter = TokenCounter(agent_client=self.agent)
        self.summarizer = ThreadSummarizer(
            agent_client=self.agent,
            glossary=self.glossary,
        )
        self.compactor = ContextCompactor(
            token_counter=self.token_counter,
            summarizer=self.summarizer,
            agent_client=self.agent,
            context_budget=config.context_budget,
            thread_compact_ratio=config.thread_compact_ratio,
            emergency_ratio=config.emergency_ratio,
            target_ratio=config.target_ratio,
        )
        self.compaction_state = CompactionState()

        # Curator (F6)
        self.curator = CuratorFork(
            glossary=self.glossary,
            corpus=self.corpus,
            revisions=self.revisions,
            agent=self.agent,
        )

        # Graceful shutdown support
        self._shutdown_requested = False
        self._original_sigint: Any = None
        self._original_sigterm: Any = None

    def close(self) -> None:
        """Close all database connections."""
        self.glossary.close()
        self.revisions.close()
        self.progress.close()
        self.corpus.close()
        if self.snapshots is not None:
            self.snapshots.close()

    def _install_signal_handlers(self) -> None:
        """Install graceful shutdown signal handlers."""
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _remove_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        """Handle shutdown signal - set flag to stop after current scene."""
        if self._shutdown_requested:
            LOGGER.warning("Force quit requested - exiting immediately")
            raise KeyboardInterrupt
        self._shutdown_requested = True
        LOGGER.info("Shutdown requested - will exit after current scene completes")

    def _create_shutdown_checkpoint(self, thread_id: int, last_post_id: int) -> None:
        """Create checkpoint on graceful shutdown."""
        if self.snapshots is None:
            return

        LOGGER.info("Shutdown: creating checkpoint snapshot...")
        try:
            snapshot_id = self.snapshots.create(
                snapshot_type="checkpoint",
                last_post_id=last_post_id,
                last_thread_id=thread_id,
                thread_position=self._thread_position,
                context=self.context,
                compaction_state=self.compaction_state,
                glossary=self.glossary,
                metadata={"shutdown": True},
            )
            LOGGER.info("Shutdown: created checkpoint snapshot %d", snapshot_id)
        except Exception as e:
            LOGGER.warning("Shutdown: failed to create checkpoint: %s", e)

    def run(self, limit: int | None = None) -> RunResult:
        """
        Execute annotation loop.

        Args:
            limit: Maximum scenes to process (None = all).

        Returns:
            RunResult with stats and final state.
        """
        start_time = time.time()

        # Install graceful shutdown handlers
        self._install_signal_handlers()

        # Get checkpoint for resume
        # SQLite WAL ensures last_post_id reflects last fully-completed scene
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
        total_inference_time = 0.0
        current_thread_id: int | None = None
        current_scene_index: int = 0  # 0-indexed within current thread

        # Scene iteration
        for scene in self.batcher.iter_scenes(start_after_post_id=start_after_post_id):
            # Thread boundary detection
            if current_thread_id != scene.thread_id:
                if current_thread_id is not None:
                    self.progress.update_thread_state(
                        current_thread_id, status="completed"
                    )
                    # Track for compaction (F5) - oldest threads will be summarized
                    # during rolling compaction in _run_tool_loop when thresholds hit
                    self.compaction_state.completed_thread_ids.append(current_thread_id)
                    LOGGER.info(
                        "Thread %d completed (now %d completed threads tracked)",
                        current_thread_id,
                        len(self.compaction_state.completed_thread_ids),
                    )

                    # Run curator evaluation (F6)
                    if self.config.enable_curator:
                        try:
                            curator_result = self.curator.run(current_thread_id)
                            if curator_result.entries_evaluated > 0:
                                LOGGER.info(
                                    "Curator: %d evaluated, %d confirmed, %d rejected, %d merged, %d revised",
                                    curator_result.entries_evaluated,
                                    curator_result.confirmed,
                                    curator_result.rejected,
                                    curator_result.merged,
                                    curator_result.revised,
                                )
                        except Exception as e:
                            LOGGER.warning("Curator evaluation failed: %s", e)

                    # Create checkpoint snapshot (F7) - after curator for clean state
                    self._create_checkpoint(
                        thread_id=current_thread_id,
                        last_post_id=scene.first_post_id
                        - 1,  # Last post of completed thread
                    )

                current_thread_id = scene.thread_id
                current_scene_index = 0  # Reset scene counter for new thread
                self._thread_position += 1  # Increment for snapshot ordering (F7)
                # Initialize chunk tracking for new thread
                self.compaction_state.start_new_thread(current_thread_id)
                self.progress.update_thread_state(
                    current_thread_id, status="in_progress"
                )
                LOGGER.info(
                    "Starting thread %d (position %d)",
                    current_thread_id,
                    self._thread_position,
                )

            LOGGER.info(
                "Processing scene %d-%d (thread %d, %d posts)",
                scene.first_post_id,
                scene.last_post_id,
                scene.thread_id,
                scene.post_count,
            )

            scene_start_time = time.time()

            # Search for relevant glossary entries
            relevant_entries = self._search_relevant_entries(scene)

            # Build messages with compaction state (F5)
            messages = self.context.build_messages(
                current_scene=scene,
                relevant_entries=relevant_entries,
                cumulative_summary=self.compaction_state.cumulative_summary or None,
                thread_summaries=self.compaction_state.thread_summaries or None,
                chunk_summaries=self.compaction_state.chunk_summaries or None,
            )

            # Log context usage metrics
            tokens, usage_pct = self.compactor.get_current_usage(messages)
            self.compactor.stats.record_usage(usage_pct)

            LOGGER.info(
                "Context: %d/%d tokens (%.1f%%) | scene %d-%d",
                tokens,
                self.compactor.budget,
                usage_pct,
                scene.first_post_id,
                scene.last_post_id,
            )

            # Execute tool loop
            try:
                tool_stats = self._run_tool_loop(messages, scene, current_scene_index)
            except AgentClientError as e:
                LOGGER.error("Agent call failed, stopping run: %s", e)
                break

            # Advance scene tracking for chunk compaction
            current_scene_index += 1
            self.compaction_state.advance_scene()

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
            total_inference_time += tool_stats.inference_time

            # Calculate scene timing
            scene_duration = time.time() - scene_start_time

            # Fetch vLLM KV cache usage after inference
            metrics = self.agent.get_metrics()
            kv_cache_pct = metrics.get("vllm_kv_cache_pct", 0.0) * 100

            LOGGER.info(
                "Scene complete: %.1fs (inference: %.1fs) | %d calls, +%d/~%d | KV: %.1f%%",
                scene_duration,
                tool_stats.inference_time,
                tool_stats.tool_calls,
                tool_stats.created,
                tool_stats.updated,
                kv_cache_pct,
            )

            # Check limit
            if limit is not None and scenes_processed >= limit:
                LOGGER.info("Reached scene limit (%d)", limit)
                break

            # Check for graceful shutdown
            if self._shutdown_requested:
                LOGGER.info(
                    "Shutdown: completed scene %d-%d, exiting",
                    scene.first_post_id,
                    scene.last_post_id,
                )
                # Create checkpoint if we're mid-thread
                if current_thread_id is not None:
                    self._create_shutdown_checkpoint(
                        current_thread_id, scene.last_post_id
                    )
                break

        # Mark final thread if any
        if current_thread_id is not None:
            # Only mark completed if we processed all scenes in the thread
            # For now, leave as in_progress since we might resume
            pass

        elapsed = time.time() - start_time
        final_state = self.progress.get_state()

        LOGGER.info(
            "Run complete: %d scenes, %d posts, %d entries created, %.1fs (inference: %.1fs, %.0f%%)",
            scenes_processed,
            total_posts,
            total_created,
            elapsed,
            total_inference_time,
            (total_inference_time / elapsed * 100) if elapsed > 0 else 0,
        )

        # Log compaction summary
        LOGGER.info("Compaction stats: %s", self.compactor.stats.summary())

        # Restore original signal handlers
        self._remove_signal_handlers()

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

    def _create_checkpoint(self, thread_id: int, last_post_id: int) -> None:
        """
        Create a checkpoint snapshot after thread completion.

        Args:
            thread_id: Completed thread ID.
            last_post_id: Last post ID processed.
        """
        if self.snapshots is None:
            return

        try:
            snapshot_id = self.snapshots.create(
                snapshot_type="checkpoint",
                last_post_id=last_post_id,
                last_thread_id=thread_id,
                thread_position=self._thread_position,
                context=self.context,
                compaction_state=self.compaction_state,
                glossary=self.glossary,
            )
            LOGGER.info(
                "Created checkpoint snapshot %d for thread %d",
                snapshot_id,
                thread_id,
            )
        except Exception as e:
            LOGGER.warning("Failed to create checkpoint snapshot: %s", e)

    def _search_relevant_entries(self, scene: Scene) -> list[GlossaryEntry]:
        """Search glossary for entries relevant to the scene content."""
        # Use first post body as search query (truncated)
        if not scene.posts:
            return []

        # Combine scene text for search - extract key words only
        scene_text = " ".join(post.body or "" for post in scene.posts[:3])
        # Sanitize for FTS5: keep only alphanumeric and spaces
        words = re.findall(r"\b[a-zA-Z]{3,}\b", scene_text)
        query = " ".join(words[:20])  # Use first 20 words

        if not query.strip():
            return []

        try:
            # Wrap in quotes for safe phrase search
            safe_query = query.replace('"', "")
            return self.glossary.search(f'"{safe_query}"', limit=10)
        except Exception as e:
            LOGGER.warning("Glossary search failed: %s", e)
            return []

    def _run_tool_loop(
        self,
        messages: list[dict],
        scene: Scene,
        scene_index: int,
    ) -> ToolStats:
        """
        Execute agent with tool calling until completion.

        Args:
            messages: Initial message list for agent.
            scene: Current scene being processed.
            scene_index: 0-indexed scene number within current thread for chunk compaction.

        Returns:
            ToolStats with counts of operations performed.
        """
        stats = ToolStats()
        tools = self.dispatcher.get_tool_definitions()
        current_messages = list(messages)  # Copy to preserve original
        initial_len = len(messages)  # Track where new messages start

        # Record the user message (scene content) to conversation history
        # Tag with thread_id and scene_index for later compaction filtering
        if messages:
            user_msg = messages[-1]  # Last message is user payload
            if user_msg.get("role") == "user":
                self.context.record_turn(
                    "user",
                    user_msg["content"],
                    thread_id=scene.thread_id,
                    scene_index=scene_index,
                )

        for round_num in range(self.config.max_tool_rounds):
            stats.rounds = round_num + 1

            # Check compaction before agent call (F5)
            # Rolling compaction: summarize oldest threads, preserve recent context
            if self.compactor.should_compact(current_messages):
                current_messages, compact_result = self.compactor.compact(
                    current_messages, self.compaction_state, self.context
                )
                LOGGER.info(
                    "Compacted: %d -> %d tokens (target reached: %s)",
                    compact_result.initial_tokens,
                    compact_result.final_tokens,
                    compact_result.target_reached,
                )

            # Call agent
            response = self.agent.chat(
                messages=current_messages,
                tools=tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            stats.inference_time += response.inference_duration_seconds
            LOGGER.info(
                "Inference: %.2fs (round %d)",
                response.inference_duration_seconds,
                round_num + 1,
            )

            message = response.message

            # Check for tool calls
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # No tool calls - agent is done
                # Record final assistant message if present
                content = message.get("content", "")
                if content:
                    current_messages.append({"role": "assistant", "content": content})
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

        # Sync all new messages (assistant + tool) to conversation history
        # Tag with thread_id and scene_index for later compaction filtering
        for msg in current_messages[initial_len:]:
            if msg["role"] in ("assistant", "tool"):
                msg["thread_id"] = scene.thread_id
                msg["scene_index"] = scene_index
                self.context.conversation_history.append(msg)

        return stats
