"""Base exporter interface for glossary export."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from terrarium_annotator.storage import GlossaryEntry


class Exporter(ABC):
    """Base class for glossary exporters."""

    @property
    @abstractmethod
    def extension(self) -> str:
        """File extension without dot (e.g., 'json', 'yaml')."""
        ...

    @abstractmethod
    def export(self, entries: Iterator[GlossaryEntry], output_path: Path) -> int:
        """Export entries to file.

        Args:
            entries: Iterator of glossary entries to export.
            output_path: Path to output file.

        Returns:
            Number of entries exported.
        """
        ...

    @staticmethod
    def entry_to_dict(entry: GlossaryEntry) -> dict:
        """Convert a glossary entry to an exportable dictionary.

        Args:
            entry: The glossary entry to convert.

        Returns:
            Dictionary with all entry fields.
        """
        return {
            "id": entry.id,
            "term": entry.term,
            "definition": entry.definition,
            "status": entry.status,
            "tags": entry.tags,
            "first_seen_post_id": entry.first_seen_post_id,
            "first_seen_thread_id": entry.first_seen_thread_id,
            "last_updated_post_id": entry.last_updated_post_id,
            "last_updated_thread_id": entry.last_updated_thread_id,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
