"""Curator for end-of-thread evaluation of tentative entries."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from terrarium_annotator.context.prompts import CURATOR_SYSTEM_PROMPT
from terrarium_annotator.tools.xml_formatter import format_glossary_entry, format_post

if TYPE_CHECKING:
    from terrarium_annotator.agent_client import AgentClient
    from terrarium_annotator.corpus import CorpusReader, StoryPost
    from terrarium_annotator.storage import GlossaryEntry, GlossaryStore, RevisionHistory

LOGGER = logging.getLogger(__name__)


@dataclass
class CuratorDecision:
    """A curator decision for a single entry."""

    entry_id: int
    entry_term: str
    action: Literal["CONFIRM", "REJECT", "MERGE", "REVISE"]
    target_id: int | None = None  # For MERGE
    revised_definition: str | None = None  # For REVISE
    reasoning: str = ""


@dataclass
class CuratorResult:
    """Result of curator evaluation for a thread."""

    thread_id: int
    entries_evaluated: int = 0
    confirmed: int = 0
    rejected: int = 0
    merged: int = 0
    revised: int = 0
    decisions: list[CuratorDecision] = field(default_factory=list)


class CuratorFork:
    """End-of-thread evaluation of tentative entries."""

    def __init__(
        self,
        glossary: GlossaryStore,
        corpus: CorpusReader,
        revisions: RevisionHistory,
        agent: AgentClient,
        context_posts: int = 3,
    ) -> None:
        """
        Initialize curator.

        Args:
            glossary: GlossaryStore for entry operations.
            corpus: CorpusReader for fetching context.
            revisions: RevisionHistory for logging decisions.
            agent: AgentClient for evaluation calls.
            context_posts: Number of posts before/after to include as context.
        """
        self.glossary = glossary
        self.corpus = corpus
        self.revisions = revisions
        self.agent = agent
        self.context_posts = context_posts

    def run(self, thread_id: int) -> CuratorResult:
        """
        Evaluate tentative entries from a completed thread.

        Args:
            thread_id: Thread ID to evaluate.

        Returns:
            CuratorResult with decisions and counts.
        """
        result = CuratorResult(thread_id=thread_id)

        # Get tentative entries from this thread
        entries = self.glossary.get_tentative_by_thread(thread_id)
        if not entries:
            LOGGER.debug("No tentative entries in thread %d", thread_id)
            return result

        LOGGER.info(
            "Curator evaluating %d tentative entries from thread %d",
            len(entries),
            thread_id,
        )

        # Evaluate each entry
        for entry in entries:
            result.entries_evaluated += 1

            try:
                decision = self._evaluate_entry(entry)
                result.decisions.append(decision)

                # Apply decision
                self._apply_decision(decision, thread_id)

                # Update counts
                if decision.action == "CONFIRM":
                    result.confirmed += 1
                elif decision.action == "REJECT":
                    result.rejected += 1
                elif decision.action == "MERGE":
                    result.merged += 1
                elif decision.action == "REVISE":
                    result.revised += 1

            except Exception as e:
                LOGGER.warning(
                    "Failed to evaluate entry %d (%s): %s",
                    entry.id,
                    entry.term,
                    e,
                )
                # Default to CONFIRM on error (conservative)
                decision = CuratorDecision(
                    entry_id=entry.id,
                    entry_term=entry.term,
                    action="CONFIRM",
                    reasoning=f"Evaluation failed, defaulting to confirm: {e}",
                )
                result.decisions.append(decision)
                self._apply_decision(decision, thread_id)
                result.confirmed += 1

        LOGGER.info(
            "Curator complete: %d confirmed, %d rejected, %d merged, %d revised",
            result.confirmed,
            result.rejected,
            result.merged,
            result.revised,
        )

        return result

    def _evaluate_entry(self, entry: GlossaryEntry) -> CuratorDecision:
        """Evaluate a single entry and return decision."""
        # Get context around first appearance
        context_posts = self.corpus.get_adjacent_posts(
            entry.first_seen_post_id,
            before=self.context_posts,
            after=self.context_posts,
        )

        # Find similar entries
        similar_entries = self.glossary.search(entry.term, limit=5)
        # Filter out the entry itself
        similar_entries = [e for e in similar_entries if e.id != entry.id]

        # Build evaluation message
        message = self._build_evaluation_message(entry, context_posts, similar_entries)

        # Call agent
        response = self.agent.chat(
            messages=[
                {"role": "system", "content": CURATOR_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=0.3,
            max_tokens=256,
        )

        # Parse decision
        content = response.message.get("content", "")
        decision = self._parse_decision(content, entry)

        LOGGER.debug(
            "Curator decision for %s (#%d): %s - %s",
            entry.term,
            entry.id,
            decision.action,
            decision.reasoning,
        )

        return decision

    def _build_evaluation_message(
        self,
        entry: GlossaryEntry,
        context_posts: list[StoryPost],
        similar_entries: list[GlossaryEntry],
    ) -> str:
        """Build the evaluation prompt for an entry."""
        parts = []

        # Entry to evaluate
        parts.append("<entry_to_evaluate>")
        parts.append(format_glossary_entry(entry))
        parts.append("</entry_to_evaluate>")

        # Context where first seen
        if context_posts:
            parts.append("\n<first_appearance_context>")
            for post in context_posts:
                parts.append(format_post(post))
            parts.append("</first_appearance_context>")

        # Similar existing entries
        if similar_entries:
            parts.append("\n<similar_entries>")
            for sim in similar_entries:
                parts.append(format_glossary_entry(sim))
            parts.append("</similar_entries>")
        else:
            parts.append("\n<similar_entries>None found</similar_entries>")

        parts.append(
            "\n\nPlease evaluate this entry and provide your decision as JSON."
        )

        return "\n".join(parts)

    def _parse_decision(
        self, content: str, entry: GlossaryEntry
    ) -> CuratorDecision:
        """Parse agent response into a CuratorDecision."""
        # Try to extract JSON from response
        json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if not json_match:
            # Default to CONFIRM if no valid JSON
            return CuratorDecision(
                entry_id=entry.id,
                entry_term=entry.term,
                action="CONFIRM",
                reasoning="No valid JSON in response, defaulting to confirm",
            )

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return CuratorDecision(
                entry_id=entry.id,
                entry_term=entry.term,
                action="CONFIRM",
                reasoning="Invalid JSON in response, defaulting to confirm",
            )

        # Extract fields
        action = data.get("action", "CONFIRM").upper()
        if action not in ("CONFIRM", "REJECT", "MERGE", "REVISE"):
            action = "CONFIRM"

        return CuratorDecision(
            entry_id=entry.id,
            entry_term=entry.term,
            action=action,
            target_id=data.get("target_id"),
            revised_definition=data.get("revised_definition"),
            reasoning=data.get("reasoning", ""),
        )

    def _apply_decision(self, decision: CuratorDecision, thread_id: int) -> None:
        """Apply a curator decision to the glossary."""
        entry_id = decision.entry_id

        if decision.action == "CONFIRM":
            # Update status to confirmed
            self.glossary.update(
                entry_id,
                status="confirmed",
                post_id=0,  # No specific post for curator action
                thread_id=thread_id,
            )
            self._log_decision(decision, thread_id)

        elif decision.action == "REJECT":
            # Log deletion first, then delete
            self._log_decision(decision, thread_id)
            self.revisions.log_deletion(
                entry_id=entry_id,
                reason=f"curator:reject - {decision.reasoning}",
                source_post_id=0,
            )
            self.glossary.delete(entry_id, reason="curator:reject")

        elif decision.action == "MERGE":
            if decision.target_id is None:
                LOGGER.warning(
                    "MERGE decision for %d missing target_id, treating as CONFIRM",
                    entry_id,
                )
                self.glossary.update(
                    entry_id,
                    status="confirmed",
                    post_id=0,
                    thread_id=thread_id,
                )
            else:
                # Get both entries
                source = self.glossary.get(entry_id)
                target = self.glossary.get(decision.target_id)

                if source and target:
                    # Merge: append source definition to target
                    merged_definition = (
                        f"{target.definition}\n\n[Merged from {source.term}]: "
                        f"{source.definition}"
                    )
                    self.glossary.update(
                        decision.target_id,
                        definition=merged_definition,
                        post_id=0,
                        thread_id=thread_id,
                    )
                    # Delete source
                    self._log_decision(decision, thread_id)
                    self.revisions.log_deletion(
                        entry_id=entry_id,
                        reason=f"curator:merge into #{decision.target_id}",
                        source_post_id=0,
                    )
                    self.glossary.delete(entry_id, reason="curator:merge")
                else:
                    # Target not found, just confirm source
                    LOGGER.warning(
                        "MERGE target %d not found, confirming %d instead",
                        decision.target_id,
                        entry_id,
                    )
                    self.glossary.update(
                        entry_id,
                        status="confirmed",
                        post_id=0,
                        thread_id=thread_id,
                    )
            self._log_decision(decision, thread_id)

        elif decision.action == "REVISE":
            if decision.revised_definition:
                self.glossary.update(
                    entry_id,
                    definition=decision.revised_definition,
                    status="confirmed",
                    post_id=0,
                    thread_id=thread_id,
                )
            else:
                # No revised definition provided, just confirm
                LOGGER.warning(
                    "REVISE decision for %d missing revised_definition, treating as CONFIRM",
                    entry_id,
                )
                self.glossary.update(
                    entry_id,
                    status="confirmed",
                    post_id=0,
                    thread_id=thread_id,
                )
            self._log_decision(decision, thread_id)

    def _log_decision(self, decision: CuratorDecision, thread_id: int) -> None:
        """Log curator decision to revision history."""
        decision_json = json.dumps({
            "action": decision.action,
            "reasoning": decision.reasoning,
            "target_id": decision.target_id,
            "revised_definition": decision.revised_definition,
        })

        self.revisions.log_change(
            entry_id=decision.entry_id,
            field_name="curator_decision",
            old_value="",
            new_value=decision_json,
            source_post_id=0,
        )
