"""Glossary tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from terrarium_annotator.storage.exceptions import DuplicateTermError
from terrarium_annotator.storage.glossary import EntryType
from terrarium_annotator.tools.xml_formatter import (
    format_error,
    format_glossary_entry,
    format_search_results,
    format_success,
)

if TYPE_CHECKING:
    from terrarium_annotator.storage import GlossaryStore, RevisionHistory


class GlossaryTools:
    """Glossary tool implementations."""

    def __init__(
        self,
        glossary: GlossaryStore,
        revisions: RevisionHistory,
    ) -> None:
        """Initialize with glossary and revision stores."""
        self._glossary = glossary
        self._revisions = revisions

    def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        status: Literal["confirmed", "tentative", "all"] = "all",
        entry_type: EntryType | Literal["all"] = "all",
        limit: int = 10,
    ) -> str:
        """Execute glossary search, return XML result."""
        entries = self._glossary.search(
            query,
            tags=tags,
            status=status,
            entry_type=entry_type,
            limit=limit,
        )
        return format_search_results(entries, query)

    def create(
        self,
        term: str,
        definition: str,
        tags: list[str],
        *,
        status: str = "tentative",
        entry_type: EntryType = "glossary",
        post_id: int,
        thread_id: int,
    ) -> str:
        """Create glossary/codex entry, log creation, return XML result."""
        try:
            entry_id = self._glossary.create(
                term=term,
                definition=definition,
                tags=tags,
                post_id=post_id,
                thread_id=thread_id,
                status=status,
                entry_type=entry_type,
            )
            # Log creation to revision history
            self._revisions.log_creation(
                entry_id=entry_id,
                term=term,
                definition=definition,
                tags=tags,
                status=status,
                source_post_id=post_id,
            )
            type_label = "codex" if entry_type == "codex" else "glossary"
            return format_success(f"Created {type_label} entry '{term}'", entry_id=entry_id)
        except DuplicateTermError as e:
            return format_error(
                f"Term '{term}' already exists (id={e.existing_id})",
                code="DUPLICATE",
            )

    def update(
        self,
        entry_id: int,
        *,
        term: str | None = None,
        definition: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        post_id: int,
        thread_id: int,
    ) -> str:
        """Update glossary entry, log changes, return XML result."""
        # Get existing entry for diff
        existing = self._glossary.get(entry_id)
        if existing is None:
            return format_error(f"Entry {entry_id} not found", code="NOT_FOUND")

        # Log each changed field
        if term is not None and term != existing.term:
            self._revisions.log_change(
                entry_id,
                "term",
                existing.term,
                term,
                source_post_id=post_id,
            )
        if definition is not None and definition != existing.definition:
            self._revisions.log_change(
                entry_id,
                "definition",
                existing.definition,
                definition,
                source_post_id=post_id,
            )
        if tags is not None and tags != existing.tags:
            self._revisions.log_change(
                entry_id,
                "tags",
                ",".join(existing.tags),
                ",".join(tags),
                source_post_id=post_id,
            )
        if status is not None and status != existing.status:
            self._revisions.log_change(
                entry_id,
                "status",
                existing.status,
                status,
                source_post_id=post_id,
            )

        # Perform update
        updated = self._glossary.update(
            entry_id,
            term=term,
            definition=definition,
            tags=tags,
            status=status,
            post_id=post_id,
            thread_id=thread_id,
        )

        if updated:
            # Return the updated entry
            entry = self._glossary.get(entry_id)
            if entry:
                return format_glossary_entry(entry)
            return format_success(f"Updated entry {entry_id}")
        return format_error(f"Failed to update entry {entry_id}", code="UPDATE_FAILED")

    def delete(
        self,
        entry_id: int,
        reason: str,
        *,
        post_id: int,
    ) -> str:
        """Delete glossary entry, log deletion, return XML result."""
        existing = self._glossary.get(entry_id)
        if existing is None:
            return format_error(f"Entry {entry_id} not found", code="NOT_FOUND")

        # Log deletion before removing
        self._revisions.log_deletion(
            entry_id,
            reason,
            source_post_id=post_id,
        )

        deleted = self._glossary.delete(entry_id, reason)
        if deleted:
            return format_success(f"Deleted entry '{existing.term}'")
        return format_error(f"Failed to delete entry {entry_id}", code="DELETE_FAILED")
