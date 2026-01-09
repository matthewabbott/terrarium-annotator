"""Curator for end-of-thread evaluation of tentative entries."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from terrarium_annotator.context.prompts import (
    BATCH_CURATOR_PROMPT,
    CURATOR_SYSTEM_PROMPT,
)
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
                self._log_decision(decision, thread_id)
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


# --- Batch Curator for post-processing full glossary ---


@dataclass
class BatchDecision:
    """A batch curator decision for a single entry."""

    entry_id: int
    entry_term: str
    action: Literal["KEEP", "DELETE", "MERGE"]
    target_id: int | None = None  # For MERGE
    reason: str = ""


@dataclass
class BatchCuratorResult:
    """Result of batch curator evaluation."""

    entries_evaluated: int = 0
    kept: int = 0
    deleted: int = 0
    merged: int = 0
    clusters_processed: int = 0
    decisions: list[BatchDecision] = field(default_factory=list)


class BatchCurator:
    """Post-processing curator for cleaning up full glossary."""

    def __init__(
        self,
        glossary: GlossaryStore,
        revisions: RevisionHistory,
        agent: AgentClient,
        *,
        dry_run: bool = False,
        cluster_size: int = 10,
    ) -> None:
        """
        Initialize batch curator.

        Args:
            glossary: GlossaryStore for entry operations.
            revisions: RevisionHistory for logging decisions.
            agent: AgentClient for evaluation calls.
            dry_run: If True, don't apply changes, just log decisions.
            cluster_size: Max entries per cluster for evaluation.
        """
        self.glossary = glossary
        self.revisions = revisions
        self.agent = agent
        self.dry_run = dry_run
        self.cluster_size = cluster_size

    def run(self, limit: int | None = None) -> BatchCuratorResult:
        """
        Run batch curator on all glossary entries.

        Args:
            limit: Maximum number of entries to process (for testing).

        Returns:
            BatchCuratorResult with decisions and counts.
        """
        result = BatchCuratorResult()

        # Get all entries
        all_entries = self.glossary.list_all()
        if limit:
            all_entries = all_entries[:limit]

        if not all_entries:
            LOGGER.info("No entries to curate")
            return result

        LOGGER.info("Batch curator processing %d entries", len(all_entries))

        # Build clusters
        clusters = self._build_clusters(all_entries)
        LOGGER.info("Built %d clusters from %d entries", len(clusters), len(all_entries))

        # Process each cluster
        for i, cluster in enumerate(clusters):
            result.clusters_processed += 1

            try:
                decisions = self._evaluate_cluster(cluster)

                for decision in decisions:
                    result.decisions.append(decision)
                    result.entries_evaluated += 1

                    if not self.dry_run:
                        self._apply_decision(decision)

                    if decision.action == "KEEP":
                        result.kept += 1
                    elif decision.action == "DELETE":
                        result.deleted += 1
                    elif decision.action == "MERGE":
                        result.merged += 1

                if (i + 1) % 10 == 0:
                    LOGGER.info(
                        "Progress: %d/%d clusters | kept=%d, deleted=%d, merged=%d",
                        i + 1,
                        len(clusters),
                        result.kept,
                        result.deleted,
                        result.merged,
                    )

            except Exception as e:
                LOGGER.warning(
                    "Failed to evaluate cluster %d (%d entries): %s",
                    i,
                    len(cluster),
                    e,
                )
                # Default to KEEP all on error
                for entry in cluster:
                    decision = BatchDecision(
                        entry_id=entry.id,
                        entry_term=entry.term,
                        action="KEEP",
                        reason=f"Cluster evaluation failed: {e}",
                    )
                    result.decisions.append(decision)
                    result.entries_evaluated += 1
                    result.kept += 1

        LOGGER.info(
            "Batch curator complete: %d kept, %d deleted, %d merged (dry_run=%s)",
            result.kept,
            result.deleted,
            result.merged,
            self.dry_run,
        )

        return result

    def _build_clusters(
        self, entries: list[GlossaryEntry]
    ) -> list[list[GlossaryEntry]]:
        """
        Group entries into clusters of related terms.

        Uses FTS search to find entries that might be duplicates or variants.
        """
        clustered_ids: set[int] = set()
        clusters: list[list[GlossaryEntry]] = []
        entry_map = {e.id: e for e in entries}

        for entry in entries:
            if entry.id in clustered_ids:
                continue

            # Start a new cluster with this entry
            cluster = [entry]
            clustered_ids.add(entry.id)

            # Find similar entries by term (handle FTS errors gracefully)
            try:
                # Clean term for FTS: only use alphanumeric parts
                clean_term = "".join(c if c.isalnum() or c.isspace() else " " for c in entry.term)
                clean_term = " ".join(clean_term.split())  # Normalize whitespace
                if clean_term:
                    similar = self.glossary.search(clean_term, limit=self.cluster_size)
                    for sim in similar:
                        if sim.id not in clustered_ids and sim.id in entry_map:
                            cluster.append(sim)
                            clustered_ids.add(sim.id)
            except Exception:
                # FTS can fail on special characters, skip this search
                pass

            # Also search by first significant word in definition
            if entry.definition:
                try:
                    # Extract first few alphanumeric words
                    words = []
                    for word in entry.definition.split()[:5]:
                        clean = "".join(c for c in word if c.isalnum())
                        if clean and len(clean) > 2:
                            words.append(clean)
                        if len(words) >= 3:
                            break
                    if words:
                        def_query = " ".join(words)
                        def_similar = self.glossary.search(def_query, limit=5)
                        for sim in def_similar:
                            if sim.id not in clustered_ids and sim.id in entry_map:
                                cluster.append(sim)
                                clustered_ids.add(sim.id)
                except Exception:
                    # FTS can fail on special characters, skip this search
                    pass

            # Cap cluster size
            cluster = cluster[: self.cluster_size]
            clusters.append(cluster)

        return clusters

    def _evaluate_cluster(self, cluster: list[GlossaryEntry]) -> list[BatchDecision]:
        """Evaluate a cluster of entries and return decisions."""
        # Build evaluation message
        message = self._build_cluster_message(cluster)

        # Call agent
        response = self.agent.chat(
            messages=[
                {"role": "system", "content": BATCH_CURATOR_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=0.3,
            max_tokens=1024,  # More tokens for cluster response
        )

        # Parse decisions
        content = response.message.get("content", "")
        return self._parse_cluster_decisions(content, cluster)

    def _build_cluster_message(self, cluster: list[GlossaryEntry]) -> str:
        """Build the evaluation prompt for a cluster."""
        parts = ["<entry_cluster>"]

        for entry in cluster:
            parts.append(f"\n<entry id=\"{entry.id}\">")
            parts.append(f"  <term>{entry.term}</term>")
            parts.append(f"  <definition>{entry.definition}</definition>")
            if entry.tags:
                parts.append(f"  <tags>{', '.join(entry.tags)}</tags>")
            parts.append("</entry>")

        parts.append("\n</entry_cluster>")
        parts.append(
            f"\n\nReview these {len(cluster)} entries and provide decisions for each."
        )

        return "\n".join(parts)

    def _parse_cluster_decisions(
        self, content: str, cluster: list[GlossaryEntry]
    ) -> list[BatchDecision]:
        """Parse agent response into BatchDecisions."""
        decisions = []
        cluster_map = {e.id: e for e in cluster}

        # Try to extract JSON array from response
        json_match = re.search(r"\[[\s\S]*?\]", content)
        if not json_match:
            # No valid JSON, default all to KEEP
            LOGGER.warning("No valid JSON array in response, defaulting to KEEP")
            return [
                BatchDecision(
                    entry_id=e.id,
                    entry_term=e.term,
                    action="KEEP",
                    reason="No valid JSON in response",
                )
                for e in cluster
            ]

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            LOGGER.warning("Invalid JSON in response, defaulting to KEEP")
            return [
                BatchDecision(
                    entry_id=e.id,
                    entry_term=e.term,
                    action="KEEP",
                    reason="Invalid JSON in response",
                )
                for e in cluster
            ]

        # Parse each decision
        decided_ids = set()
        for item in data:
            if not isinstance(item, dict):
                continue

            entry_id = item.get("id")
            if entry_id not in cluster_map:
                continue

            entry = cluster_map[entry_id]
            action = item.get("action", "KEEP").upper()
            if action not in ("KEEP", "DELETE", "MERGE"):
                action = "KEEP"

            decision = BatchDecision(
                entry_id=entry_id,
                entry_term=entry.term,
                action=action,
                target_id=item.get("target_id"),
                reason=item.get("reason", ""),
            )
            decisions.append(decision)
            decided_ids.add(entry_id)

        # Default any missing entries to KEEP
        for entry in cluster:
            if entry.id not in decided_ids:
                decisions.append(
                    BatchDecision(
                        entry_id=entry.id,
                        entry_term=entry.term,
                        action="KEEP",
                        reason="No decision in response",
                    )
                )

        return decisions

    def _apply_decision(self, decision: BatchDecision) -> None:
        """Apply a batch curator decision."""
        entry_id = decision.entry_id

        if decision.action == "KEEP":
            # Mark as confirmed
            self.glossary.update(
                entry_id,
                status="confirmed",
                post_id=0,
                thread_id=0,
            )
            self._log_decision(decision)

        elif decision.action == "DELETE":
            # Log deletion first, then delete
            self._log_decision(decision)
            self.revisions.log_deletion(
                entry_id=entry_id,
                reason=f"batch_curator:delete - {decision.reason}",
                source_post_id=0,
            )
            self.glossary.delete(entry_id, reason="batch_curator:delete")

        elif decision.action == "MERGE":
            if decision.target_id is None:
                LOGGER.warning(
                    "MERGE decision for %d missing target_id, treating as KEEP",
                    entry_id,
                )
                self.glossary.update(
                    entry_id,
                    status="confirmed",
                    post_id=0,
                    thread_id=0,
                )
                self._log_decision(decision)
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
                        thread_id=0,
                    )
                    # Delete source
                    self._log_decision(decision)
                    self.revisions.log_deletion(
                        entry_id=entry_id,
                        reason=f"batch_curator:merge into #{decision.target_id}",
                        source_post_id=0,
                    )
                    self.glossary.delete(entry_id, reason="batch_curator:merge")
                else:
                    # Target not found, just keep source
                    LOGGER.warning(
                        "MERGE target %d not found, keeping %d instead",
                        decision.target_id,
                        entry_id,
                    )
                    self.glossary.update(
                        entry_id,
                        status="confirmed",
                        post_id=0,
                        thread_id=0,
                    )
                    self._log_decision(decision)

    def _log_decision(self, decision: BatchDecision) -> None:
        """Log batch curator decision to revision history."""
        decision_json = json.dumps({
            "action": decision.action,
            "reason": decision.reason,
            "target_id": decision.target_id,
        })

        self.revisions.log_change(
            entry_id=decision.entry_id,
            field_name="batch_curator_decision",
            old_value="",
            new_value=decision_json,
            source_post_id=0,
        )
