"""Tests for glossary exporters."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from terrarium_annotator.exporters import JsonExporter, YamlExporter
from terrarium_annotator.storage import GlossaryEntry, GlossaryStore


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def glossary(temp_db: Path) -> GlossaryStore:
    """Create a glossary store with test data."""
    store = GlossaryStore(temp_db)
    # Add test entries
    store.create(
        term="Soma",
        definition="The questmaster who guides adventurers.",
        tags=["character", "qm"],
        post_id=100,
        thread_id=1,
        status="confirmed",
    )
    store.create(
        term="Dawn",
        definition="A warrior character.",
        tags=["character"],
        post_id=150,
        thread_id=1,
        status="tentative",
    )
    store.create(
        term="The Citadel",
        definition="A fortress location.",
        tags=["location"],
        post_id=200,
        thread_id=2,
        status="confirmed",
    )
    yield store
    store.close()


@pytest.fixture
def temp_output() -> Path:
    """Create a temporary output file path."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        return Path(f.name)


class TestJsonExporter:
    """Tests for JsonExporter."""

    def test_extension(self):
        """Verify json extension."""
        exporter = JsonExporter()
        assert exporter.extension == "json"

    def test_export_basic(self, glossary: GlossaryStore, temp_output: Path):
        """Export entries and validate JSON structure."""
        exporter = JsonExporter()
        count = exporter.export(glossary.all_entries(), temp_output)

        assert count == 3

        with open(temp_output) as f:
            data = json.load(f)

        assert "entries" in data
        assert "count" in data
        assert data["count"] == 3
        assert len(data["entries"]) == 3

    def test_export_preserves_all_fields(self, glossary: GlossaryStore, temp_output: Path):
        """Verify all entry fields are preserved."""
        exporter = JsonExporter()
        exporter.export(glossary.all_entries(), temp_output)

        with open(temp_output) as f:
            data = json.load(f)

        # Find Soma entry
        soma = next(e for e in data["entries"] if e["term"] == "Soma")

        assert soma["id"] is not None
        assert soma["term"] == "Soma"
        assert soma["definition"] == "The questmaster who guides adventurers."
        assert soma["status"] == "confirmed"
        assert soma["tags"] == ["character", "qm"]
        assert soma["first_seen_post_id"] == 100
        assert soma["first_seen_thread_id"] == 1
        assert "created_at" in soma
        assert "updated_at" in soma

    def test_export_empty_glossary(self, temp_db: Path, temp_output: Path):
        """Handle empty glossary gracefully."""
        store = GlossaryStore(temp_db)
        exporter = JsonExporter()

        count = exporter.export(store.all_entries(), temp_output)
        store.close()

        assert count == 0

        with open(temp_output) as f:
            data = json.load(f)

        assert data["count"] == 0
        assert data["entries"] == []


class TestYamlExporter:
    """Tests for YamlExporter."""

    def test_extension(self):
        """Verify yaml extension."""
        exporter = YamlExporter()
        assert exporter.extension == "yaml"

    def test_export_basic(self, glossary: GlossaryStore):
        """Export entries and validate YAML structure."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)

        exporter = YamlExporter()
        count = exporter.export(glossary.all_entries(), output_path)

        assert count == 3

        with open(output_path) as f:
            data = yaml.safe_load(f)

        assert "entries" in data
        assert "count" in data
        assert data["count"] == 3
        assert len(data["entries"]) == 3

    def test_export_unicode(self, temp_db: Path):
        """Verify unicode characters are preserved."""
        store = GlossaryStore(temp_db)
        store.create(
            term="Émile",
            definition="A character with accented name.",
            tags=["character"],
            post_id=1,
            thread_id=1,
        )

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            output_path = Path(f.name)

        exporter = YamlExporter()
        exporter.export(store.all_entries(), output_path)
        store.close()

        with open(output_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["entries"][0]["term"] == "Émile"


class TestEntryToDict:
    """Tests for the entry_to_dict conversion."""

    def test_converts_all_fields(self, glossary: GlossaryStore):
        """Verify entry_to_dict includes all fields."""
        entry = next(glossary.all_entries())
        exporter = JsonExporter()

        result = exporter.entry_to_dict(entry)

        expected_keys = {
            "id",
            "term",
            "definition",
            "status",
            "tags",
            "first_seen_post_id",
            "first_seen_thread_id",
            "last_updated_post_id",
            "last_updated_thread_id",
            "created_at",
            "updated_at",
        }
        assert set(result.keys()) == expected_keys


class TestFilterEntries:
    """Tests for CLI filter_entries function."""

    def test_filter_by_status_confirmed(self, glossary: GlossaryStore):
        """Filter entries by confirmed status."""
        from terrarium_annotator.cli import filter_entries

        entries = list(filter_entries(glossary.all_entries(), "confirmed", None))

        assert len(entries) == 2
        assert all(e.status == "confirmed" for e in entries)

    def test_filter_by_status_tentative(self, glossary: GlossaryStore):
        """Filter entries by tentative status."""
        from terrarium_annotator.cli import filter_entries

        entries = list(filter_entries(glossary.all_entries(), "tentative", None))

        assert len(entries) == 1
        assert entries[0].term == "Dawn"

    def test_filter_by_status_all(self, glossary: GlossaryStore):
        """Filter with 'all' returns all entries."""
        from terrarium_annotator.cli import filter_entries

        entries = list(filter_entries(glossary.all_entries(), "all", None))

        assert len(entries) == 3

    def test_filter_by_tags(self, glossary: GlossaryStore):
        """Filter entries by tags."""
        from terrarium_annotator.cli import filter_entries

        entries = list(filter_entries(glossary.all_entries(), None, ["character"]))

        assert len(entries) == 2
        terms = {e.term for e in entries}
        assert terms == {"Soma", "Dawn"}

    def test_filter_by_multiple_tags(self, glossary: GlossaryStore):
        """Filter entries requiring ALL specified tags."""
        from terrarium_annotator.cli import filter_entries

        entries = list(filter_entries(glossary.all_entries(), None, ["character", "qm"]))

        assert len(entries) == 1
        assert entries[0].term == "Soma"

    def test_filter_combined(self, glossary: GlossaryStore):
        """Filter by both status and tags."""
        from terrarium_annotator.cli import filter_entries

        entries = list(filter_entries(glossary.all_entries(), "confirmed", ["character"]))

        assert len(entries) == 1
        assert entries[0].term == "Soma"
