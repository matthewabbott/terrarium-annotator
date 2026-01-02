"""JSON exporter for glossary entries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from terrarium_annotator.exporters.base import Exporter

if TYPE_CHECKING:
    from terrarium_annotator.storage import GlossaryEntry


class JsonExporter(Exporter):
    """Export glossary entries to JSON format."""

    @property
    def extension(self) -> str:
        """Return json extension."""
        return "json"

    def export(self, entries: Iterator[GlossaryEntry], output_path: Path) -> int:
        """Export entries to JSON file.

        Args:
            entries: Iterator of glossary entries to export.
            output_path: Path to output JSON file.

        Returns:
            Number of entries exported.
        """
        data = [self.entry_to_dict(entry) for entry in entries]
        output = {
            "entries": data,
            "count": len(data),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        return len(data)
