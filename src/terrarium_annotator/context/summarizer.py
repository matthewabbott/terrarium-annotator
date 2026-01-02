"""Thread summarization for context compaction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.context.prompts import THREAD_SUMMARY_PROMPT

if TYPE_CHECKING:
    from terrarium_annotator.agent_client import AgentClient
    from terrarium_annotator.storage import GlossaryEntry, GlossaryStore

LOGGER = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    """Result of thread summarization."""

    thread_id: int
    summary_text: str
    entries_created: list[int]
    entries_updated: list[int]
    token_count: int


class ThreadSummarizer:
    """Generates hybrid summaries for completed threads."""

    def __init__(
        self,
        agent_client: AgentClient,
        glossary: GlossaryStore,
        max_tokens: int = 512,
        chars_per_token: float = 4.0,
    ) -> None:
        """
        Initialize thread summarizer.

        Args:
            agent_client: Agent for generating summaries.
            glossary: GlossaryStore for looking up entries by thread.
            max_tokens: Max tokens for summary response.
            chars_per_token: Heuristic for token estimation.
        """
        self.agent = agent_client
        self.glossary = glossary
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token

    def summarize_thread(
        self,
        thread_id: int,
        conversation_excerpt: list[dict] | None = None,
    ) -> SummaryResult:
        """
        Generate hybrid summary for a completed thread.

        Args:
            thread_id: Thread to summarize.
            conversation_excerpt: Recent conversation turns for context (optional).

        Returns:
            SummaryResult with summary text and metadata.
        """
        # Get entries created/updated in this thread
        entries_created = self.glossary.get_by_thread(
            thread_id, field="first_seen_thread_id"
        )
        entries_updated = self.glossary.get_by_thread(
            thread_id, field="last_updated_thread_id"
        )
        # Filter out entries that were created in this thread from updates
        created_ids = {e.id for e in entries_created}
        entries_updated = [e for e in entries_updated if e.id not in created_ids]

        LOGGER.debug(
            "Summarizing thread %d: %d created, %d updated",
            thread_id,
            len(entries_created),
            len(entries_updated),
        )

        # Build summarization prompt
        messages = self._build_summary_messages(
            thread_id, conversation_excerpt or [], entries_created, entries_updated
        )

        # Try agent summarization
        summary_text = ""
        try:
            response = self.agent.chat(
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=0.3,
            )
            summary_text = response.message.get("content", "")
        except Exception as e:
            LOGGER.warning("Agent summarization failed for thread %d: %s", thread_id, e)

        # Fallback to heuristic if agent fails
        if not summary_text.strip():
            LOGGER.info("Using heuristic summary for thread %d", thread_id)
            summary_text = self._heuristic_summary(
                thread_id, entries_created, entries_updated
            )

        # Estimate token count
        token_count = max(1, int(len(summary_text) / self.chars_per_token))

        return SummaryResult(
            thread_id=thread_id,
            summary_text=summary_text,
            entries_created=[e.id for e in entries_created if e.id is not None],
            entries_updated=[e.id for e in entries_updated if e.id is not None],
            token_count=token_count,
        )

    def _build_summary_messages(
        self,
        thread_id: int,
        conversation: list[dict],
        created: list[GlossaryEntry],
        updated: list[GlossaryEntry],
    ) -> list[dict]:
        """Build messages for summarization request."""
        # Format entry lists
        created_str = ", ".join(e.term for e in created[:10])
        if len(created) > 10:
            created_str += f" (+{len(created) - 10} more)"

        updated_str = ", ".join(e.term for e in updated[:10])
        if len(updated) > 10:
            updated_str += f" (+{len(updated) - 10} more)"

        prompt = THREAD_SUMMARY_PROMPT.format(
            thread_id=thread_id,
            entries_created=created_str or "(none)",
            entries_updated=updated_str or "(none)",
        )

        messages: list[dict] = [{"role": "system", "content": prompt}]

        # Add conversation excerpt if provided
        if conversation:
            # Take last few turns for context
            recent = conversation[-6:]
            for turn in recent:
                if turn.get("role") in ("user", "assistant"):
                    content = turn.get("content", "")
                    if content and len(content) > 500:
                        content = content[:500] + "..."
                    messages.append({"role": turn["role"], "content": content})

        # Add user request
        messages.append({
            "role": "user",
            "content": "Please provide a concise summary of this thread.",
        })

        return messages

    def _heuristic_summary(
        self,
        thread_id: int,
        created: list[GlossaryEntry],
        updated: list[GlossaryEntry],
    ) -> str:
        """Fallback summary when agent unavailable."""
        parts = [f"Thread {thread_id} processed."]

        if created:
            terms = [e.term for e in created[:5]]
            term_list = ", ".join(terms)
            if len(created) > 5:
                term_list += f" (+{len(created) - 5} more)"
            parts.append(f"Created entries: {term_list}.")

        if updated:
            terms = [e.term for e in updated[:5]]
            term_list = ", ".join(terms)
            if len(updated) > 5:
                term_list += f" (+{len(updated) - 5} more)"
            parts.append(f"Updated entries: {term_list}.")

        if not created and not updated:
            parts.append("No glossary changes.")

        return " ".join(parts)

    def to_thread_summary(
        self,
        result: SummaryResult,
        position: int,
    ) -> ThreadSummary:
        """Convert SummaryResult to ThreadSummary dataclass."""
        return ThreadSummary(
            thread_id=result.thread_id,
            position=position,
            summary_text=result.summary_text,
            entries_created=result.entries_created,
            entries_updated=result.entries_updated,
        )
