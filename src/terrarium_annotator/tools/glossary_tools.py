"""Glossary tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Literal

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
        snapshot_id: int | None = None,
    ) -> str:
        """Create glossary/codex entry, log creation, return XML result.

        Args:
            term: Entry term.
            definition: Entry definition.
            tags: Entry tags.
            status: Entry status (default: tentative).
            entry_type: "glossary" or "codex".
            post_id: Source post ID.
            thread_id: Source thread ID.
            snapshot_id: Snapshot ID for revision linkage (F11).
        """
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
            # Log creation to revision history with snapshot linkage
            self._revisions.log_creation(
                entry_id=entry_id,
                term=term,
                definition=definition,
                tags=tags,
                status=status,
                source_post_id=post_id,
                snapshot_id=snapshot_id,
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
        snapshot_id: int | None = None,
    ) -> str:
        """Update glossary entry, log changes, return XML result.

        Args:
            entry_id: Entry ID to update.
            term: New term (optional).
            definition: New definition (optional).
            tags: New tags (optional).
            status: New status (optional).
            post_id: Source post ID.
            thread_id: Source thread ID.
            snapshot_id: Snapshot ID for revision linkage (F11).
        """
        # Get existing entry for diff
        existing = self._glossary.get(entry_id)
        if existing is None:
            return format_error(f"Entry {entry_id} not found", code="NOT_FOUND")

        # Log each changed field with snapshot linkage
        if term is not None and term != existing.term:
            self._revisions.log_change(
                entry_id,
                "term",
                existing.term,
                term,
                source_post_id=post_id,
                snapshot_id=snapshot_id,
            )
        if definition is not None and definition != existing.definition:
            self._revisions.log_change(
                entry_id,
                "definition",
                existing.definition,
                definition,
                source_post_id=post_id,
                snapshot_id=snapshot_id,
            )
        if tags is not None and tags != existing.tags:
            self._revisions.log_change(
                entry_id,
                "tags",
                ",".join(existing.tags),
                ",".join(tags),
                source_post_id=post_id,
                snapshot_id=snapshot_id,
            )
        if status is not None and status != existing.status:
            self._revisions.log_change(
                entry_id,
                "status",
                existing.status,
                status,
                source_post_id=post_id,
                snapshot_id=snapshot_id,
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
        snapshot_id: int | None = None,
    ) -> str:
        """Delete glossary entry, log deletion, return XML result.

        Args:
            entry_id: Entry ID to delete.
            reason: Reason for deletion.
            post_id: Source post ID.
            snapshot_id: Snapshot ID for revision linkage (F11).
        """
        existing = self._glossary.get(entry_id)
        if existing is None:
            return format_error(f"Entry {entry_id} not found", code="NOT_FOUND")

        # Log deletion before removing with snapshot linkage
        self._revisions.log_deletion(
            entry_id,
            reason,
            source_post_id=post_id,
            snapshot_id=snapshot_id,
        )

        deleted = self._glossary.delete(entry_id, reason)
        if deleted:
            return format_success(f"Deleted entry '{existing.term}'")
        return format_error(f"Failed to delete entry {entry_id}", code="DELETE_FAILED")

    def lookup(self, term: str) -> str:
        """Look up a specific term by exact normalized name.

        Args:
            term: The term to look up.

        Returns:
            XML with the entry if found, or null indicator if not.
        """
        results = self._glossary.lookup_terms([term])
        entry = results.get(term)
        if entry:
            return format_glossary_entry(entry)
        return format_success(f"Term '{term}' not found in glossary", found=False)

    def upsert(
        self,
        term: str,
        definition: str,
        tags: list[str],
        *,
        post_id: int,
        thread_id: int,
        scene_index: int | None = None,
        snapshot_id: int | None = None,
        merge_callback: "Callable[[str, str, str], str] | None" = None,
    ) -> str:
        """Create or update a glossary entry with smart merge.

        If the term doesn't exist, creates it. If it exists, merges the
        new definition with the existing one.

        Args:
            term: The term to define.
            definition: New definition or additional information.
            tags: Tags for categorization.
            post_id: Source post ID.
            thread_id: Source thread ID.
            scene_index: Scene index for source tracking.
            snapshot_id: Snapshot ID for revision linkage.
            merge_callback: Optional function(old_def, new_def, term) -> merged_def.
                If not provided, new definition replaces old.

        Returns:
            XML result indicating created or updated.
        """
        # Check if term exists
        results = self._glossary.lookup_terms([term])
        existing = results.get(term)

        if existing is None:
            # Create new entry
            try:
                entry_id = self._glossary.create(
                    term=term,
                    definition=definition,
                    tags=tags,
                    post_id=post_id,
                    thread_id=thread_id,
                    status="tentative",
                    entry_type="glossary",
                )
                # Log creation
                self._revisions.log_creation(
                    entry_id=entry_id,
                    term=term,
                    definition=definition,
                    tags=tags,
                    status="tentative",
                    source_post_id=post_id,
                    snapshot_id=snapshot_id,
                )
                # Track source scene
                if scene_index is not None:
                    self._glossary.add_source_scene(
                        entry_id=entry_id,
                        thread_id=thread_id,
                        post_id=post_id,
                        scene_index=scene_index,
                    )
                return format_success(
                    f"Created glossary entry '{term}'",
                    entry_id=entry_id,
                    action="created",
                )
            except DuplicateTermError as e:
                # Race condition - another create happened, treat as update
                existing = self._glossary.get(e.existing_id)
                if existing is None:
                    return format_error(f"Entry disappeared: {e}", code="RACE_ERROR")

        # Update existing entry
        entry_id = existing.id
        assert entry_id is not None

        # Merge definitions
        if merge_callback and existing.definition != definition:
            merged_def = merge_callback(existing.definition, definition, term)
        else:
            # Default: replace with new definition
            merged_def = definition

        # Only update if definition actually changed
        if merged_def != existing.definition:
            # Log change
            self._revisions.log_change(
                entry_id,
                "definition",
                existing.definition,
                merged_def,
                source_post_id=post_id,
                snapshot_id=snapshot_id,
            )

            # Perform update
            self._glossary.update(
                entry_id,
                definition=merged_def,
                tags=tags,  # Update tags too
                post_id=post_id,
                thread_id=thread_id,
            )

        # Track source scene
        if scene_index is not None:
            self._glossary.add_source_scene(
                entry_id=entry_id,
                thread_id=thread_id,
                post_id=post_id,
                scene_index=scene_index,
            )

        return format_success(
            f"Updated glossary entry '{term}'",
            entry_id=entry_id,
            action="updated",
        )
