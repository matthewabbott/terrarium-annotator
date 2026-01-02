"""YAML exporter for glossary entries."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import yaml

from terrarium_annotator.exporters.base import Exporter

if TYPE_CHECKING:
    from terrarium_annotator.storage import GlossaryEntry


class YamlExporter(Exporter):
    """Export glossary entries to YAML format."""

    @property
    def extension(self) -> str:
        """Return yaml extension."""
        return "yaml"

    def export(self, entries: Iterator[GlossaryEntry], output_path: Path) -> int:
        """Export entries to YAML file.

        Args:
            entries: Iterator of glossary entries to export.
            output_path: Path to output YAML file.

        Returns:
            Number of entries exported.
        """
        data = [self.entry_to_dict(entry) for entry in entries]
        output = {
            "entries": data,
            "count": len(data),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(output, f, allow_unicode=True, sort_keys=False)
        return len(data)
