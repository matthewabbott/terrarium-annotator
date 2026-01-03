"""Context compaction for managing token budget."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from terrarium_annotator.context.metrics import CompactionStats
from terrarium_annotator.context.models import ChunkSummary, ThreadSummary
from terrarium_annotator.context.prompts import CUMULATIVE_SUMMARY_PROMPT

if TYPE_CHECKING:
    from terrarium_annotator.agent_client import AgentClient
    from terrarium_annotator.context.annotation import AnnotationContext
    from terrarium_annotator.context.summarizer import ThreadSummarizer
    from terrarium_annotator.context.token_counter import TokenCounter

LOGGER = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    """Result of compaction operation."""

    initial_tokens: int
    final_tokens: int
    chunks_summarized: int
    threads_summarized: int
    summaries_merged: int
    turns_trimmed: int
    responses_truncated: int
    target_reached: bool
    highest_tier: float = 0  # 0 = no compaction, 0.5/1/2/3/4 = tier used


@dataclass
class CompactionState:
    """Mutable state for compaction tracking."""

    cumulative_summary: str = ""
    thread_summaries: list[ThreadSummary] = field(default_factory=list)
    chunk_summaries: list[ChunkSummary] = field(default_factory=list)
    completed_thread_ids: list[int] = field(default_factory=list)
    # Track current thread for chunk compaction
    current_thread_id: int | None = None
    current_scene_index: int = 0  # 0-indexed within current thread
    # Track which chunks have been summarized (indices)
    summarized_chunk_indices: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict for snapshot storage."""
        return {
            "cumulative_summary": self.cumulative_summary,
            "thread_summaries": [ts.to_dict() for ts in self.thread_summaries],
            "chunk_summaries": [cs.to_dict() for cs in self.chunk_summaries],
            "completed_thread_ids": self.completed_thread_ids,
            "current_thread_id": self.current_thread_id,
            "current_scene_index": self.current_scene_index,
            "summarized_chunk_indices": self.summarized_chunk_indices,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CompactionState:
        """Reconstruct from snapshot data."""
        state = cls()
        state.cumulative_summary = data.get("cumulative_summary", "")
        state.thread_summaries = [
            ThreadSummary.from_dict(ts) for ts in data.get("thread_summaries", [])
        ]
        state.chunk_summaries = [
            ChunkSummary.from_dict(cs) for cs in data.get("chunk_summaries", [])
        ]
        state.completed_thread_ids = data.get("completed_thread_ids", [])
        state.current_thread_id = data.get("current_thread_id")
        state.current_scene_index = data.get("current_scene_index", 0)
        state.summarized_chunk_indices = data.get("summarized_chunk_indices", [])
        return state

    def start_new_thread(self, thread_id: int) -> None:
        """Start tracking a new thread, clearing chunk state."""
        self.current_thread_id = thread_id
        self.current_scene_index = 0
        self.chunk_summaries = []
        self.summarized_chunk_indices = []

    def advance_scene(self) -> None:
        """Advance to next scene in current thread."""
        self.current_scene_index += 1

    def get_completed_chunk_count(self, scenes_per_chunk: int) -> int:
        """Get number of completed chunks (full groups of N scenes)."""
        return self.current_scene_index // scenes_per_chunk

    def get_unsummarized_chunks(
        self, scenes_per_chunk: int
    ) -> list[tuple[int, int, int]]:
        """Get list of (chunk_index, first_scene, last_scene) for unsummarized chunks."""
        completed_chunks = self.get_completed_chunk_count(scenes_per_chunk)
        unsummarized = []
        for i in range(completed_chunks):
            if i not in self.summarized_chunk_indices:
                first = i * scenes_per_chunk
                last = first + scenes_per_chunk - 1
                unsummarized.append((i, first, last))
        return unsummarized


class ContextCompactor:
    """
    Rolling context compaction with smart thresholds.

    Context structure after compaction:
    ┌─────────────────────────────────────────────────────────────┐
    │ cumulative_summary: "The Story So Far" (all past threads)   │
    ├─────────────────────────────────────────────────────────────┤
    │ chunk_summaries: [Chunk 0, 1, 2...] (for current thread)    │
    ├─────────────────────────────────────────────────────────────┤
    │ Full conversation: recent chunks only (last 2-3)            │
    └─────────────────────────────────────────────────────────────┘

    Thresholds:
    - Under 60%: No compaction needed
    - ≥80%: Tier 0.5 (chunks) or Tier 1 (threads) until <60%
    - ≥85%: Emergency Tiers 3-4 (trim thinking, truncate)

    Tiers:
    0.5. Summarize oldest completed chunk → add to chunk_summaries (intra-thread)
    1.   Summarize thread → merge into cumulative_summary (thread boundary)
    3.   Trim <thinking> blocks from old messages (emergency)
    4.   Truncate old assistant responses (emergency)

    Key insight: Compaction modifies BOTH the messages list AND the
    underlying conversation_history, so changes persist across scenes.
    """

    def __init__(
        self,
        token_counter: TokenCounter,
        summarizer: ThreadSummarizer,
        agent_client: AgentClient | None = None,
        context_budget: int = 16000,
        soft_ratio: float = 0.60,
        thread_compact_ratio: float = 0.80,
        emergency_ratio: float = 0.85,
        target_ratio: float = 0.70,
        scenes_per_chunk: int = 7,
        preserve_recent_chunks: int = 2,
    ) -> None:
        """
        Initialize compactor.

        Args:
            token_counter: TokenCounter for measuring context size.
            summarizer: ThreadSummarizer for tier 1 and chunk summarization.
            agent_client: Optional AgentClient for summary merging.
            context_budget: Total context budget in tokens.
            soft_ratio: Under this, skip compaction entirely (default 60%).
            thread_compact_ratio: Enable rolling compaction (default 80%).
            emergency_ratio: Emergency compaction with all tiers (default 85%).
            target_ratio: Target ratio after compaction (default 70%).
            scenes_per_chunk: Number of scenes per chunk for Tier 0.5 (default 7).
            preserve_recent_chunks: Keep this many recent chunks intact (default 2).
        """
        self.counter = token_counter
        self.summarizer = summarizer
        self.agent = agent_client
        self.budget = context_budget
        self.soft_threshold = int(context_budget * soft_ratio)
        self.thread_compact_threshold = int(context_budget * thread_compact_ratio)
        self.emergency_threshold = int(context_budget * emergency_ratio)
        self.target = int(context_budget * target_ratio)
        self.scenes_per_chunk = scenes_per_chunk
        self.preserve_recent_chunks = preserve_recent_chunks
        self._stats = CompactionStats()

    def should_compact_thread(self, messages: list[dict]) -> bool:
        """Check if thread compaction should happen (at thread boundary)."""
        tokens = self.counter.count_messages(messages)
        return tokens > self.thread_compact_threshold

    def should_emergency_compact(self, messages: list[dict]) -> bool:
        """Check if emergency compaction should trigger."""
        tokens = self.counter.count_messages(messages)
        should = tokens > self.emergency_threshold
        if should:
            LOGGER.warning(
                "Emergency compaction triggered: %d tokens > %d threshold",
                tokens,
                self.emergency_threshold,
            )
        return should

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if any compaction should trigger.

        Returns True if over 80% (rolling compaction threshold).
        """
        tokens = self.counter.count_messages(messages)
        return tokens > self.thread_compact_threshold

    def compact(
        self,
        messages: list[dict],
        state: CompactionState,
        context: AnnotationContext | None = None,
    ) -> tuple[list[dict], CompactionResult]:
        """
        Apply rolling compaction with smart thresholds.

        Compaction modifies BOTH the messages list AND the underlying
        conversation_history in context, so changes persist across scenes.

        Thresholds:
        - Under 60%: Skip compaction entirely
        - 60-80%: No compaction needed (under target)
        - ≥80%: Loop Tier 1 (summarize oldest threads) until <60%
        - >3 summaries: Tier 2 merges oldest into cumulative (keeps 3 recent)
        - ≥90%: Emergency with Tiers 3-4 (trim thinking, truncate)

        Args:
            messages: Current message list (will be copied, not mutated).
            state: Compaction state with summaries and completed threads.
            context: AnnotationContext to modify for persistent history changes.

        Returns:
            Tuple of (compacted messages, CompactionResult).
        """
        messages = list(messages)  # Work on copy

        initial_tokens = self.counter.count_messages(messages)
        usage_pct = (initial_tokens / self.budget) * 100 if self.budget > 0 else 0
        result = CompactionResult(
            initial_tokens=initial_tokens,
            final_tokens=initial_tokens,
            chunks_summarized=0,
            threads_summarized=0,
            summaries_merged=0,
            turns_trimmed=0,
            responses_truncated=0,
            target_reached=False,
        )

        current_tokens = initial_tokens

        # Under 60%: Skip compaction entirely - we have plenty of room
        if current_tokens < self.soft_threshold:
            result.target_reached = True
            LOGGER.debug(
                "Skipping compaction: %d tokens (%.1f%%) < 60%% threshold",
                current_tokens,
                usage_pct,
            )
            return messages, result

        # Determine how aggressive to be
        is_emergency = current_tokens > self.emergency_threshold
        max_iterations = 20  # Safety limit
        prev_tokens = current_tokens + 1  # Ensure first iteration runs

        for iteration in range(max_iterations):
            if current_tokens <= self.target:
                result.target_reached = True
                break

            # Detect no-progress doom loop
            if current_tokens >= prev_tokens:
                LOGGER.warning(
                    "Compaction stalled at %d tokens (no progress), breaking",
                    current_tokens,
                )
                break
            prev_tokens = current_tokens

            # Tier 0.5: Adaptive chunk compaction (intra-thread)
            # Try with decreasing preserve_recent values: 2 → 1 → 0
            chunk_compacted = False
            for preserve_n in range(self.preserve_recent_chunks, -1, -1):
                unsummarized = state.get_unsummarized_chunks(self.scenes_per_chunk)
                completed_chunks = state.get_completed_chunk_count(
                    self.scenes_per_chunk
                )
                summarizable = (
                    completed_chunks - preserve_n - len(state.summarized_chunk_indices)
                )

                if summarizable > 0 and unsummarized:
                    # Summarize oldest unsummarized chunk
                    chunk_idx, first_scene, last_scene = unsummarized[0]
                    LOGGER.info(
                        "Tier 0.5: Summarizing chunk %d (scenes %d-%d) of thread %d "
                        "(preserve_recent=%d)",
                        chunk_idx,
                        first_scene,
                        last_scene,
                        state.current_thread_id,
                        preserve_n,
                    )

                    # Get conversation turns for this chunk
                    chunk_messages = [
                        m
                        for m in messages
                        if (
                            m.get("thread_id") == state.current_thread_id
                            and m.get("scene_index") is not None
                            and first_scene <= m.get("scene_index") <= last_scene
                        )
                    ]

                    # Summarize the chunk
                    chunk_summary = self.summarizer.summarize_chunk(
                        thread_id=state.current_thread_id or 0,
                        chunk_index=chunk_idx,
                        first_scene_index=first_scene,
                        last_scene_index=last_scene,
                        conversation_excerpt=chunk_messages,
                    )
                    state.chunk_summaries.append(chunk_summary)
                    state.summarized_chunk_indices.append(chunk_idx)

                    # Remove chunk turns from messages AND persistent history
                    messages = self._remove_chunk_turns(
                        messages, state.current_thread_id or 0, first_scene, last_scene
                    )
                    if context is not None:
                        removed = context.remove_chunk_turns(
                            state.current_thread_id or 0, first_scene, last_scene
                        )
                        LOGGER.debug(
                            "Removed %d turns for chunk %d (scenes %d-%d)",
                            removed,
                            chunk_idx,
                            first_scene,
                            last_scene,
                        )

                    result.chunks_summarized += 1
                    result.highest_tier = max(result.highest_tier, 0.5)
                    current_tokens = self.counter.count_messages(messages)
                    chunk_compacted = True
                    break  # Exit preserve_n loop

            if chunk_compacted:
                if current_tokens < self.soft_threshold:
                    result.target_reached = True
                    break
                continue

            # Tier 0.5b: Partial chunk fallback
            # If no full chunks available, summarize half of current scenes
            if (
                state.current_scene_index >= 6
                and not state.summarized_chunk_indices
                and state.current_thread_id is not None
            ):
                half = state.current_scene_index // 2
                if half >= 3:
                    LOGGER.info(
                        "Tier 0.5b: Force partial chunk (scenes 0-%d) of thread %d",
                        half - 1,
                        state.current_thread_id,
                    )
                    messages, compacted = self._summarize_partial_chunk(
                        messages, state, context, last_scene=half - 1
                    )
                    if compacted:
                        result.chunks_summarized += 1
                        result.highest_tier = max(result.highest_tier, 0.5)
                        current_tokens = self.counter.count_messages(messages)
                        if current_tokens < self.soft_threshold:
                            result.target_reached = True
                            break
                        continue

            # Tier 1: Summarize oldest completed thread → merge into cumulative
            # Only if we have 2+ completed threads (preserve current thread's context)
            if len(state.completed_thread_ids) > 1:
                oldest_id = state.completed_thread_ids.pop(0)
                LOGGER.info(
                    "Tier 1: Summarizing thread %d and merging to cumulative (%d completed remain)",
                    oldest_id,
                    len(state.completed_thread_ids),
                )

                # Get conversation excerpt for this thread from messages
                thread_messages = [
                    m for m in messages if m.get("thread_id") == oldest_id
                ]
                summary_result = self.summarizer.summarize_thread(
                    oldest_id,
                    thread_messages,
                )

                # Immediately merge into cumulative instead of keeping separate summaries
                thread_summary = self.summarizer.to_thread_summary(
                    summary_result, position=0
                )
                state.cumulative_summary = self._merge_summaries(
                    state.cumulative_summary, [thread_summary]
                )

                # Remove from messages AND persistent history
                messages = self._remove_thread_turns(messages, oldest_id)
                if context is not None:
                    removed = context.remove_thread_turns(oldest_id)
                    LOGGER.debug(
                        "Removed %d turns from conversation history for thread %d",
                        removed,
                        oldest_id,
                    )

                result.threads_summarized += 1
                result.summaries_merged += 1  # Since we merge immediately
                result.highest_tier = max(result.highest_tier, 1)
                current_tokens = self.counter.count_messages(messages)

                # At 80%: Keep looping Tier 1 until under 60% (soft threshold)
                if current_tokens < self.soft_threshold:
                    result.target_reached = True
                    break
                continue

            # Below here: emergency-only tiers (90%+ threshold)
            if not is_emergency:
                break

            # Tier 3: Trim thinking tokens (preserve recent 4)
            trimmed, count = self._trim_thinking(messages, preserve_recent=4)
            if count > 0:
                LOGGER.info("Tier 3: Trimmed %d thinking blocks", count)
                messages = trimmed
                result.turns_trimmed += count
                result.highest_tier = max(result.highest_tier, 3)
                current_tokens = self.counter.count_messages(messages)
                continue

            # Tier 4: Truncate old responses
            truncated, count = self._truncate_responses(
                messages, max_age=8, max_len=500
            )
            if count > 0:
                LOGGER.info("Tier 4: Truncated %d old responses", count)
                messages = truncated
                result.responses_truncated += count
                result.highest_tier = max(result.highest_tier, 4)
                current_tokens = self.counter.count_messages(messages)
                continue

            # No more compaction options
            LOGGER.warning(
                "Compaction exhausted at %d tokens (target: %d)",
                current_tokens,
                self.target,
            )
            break

        result.final_tokens = current_tokens
        result.target_reached = current_tokens <= self.target

        # Update stats
        if result.highest_tier > 0:
            self._stats.record_compaction(
                result.highest_tier, initial_tokens, current_tokens
            )

        LOGGER.info(
            "Compaction: %d -> %d tokens (%.1f%% -> %.1f%%, tier: %d, target: %s)",
            initial_tokens,
            current_tokens,
            (initial_tokens / self.budget) * 100,
            (current_tokens / self.budget) * 100,
            result.highest_tier,
            result.target_reached,
        )

        return messages, result

    @property
    def stats(self) -> CompactionStats:
        """Get compaction statistics."""
        return self._stats

    def get_current_usage(self, messages: list[dict]) -> tuple[int, float]:
        """Get current token count and usage percentage."""
        tokens = self.counter.count_messages(messages)
        usage_percent = (tokens / self.budget) * 100 if self.budget > 0 else 0.0
        return tokens, usage_percent

    def _remove_thread_turns(self, messages: list[dict], thread_id: int) -> list[dict]:
        """Remove turns for a summarized thread.

        Filters out messages tagged with this thread_id.
        Messages without thread_id are preserved.
        """
        return [m for m in messages if m.get("thread_id") != thread_id]

    def _remove_chunk_turns(
        self,
        messages: list[dict],
        thread_id: int,
        first_scene: int,
        last_scene: int,
    ) -> list[dict]:
        """Remove turns for a summarized chunk within a thread.

        Filters out messages from the specified thread and scene range.
        Messages without scene_index are preserved (tool calls, etc.).
        """

        def should_keep(msg: dict) -> bool:
            # Keep turns from other threads
            if msg.get("thread_id") != thread_id:
                return True
            # Keep turns without scene_index
            scene_idx = msg.get("scene_index")
            if scene_idx is None:
                return True
            # Remove turns within the chunk's scene range
            return not (first_scene <= scene_idx <= last_scene)

        return [m for m in messages if should_keep(m)]

    def _merge_summaries(self, cumulative: str, summaries: list[ThreadSummary]) -> str:
        """Merge thread summaries into cumulative summary."""
        # Format summaries
        summary_texts = []
        for s in summaries:
            summary_texts.append(f"Thread {s.thread_id}: {s.summary_text}")
        summaries_str = "\n\n".join(summary_texts)

        # Try agent-based merging
        if self.agent:
            try:
                prompt = CUMULATIVE_SUMMARY_PROMPT.format(
                    cumulative=cumulative or "(none)",
                    summaries=summaries_str,
                )
                response = self.agent.chat(
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "Merge these summaries."},
                    ],
                    max_tokens=512,
                    temperature=0.3,
                )
                merged = response.message.get("content", "")
                if merged.strip():
                    return merged.strip()
            except Exception as e:
                LOGGER.warning("Agent merge failed, using heuristic: %s", e)

        # Heuristic: simple concatenation
        parts = []
        if cumulative:
            parts.append(cumulative)
        parts.extend(summary_texts)
        return "\n\n".join(parts)

    def _trim_thinking(
        self, messages: list[dict], preserve_recent: int
    ) -> tuple[list[dict], int]:
        """Remove <thinking> blocks from old messages."""
        # Pattern for thinking blocks
        thinking_pattern = re.compile(
            r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE
        )

        result = []
        trimmed_count = 0

        for i, msg in enumerate(messages):
            # Preserve recent messages
            if i >= len(messages) - preserve_recent:
                result.append(msg)
                continue

            content = msg.get("content", "")
            if not content or msg.get("role") != "assistant":
                result.append(msg)
                continue

            # Check for thinking blocks
            if "<thinking>" in content.lower():
                new_content = thinking_pattern.sub("", content).strip()
                if new_content != content:
                    result.append({**msg, "content": new_content})
                    trimmed_count += 1
                    continue

            result.append(msg)

        return result, trimmed_count

    def _truncate_responses(
        self, messages: list[dict], max_age: int, max_len: int
    ) -> tuple[list[dict], int]:
        """Truncate old assistant responses."""
        result = []
        truncated_count = 0

        for i, msg in enumerate(messages):
            # Preserve recent messages
            if i >= len(messages) - max_age:
                result.append(msg)
                continue

            content = msg.get("content", "")
            if not content or msg.get("role") != "assistant":
                result.append(msg)
                continue

            # Skip already-truncated messages
            if content.endswith("... [truncated]"):
                result.append(msg)
                continue

            # Truncate long responses
            if len(content) > max_len:
                truncated = content[:max_len] + "... [truncated]"
                result.append({**msg, "content": truncated})
                truncated_count += 1
                continue

            result.append(msg)

        return result, truncated_count

    def _summarize_partial_chunk(
        self,
        messages: list[dict],
        state: CompactionState,
        context: AnnotationContext | None,
        last_scene: int,
    ) -> tuple[list[dict], bool]:
        """
        Summarize a partial chunk (fewer than scenes_per_chunk scenes).

        Used as a fallback when no full chunks are available for compaction.

        Args:
            messages: Current message list.
            state: Compaction state.
            context: AnnotationContext for persistent history changes.
            last_scene: Last scene index to include (0-indexed, inclusive).

        Returns:
            Tuple of (modified messages, whether compaction happened).
        """
        thread_id = state.current_thread_id
        if thread_id is None:
            return messages, False

        first_scene = 0
        # Use chunk_index = -1 to indicate partial chunk
        chunk_idx = -1 - len(state.summarized_chunk_indices)

        # Get conversation turns for partial chunk
        chunk_messages = [
            m
            for m in messages
            if (
                m.get("thread_id") == thread_id
                and m.get("scene_index") is not None
                and first_scene <= m.get("scene_index") <= last_scene
            )
        ]

        if not chunk_messages:
            return messages, False

        # Summarize the partial chunk
        chunk_summary = self.summarizer.summarize_chunk(
            thread_id=thread_id,
            chunk_index=chunk_idx,
            first_scene_index=first_scene,
            last_scene_index=last_scene,
            conversation_excerpt=chunk_messages,
        )
        state.chunk_summaries.append(chunk_summary)
        # Track with negative index to distinguish from regular chunks
        state.summarized_chunk_indices.append(chunk_idx)

        # Remove partial chunk turns from messages AND persistent history
        messages = self._remove_chunk_turns(
            messages, thread_id, first_scene, last_scene
        )
        if context is not None:
            removed = context.remove_chunk_turns(thread_id, first_scene, last_scene)
            LOGGER.debug(
                "Removed %d turns for partial chunk (scenes %d-%d)",
                removed,
                first_scene,
                last_scene,
            )

        return messages, True
