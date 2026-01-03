"""Context compaction for managing token budget."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from terrarium_annotator.context.metrics import CompactionStats
from terrarium_annotator.context.models import ThreadSummary
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
    threads_summarized: int
    summaries_merged: int
    turns_trimmed: int
    responses_truncated: int
    target_reached: bool
    highest_tier: int = 0  # 0 = no compaction, 1-4 = tier used


@dataclass
class CompactionState:
    """Mutable state for compaction tracking."""

    cumulative_summary: str = ""
    thread_summaries: list[ThreadSummary] = field(default_factory=list)
    completed_thread_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict for snapshot storage."""
        return {
            "cumulative_summary": self.cumulative_summary,
            "thread_summaries": [ts.to_dict() for ts in self.thread_summaries],
            "completed_thread_ids": self.completed_thread_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CompactionState:
        """Reconstruct from snapshot data."""
        state = cls()
        state.cumulative_summary = data.get("cumulative_summary", "")
        state.thread_summaries = [
            ThreadSummary.from_dict(ts) for ts in data.get("thread_summaries", [])
        ]
        state.completed_thread_ids = data.get("completed_thread_ids", [])
        return state


class ContextCompactor:
    """
    Rolling context compaction with smart thresholds.

    Context structure after compaction:
    ┌─────────────────────────────────────────────────────────────┐
    │ cumulative_summary: "The Story So Far" (merged summaries)   │
    ├─────────────────────────────────────────────────────────────┤
    │ thread_summaries: [Thread N, N+1, N+2] (3 recent, with IDs) │
    ├─────────────────────────────────────────────────────────────┤
    │ Full conversation: recent threads                           │
    └─────────────────────────────────────────────────────────────┘

    Thresholds:
    - Under 60%: No compaction needed
    - ≥80%: Loop Tier 1 until <60% (summarize oldest threads)
    - >3 summaries: Tier 2 merges oldest into cumulative
    - ≥90%: Emergency Tiers 3-4 (trim thinking, truncate)

    Tiers:
    1. Summarize oldest completed thread → add to thread_summaries
    2. Merge oldest 2 summaries into cumulative (keeps 3 recent with entry IDs)
    3. Trim <thinking> blocks from old messages (emergency)
    4. Truncate old assistant responses (emergency)

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
        emergency_ratio: float = 0.90,
        target_ratio: float = 0.70,
    ) -> None:
        """
        Initialize compactor.

        Args:
            token_counter: TokenCounter for measuring context size.
            summarizer: ThreadSummarizer for tier 1.
            agent_client: Optional AgentClient for tier 2 summary merging.
            context_budget: Total context budget in tokens.
            soft_ratio: Under this, skip compaction entirely (default 60%).
            thread_compact_ratio: Enable rolling compaction (default 80%).
            emergency_ratio: Emergency compaction with all tiers (default 90%).
            target_ratio: Target ratio after compaction (default 70%).
        """
        self.counter = token_counter
        self.summarizer = summarizer
        self.agent = agent_client
        self.budget = context_budget
        self.soft_threshold = int(context_budget * soft_ratio)
        self.thread_compact_threshold = int(context_budget * thread_compact_ratio)
        self.emergency_threshold = int(context_budget * emergency_ratio)
        self.target = int(context_budget * target_ratio)
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

        for iteration in range(max_iterations):
            if current_tokens <= self.target:
                result.target_reached = True
                break

            # Tier 1: Summarize oldest completed thread
            # Only if we have 2+ completed threads (preserve current thread's context)
            if len(state.completed_thread_ids) > 1:
                oldest_id = state.completed_thread_ids.pop(0)
                LOGGER.info(
                    "Tier 1: Summarizing thread %d (%d completed remain)",
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
                state.thread_summaries.append(
                    self.summarizer.to_thread_summary(
                        summary_result,
                        position=len(state.thread_summaries),
                    )
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
                result.highest_tier = max(result.highest_tier, 1)
                current_tokens = self.counter.count_messages(messages)

                # At 80%: Keep looping Tier 1 until under 60% (soft threshold)
                if current_tokens < self.soft_threshold:
                    result.target_reached = True
                    break
                continue

            # Tier 2: Merge old summaries into cumulative (runs when >3 summaries)
            # NOT emergency-only - keeps exactly 3 recent summaries with entry IDs
            if len(state.thread_summaries) > 3:
                LOGGER.info("Tier 2: Merging old summaries into cumulative")
                oldest_summaries = state.thread_summaries[:2]
                state.thread_summaries = state.thread_summaries[2:]
                state.cumulative_summary = self._merge_summaries(
                    state.cumulative_summary, oldest_summaries
                )
                result.summaries_merged += 2
                result.highest_tier = max(result.highest_tier, 2)
                current_tokens = self.counter.count_messages(messages)
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

            # Truncate long responses
            if len(content) > max_len:
                truncated = content[:max_len] + "... [truncated]"
                result.append({**msg, "content": truncated})
                truncated_count += 1
                continue

            result.append(msg)

        return result, truncated_count
