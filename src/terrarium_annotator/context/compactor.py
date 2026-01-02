"""Context compaction for managing token budget."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.context.prompts import CUMULATIVE_SUMMARY_PROMPT

if TYPE_CHECKING:
    from terrarium_annotator.agent_client import AgentClient
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
            ThreadSummary.from_dict(ts)
            for ts in data.get("thread_summaries", [])
        ]
        state.completed_thread_ids = data.get("completed_thread_ids", [])
        return state


class ContextCompactor:
    """
    4-tier context compaction algorithm.

    Tiers (applied in order):
    1. Summarize oldest completed thread
    2. Merge old summaries into cumulative summary
    3. Trim thinking tokens from old turns (preserve recent 4)
    4. Truncate old assistant responses (max 500 chars)

    Thresholds:
    - Trigger: 80% of context budget
    - Target: 70% of context budget
    """

    def __init__(
        self,
        token_counter: TokenCounter,
        summarizer: ThreadSummarizer,
        agent_client: AgentClient | None = None,
        context_budget: int = 16000,
        trigger_ratio: float = 0.80,
        target_ratio: float = 0.70,
    ) -> None:
        """
        Initialize compactor.

        Args:
            token_counter: TokenCounter for measuring context size.
            summarizer: ThreadSummarizer for tier 1.
            agent_client: Optional AgentClient for tier 2 summary merging.
            context_budget: Total context budget in tokens.
            trigger_ratio: Ratio at which to trigger compaction.
            target_ratio: Target ratio after compaction.
        """
        self.counter = token_counter
        self.summarizer = summarizer
        self.agent = agent_client
        self.budget = context_budget
        self.trigger = int(context_budget * trigger_ratio)
        self.target = int(context_budget * target_ratio)

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if compaction should be triggered."""
        tokens = self.counter.count_messages(messages)
        should = tokens > self.trigger
        if should:
            LOGGER.debug(
                "Compaction triggered: %d tokens > %d trigger",
                tokens,
                self.trigger,
            )
        return should

    def compact(
        self,
        messages: list[dict],
        state: CompactionState,
        thread_conversation: dict[int, list[dict]] | None = None,
    ) -> tuple[list[dict], CompactionResult]:
        """
        Apply 4-tier compaction until target reached or no more options.

        Args:
            messages: Current message list (will be copied, not mutated).
            state: Compaction state with summaries and completed threads.
            thread_conversation: Optional map of thread_id to conversation excerpt.

        Returns:
            Tuple of (compacted messages, CompactionResult).
        """
        messages = list(messages)  # Work on copy
        thread_conversation = thread_conversation or {}

        initial_tokens = self.counter.count_messages(messages)
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
        max_iterations = 20  # Safety limit

        for _ in range(max_iterations):
            if current_tokens <= self.target:
                result.target_reached = True
                break

            # Tier 1: Summarize oldest completed thread
            if len(state.completed_thread_ids) > 1:
                oldest_id = state.completed_thread_ids.pop(0)
                LOGGER.debug("Tier 1: Summarizing thread %d", oldest_id)

                summary_result = self.summarizer.summarize_thread(
                    oldest_id,
                    thread_conversation.get(oldest_id, []),
                )
                state.thread_summaries.append(
                    self.summarizer.to_thread_summary(
                        summary_result,
                        position=len(state.thread_summaries),
                    )
                )
                messages = self._remove_thread_turns(messages, oldest_id)
                result.threads_summarized += 1
                current_tokens = self.counter.count_messages(messages)
                continue

            # Tier 2: Merge old summaries into cumulative
            if len(state.thread_summaries) > 3:
                LOGGER.debug("Tier 2: Merging old summaries")
                oldest_summaries = state.thread_summaries[:2]
                state.thread_summaries = state.thread_summaries[2:]
                state.cumulative_summary = self._merge_summaries(
                    state.cumulative_summary, oldest_summaries
                )
                result.summaries_merged += 2
                # Token reduction comes from having fewer separate summaries
                current_tokens = self.counter.count_messages(messages)
                continue

            # Tier 3: Trim thinking tokens (preserve recent 4)
            trimmed, count = self._trim_thinking(messages, preserve_recent=4)
            if count > 0:
                LOGGER.debug("Tier 3: Trimmed %d thinking blocks", count)
                messages = trimmed
                result.turns_trimmed += count
                current_tokens = self.counter.count_messages(messages)
                continue

            # Tier 4: Truncate old responses
            truncated, count = self._truncate_responses(
                messages, max_age=8, max_len=500
            )
            if count > 0:
                LOGGER.debug("Tier 4: Truncated %d old responses", count)
                messages = truncated
                result.responses_truncated += count
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

        LOGGER.info(
            "Compaction: %d -> %d tokens (target: %d, reached: %s)",
            initial_tokens,
            current_tokens,
            self.target,
            result.target_reached,
        )

        return messages, result

    def _remove_thread_turns(
        self, messages: list[dict], thread_id: int
    ) -> list[dict]:
        """Remove turns for a summarized thread."""
        # Filter out messages tagged with this thread_id
        # For now, we don't have thread tagging in messages,
        # so this is a no-op placeholder
        # TODO: Tag messages with thread_id in runner for proper filtering
        return messages

    def _merge_summaries(
        self, cumulative: str, summaries: list[ThreadSummary]
    ) -> str:
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
